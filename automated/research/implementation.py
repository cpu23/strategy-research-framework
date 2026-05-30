from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import registry
from .contracts import APPROVAL_SCOPES, IMPLEMENTATION_REQUEST_STATUSES, ensure_member
from .hashing import file_sha256
from .schemas import (
    REPO_ROOT,
    SANDBOX_ROOT,
    FORBIDDEN_SANDBOX_PREFIXES,
    FORBIDDEN_SANDBOX_FILES,
    SchemaValidationError,
    load_yaml,
    validate_implementation_request,
)

IMPL_REQUESTS_DIR = REPO_ROOT / "automated" / "implementation_requests"

_INPUT_PATTERN = re.compile(
    r'^\s*input\s+(int|double|bool|string|datetime|color)\s+(\w+)\s*(?:=\s*(.+?))?\s*;',
    re.MULTILINE,
)

_DANGER_PATTERNS: list[tuple[str, str, str]] = [
    ("import_directive", r'#import', "External DLL import via #import"),
    ("shell_execute", r'ShellExecute[A-Za-z]*\s*\(', "Shell execution detected"),
    ("web_request", r'WebRequest\s*\(', "Network request via WebRequest"),
    ("file_open", r'FileOpen\s*\(', "File I/O via FileOpen"),
    ("file_write", r'FileWrite\s*\(', "File I/O via FileWrite"),
    ("file_delete", r'FileDelete\s*\(', "File deletion via FileDelete"),
    ("global_variable_set", r'GlobalVariableSet\s*\(', "Global variable set via GlobalVariableSet"),
    ("hardcoded_lot", r'(?:double|int)\s+lot\s*=', "Potentially hardcoded lot size"),
]

_ORDER_PATTERNS: list[tuple[str, str, str]] = [
    ("order_send", r'OrderSend\s*\(', "OrderSend without obvious magic/symbol filter"),
    ("trade_buy", r'trade\.\s*Buy\s*\(', "trade.Buy without obvious stop-loss handling"),
    ("trade_sell", r'trade\.\s*Sell\s*\(', "trade.Sell without obvious stop-loss handling"),
    ("position_open", r'trade\.\s*PositionOpen\s*\(', "trade.PositionOpen without obvious stop-loss handling"),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _req_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"IMPL_REQ_{stamp}_{uuid.uuid4().hex[:8].upper()}"


def _impl_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"IMPL_{stamp}_{uuid.uuid4().hex[:8].upper()}"


def sandbox_path(strategy_id: str, version: str) -> Path:
    return SANDBOX_ROOT / strategy_id / version


def assert_sandbox_path(path: Path | str) -> None:
    resolved = Path(path).resolve()
    prefix = str(SANDBOX_ROOT.resolve())
    if not str(resolved).startswith(prefix + "/") and str(resolved) != prefix:
        raise SchemaValidationError(
            f"Path must be under {SANDBOX_ROOT}; got {path}"
        )
    for forbidden_prefix in FORBIDDEN_SANDBOX_PREFIXES:
        if str(resolved).startswith(str(forbidden_prefix.resolve()) + "/") or str(resolved) == str(forbidden_prefix.resolve()):
            raise SchemaValidationError(
                f"Path is in forbidden area {forbidden_prefix}; got {path}"
            )
    if resolved in FORBIDDEN_SANDBOX_FILES:
        raise SchemaValidationError(
            f"Path is a forbidden file; got {path}"
    )


def check_path_safety(paths: list[Path | str]) -> list[str]:
    violations: list[str] = []
    for p in paths:
        resolved = Path(p).resolve()
        try:
            assert_sandbox_path(resolved)
        except SchemaValidationError as exc:
            violations.append(str(exc))
    return violations


def check_overwrite(path: Path | str, force: bool = False) -> str | None:
    target = Path(path)
    if target.exists() and not force:
        return f"File already exists and --force was not specified: {target}"
    return None


def check_no_production_touch(paths: list[Path | str]) -> list[str]:
    violations: list[str] = []
    strategies_root = str((REPO_ROOT / "automated" / "strategies").resolve())
    for p in paths:
        resolved = str(Path(p).resolve())
        if resolved.startswith(strategies_root + "/") or resolved == strategies_root:
            violations.append(f"Generated code must not touch {REPO_ROOT / 'automated' / 'strategies'}: {p}")
    return violations


def parse_mql5_inputs(mq5_path: str | Path) -> list[dict[str, Any]]:
    path = Path(mq5_path)
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    inputs: list[dict[str, Any]] = []
    for match in _INPUT_PATTERN.finditer(text):
        decl_type = match.group(1)
        name = match.group(2)
        raw_default = match.group(3)
        default = raw_default.strip() if raw_default else None
        inputs.append({"name": name, "type": decl_type, "default": default})
    return inputs


def compare_inputs(
    mq5_inputs: list[dict[str, Any]],
    expected_inputs: list[dict[str, Any]] | None,
    allow_extra_inputs: bool = False,
) -> dict[str, Any]:
    mismatches: list[str] = []
    expected_by_name: dict[str, dict[str, Any]] = {}
    if expected_inputs:
        for inp in expected_inputs:
            expected_by_name[inp["name"]] = inp

    mq5_by_name: dict[str, dict[str, Any]] = {}
    for inp in mq5_inputs:
        mq5_by_name[inp["name"]] = inp

    for exp_name, exp in expected_by_name.items():
        if exp.get("required", False):
            if exp_name not in mq5_by_name:
                mismatches.append(f"Required input {exp_name} is missing from generated .mq5")
                continue
            mq5 = mq5_by_name[exp_name]
            exp_type = exp.get("type", "").lower()
            mq5_type = mq5.get("type", "").lower()
            if exp_type and exp_type != mq5_type:
                mismatches.append(
                    f"Type mismatch for {exp_name}: expected {exp_type}, got {mq5_type}"
                )
            if "default" in exp and exp["default"] is not None:
                exp_default = str(exp["default"]).strip()
                mq5_default = str(mq5.get("default", "")).strip() if mq5.get("default") is not None else ""
                if exp_default != mq5_default:
                    mismatches.append(
                        f"Default mismatch for {exp_name}: expected {exp_default!r}, got {mq5_default!r}"
                    )

    if not allow_extra_inputs:
        for mq5_name in mq5_by_name:
            if mq5_name not in expected_by_name:
                mismatches.append(f"Unexpected input {mq5_name} found in generated .mq5 but not in expected_inputs")

    return {"match": len(mismatches) == 0, "mismatches": mismatches}


def scan_dangerous_patterns(mq5_path: str | Path) -> list[dict[str, Any]]:
    path = Path(mq5_path)
    if not path.is_file():
        return [{"id": "file_not_found", "severity": "error", "description": f"File not found: {mq5_path}"}]
    text = path.read_text(encoding="utf-8", errors="replace")
    findings: list[dict[str, Any]] = []

    for pattern_id, pattern, description in _DANGER_PATTERNS:
        if re.search(pattern, text, re.MULTILINE):
            findings.append({
                "id": pattern_id,
                "severity": "warning",
                "description": description,
                "pattern": pattern_id,
            })

    has_magic_filter = bool(re.search(r'magic|InpMagic|MAGIC', text, re.IGNORECASE))
    has_symbol_filter = bool(re.search(r'symbol|InpSymbol|SYMBOL', text, re.IGNORECASE))
    has_sl_check = bool(re.search(r'stop.?los[s]?|sl\s*[=>(]|StopLoss|InpStopLoss|SL_', text, re.IGNORECASE))
    has_sl_param = bool(re.search(r'Inp.*[Ss][Ll]|Inp.*Stop', text))

    for pattern_id, pattern, description in _ORDER_PATTERNS:
        if re.search(pattern, text, re.MULTILINE):
            severity = "warning"
            if not has_magic_filter and not has_symbol_filter:
                description += " (no magic/symbol filter detected nearby)"
            if pattern_id in ("trade_buy", "trade_sell") and not has_sl_check and not has_sl_param:
                description += " (no obvious stop-loss handling detected)"
            findings.append({
                "id": pattern_id,
                "severity": severity,
                "description": description,
                "pattern": pattern_id,
            })

    if re.search(r'PositionSelect\s*\([^)]*\)', text) and re.search(r'while|for', text):
        findings.append({
            "id": "account_wide_position_loop",
            "severity": "warning",
            "description": "Account-wide position loop detected via PositionSelect in a loop",
            "pattern": "account_wide_position_loop",
        })

    return findings


def _check_risk_controls(text: str) -> list[str]:
    detected: list[str] = []
    risk_patterns = [
        (r'InpRisk\w*', "Risk input parameter"),
        (r'CalculatePositionSize', "CalculatePositionSize function"),
        (r'InpRiskPerTrade|InpRiskPercent|RiskPerTrade', "Per-trade risk parameter"),
        (r'm_risk|my_risk|risk_manager', "Risk manager variable"),
    ]
    for pattern, label in risk_patterns:
        if re.search(pattern, text):
            detected.append(label)
    return detected


def _check_order_placement(text: str) -> list[str]:
    detected: list[str] = []
    order_patterns = [
        (r'OrderSend\s*\(', "OrderSend"),
        (r'trade\.\s*(Buy|Sell|PositionOpen)\s*\(', "trade.PositionOpen/Buy/Sell"),
    ]
    for pattern, label in order_patterns:
        if re.search(pattern, text):
            detected.append(label)
    return detected


def build_diff_review(
    *,
    impl_request_id: str,
    db_path: str | Path,
) -> dict[str, Any]:
    request = registry.get_implementation_request(db_path, impl_request_id)
    if not request:
        raise ValueError(f"implementation request not found: {impl_request_id}")

    implementations = registry.list_implementations(db_path, impl_request_id)
    current_impl = implementations[-1] if implementations else None

    sandbox = Path(request["sandbox_dir"])
    generated_mq5_path: Path | None = None
    generated_files_list: list[str] = []
    forbidden_touches: list[str] = []

    if sandbox.is_dir():
        for f in sandbox.rglob("*"):
            if f.is_file() and f.suffix.lower() == ".mq5":
                generated_files_list.append(str(f))
                if current_impl:
                    generated_mq5_path = Path(current_impl["generated_mq5_path"])

    if current_impl and current_impl.get("generated_mq5_path"):
        generated_mq5_path = Path(current_impl["generated_mq5_path"])

    if generated_mq5_path and generated_mq5_path.is_file():
        generated_files_list.append(str(generated_mq5_path))
        forbidden_touches = check_path_safety([generated_mq5_path])

    mq5_inputs: list[dict[str, Any]] = []
    if generated_mq5_path and generated_mq5_path.is_file():
        mq5_inputs = parse_mql5_inputs(generated_mq5_path)

    request_artifact_path = Path(request["request_artifact_path"])
    request_data: dict[str, Any] = {}
    if request_artifact_path.is_file():
        request_data = load_yaml(request_artifact_path)

    expected_inputs = request_data.get("expected_inputs") or []
    if not isinstance(expected_inputs, list):
        expected_inputs = []

    input_comparison = compare_inputs(mq5_inputs, expected_inputs)

    danger_findings: list[dict[str, Any]] = []
    if generated_mq5_path and generated_mq5_path.is_file():
        danger_findings = scan_dangerous_patterns(generated_mq5_path)
        mq5_text = generated_mq5_path.read_text(encoding="utf-8", errors="replace")
        risk_controls = _check_risk_controls(mq5_text)
        order_logic = _check_order_placement(mq5_text)
    else:
        risk_controls = []
        order_logic = []

    hard_blockers: list[str] = []
    if forbidden_touches:
        hard_blockers.extend(forbidden_touches)
    if not generated_mq5_path or not generated_mq5_path.is_file():
        hard_blockers.append("Generated .mq5 file is missing")
    if not mq5_inputs and (expected_inputs and any(e.get("required") for e in expected_inputs)):
        hard_blockers.append("Could not parse any inputs from generated .mq5")
    if not input_comparison["match"]:
        hard_blockers.extend(input_comparison["mismatches"])

    compile_status = current_impl.get("compile_status") if current_impl else None
    baseline_eligible = (
        compile_status in ("passed", "mock_checked")
        and not hard_blockers
        and len(forbidden_touches) == 0
    )

    review: dict[str, Any] = {
        "schema_version": "diff_review_v1",
        "implementation_request_id": impl_request_id,
        "strategy_id": request["strategy_id"],
        "strategy_version": request["strategy_version"],
        "reviewed_at": utc_now(),
        "files_created": generated_files_list,
        "files_modified": [],
        "forbidden_files_touched": forbidden_touches,
        "declared_mql5_inputs": mq5_inputs,
        "expected_spec_inputs": expected_inputs,
        "input_mismatches": input_comparison["mismatches"],
        "risk_controls_detected": risk_controls,
        "order_placement_logic_detected": order_logic,
        "dangerous_patterns": danger_findings,
        "hard_blockers": hard_blockers,
        "compile_status": compile_status,
        "baseline_eligible": baseline_eligible,
        "human_review_required": len(danger_findings) > 0 or len(hard_blockers) > 0,
    }
    return review


def write_diff_review_artifact(review: dict[str, Any], impl_request_id: str) -> Path:
    artifact_dir = IMPL_REQUESTS_DIR / impl_request_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / "diff_review.yaml"
    path.write_text(yaml.safe_dump(review, sort_keys=False), encoding="utf-8")
    return path


def write_request_artifact(data: dict[str, Any], impl_request_id: str) -> Path:
    artifact_dir = IMPL_REQUESTS_DIR / impl_request_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / "request.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def create_implementation_request(
    db_path: str | Path,
    *,
    strategy_id: str,
    strategy_version: str,
    sandbox_dir: str | Path,
    generated_files: list[str],
    created_by: str = "human",
    hypothesis_id: str | None = None,
    strategy_spec_path: str | None = None,
    expected_inputs: list[dict[str, Any]] | None = None,
    parameters: dict[str, Any] | None = None,
    entry_logic: str | None = None,
    exit_logic: str | None = None,
    risk_logic: str | None = None,
    compile_command: str | None = None,
    test_plan: str | None = None,
) -> dict[str, Any]:
    impl_req_id = _req_id()
    now = utc_now()

    sandbox = Path(sandbox_dir)
    assert_sandbox_path(sandbox)

    data: dict[str, Any] = {
        "implementation_request_id": impl_req_id,
        "hypothesis_id": hypothesis_id,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "strategy_spec_path": strategy_spec_path,
        "sandbox_dir": str(sandbox),
        "allowed_files": ["*.mq5", "*.set"],
        "forbidden_files": [
            "automated/strategies/**",
            "automated/research/**",
            "automated/scripts/**",
            "automated/specs/**",
            "automated/runs/**",
            "automated/reports/**",
            "tests/**",
            "hypotheses/**",
        ],
        "generated_files": generated_files,
        "entry_logic": entry_logic,
        "exit_logic": exit_logic,
        "risk_logic": risk_logic,
        "parameters": parameters or {},
        "expected_inputs": expected_inputs or [],
        "compile_command": compile_command or "mql5",
        "test_plan": test_plan or "",
        "created_by": created_by,
        "created_at": now,
        "status": "proposed",
    }

    validate_implementation_request(data)
    artifact_path = write_request_artifact(data, impl_req_id)

    sandbox.mkdir(parents=True, exist_ok=True)

    registry.insert_implementation_request(db_path, {
        "implementation_request_id": impl_req_id,
        "hypothesis_id": hypothesis_id,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "strategy_spec_path": strategy_spec_path,
        "request_artifact_path": str(artifact_path),
        "sandbox_dir": str(sandbox),
        "status": "proposed",
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    })

    return {"implementation_request_id": impl_req_id, "artifact_path": str(artifact_path), "sandbox_dir": str(sandbox), "status": "proposed"}


def validate_request(db_path: str | Path, impl_request_id: str) -> dict[str, Any]:
    request = registry.get_implementation_request(db_path, impl_request_id)
    if not request:
        raise ValueError(f"implementation request not found: {impl_request_id}")

    errors: list[str] = []
    artifact_path = Path(request["request_artifact_path"])
    if not artifact_path.is_file():
        errors.append(f"Request artifact file not found: {artifact_path}")
    else:
        data = load_yaml(artifact_path)
        try:
            validate_implementation_request(data)
        except SchemaValidationError as exc:
            errors.append(str(exc))

    sandbox = Path(request["sandbox_dir"])
    try:
        assert_sandbox_path(sandbox)
    except SchemaValidationError as exc:
        errors.append(str(exc))

    spec_path = request.get("strategy_spec_path")
    if spec_path:
        spec_full = Path(spec_path)
        if not spec_full.is_absolute():
            spec_full = REPO_ROOT / spec_full
        if not spec_full.is_file():
            errors.append(f"Referenced strategy spec does not exist: {spec_path}")

    status = request.get("status", "proposed")
    if status not in IMPLEMENTATION_REQUEST_STATUSES:
        errors.append(f"Invalid status: {status}")

    valid = len(errors) == 0
    if valid and status == "proposed":
        registry.update_implementation_request(db_path, impl_request_id, status="validated")

    return {"implementation_request_id": impl_request_id, "valid": valid, "errors": errors, "status": status}


def compile_check(
    db_path: str | Path,
    impl_request_id: str,
    *,
    mock: bool = False,
    real_compile_config: Any = None,
    compile_config_path: str | Path | None = None,
) -> dict[str, Any]:
    request = registry.get_implementation_request(db_path, impl_request_id)
    if not request:
        raise ValueError(f"implementation request not found: {impl_request_id}")

    sandbox = Path(request["sandbox_dir"])
    try:
        assert_sandbox_path(sandbox)
    except SchemaValidationError as exc:
        return {"implementation_request_id": impl_request_id, "status": "failed", "errors": [str(exc)]}

    mq5_files = list(sandbox.rglob("*.mq5"))
    if not mq5_files:
        return {"implementation_request_id": impl_request_id, "status": "failed", "errors": ["No .mq5 files found in sandbox"]}

    generated_mq5 = mq5_files[0]
    forbidden = check_no_production_touch([generated_mq5])
    if forbidden:
        return {"implementation_request_id": impl_request_id, "status": "failed", "errors": forbidden}

    code_hash = file_sha256(generated_mq5) if generated_mq5.is_file() else None
    input_digests: dict[str, str] = {}

    if mock:
        compile_status = "mock_checked"
        compile_errors: list[str] = []
    elif real_compile_config is not None:
        from .compiler import run_real_compile
        compile_result = run_real_compile(
            generated_mq5, real_compile_config,
            config_path=compile_config_path,
        )
        compile_status = compile_result["status"]
        compile_errors = compile_result.get("errors", [])
        input_digests = compile_result.get("input_digests", {})
    else:
        compile_status = "mock_checked"
        compile_errors = ["Real MT5 compile not available; pass --real-compile-config for real compile"]

    impl_id = _impl_id()
    now = utc_now()
    registry.insert_implementation(db_path, {
        "implementation_id": impl_id,
        "implementation_request_id": impl_request_id,
        "strategy_id": request["strategy_id"],
        "strategy_version": request["strategy_version"],
        "generated_mq5_path": str(generated_mq5),
        "code_sha256": code_hash,
        "compile_status": compile_status,
        "diff_review_status": None,
        "input_match_status": None,
        "approved_for_baseline": 0,
        "approved_by": None,
        "approved_at": None,
        "baseline_experiment_id": None,
        "created_at": now,
    })

    if compile_status in ("mock_checked", "passed"):
        registry.update_implementation_request(db_path, impl_request_id, status="compiled")

    return {
        "implementation_request_id": impl_request_id,
        "implementation_id": impl_id,
        "generated_mq5_path": str(generated_mq5),
        "code_sha256": code_hash,
        "compile_status": compile_status,
        "errors": compile_errors,
        "input_digests": input_digests,
    }


def run_diff_review(
    db_path: str | Path,
    impl_request_id: str,
) -> dict[str, Any]:
    request = registry.get_implementation_request(db_path, impl_request_id)
    if not request:
        raise ValueError(f"implementation request not found: {impl_request_id}")

    implementations = registry.list_implementations(db_path, impl_request_id)
    if not implementations:
        raise ValueError(f"No implementations found for request {impl_request_id}; run compile-check first")

    current_impl = implementations[-1]
    review = build_diff_review(impl_request_id=impl_request_id, db_path=db_path)
    artifact_path = write_diff_review_artifact(review, impl_request_id)

    registry.update_implementation(db_path, current_impl["implementation_id"], diff_review_status="reviewed")
    input_match_status = "match" if review["baseline_eligible"] and not review["input_mismatches"] else "mismatch"
    registry.update_implementation(db_path, current_impl["implementation_id"], input_match_status=input_match_status)

    registry.update_implementation_request(db_path, impl_request_id, status="reviewed")

    return {
        "implementation_request_id": impl_request_id,
        "implementation_id": current_impl["implementation_id"],
        "artifact_path": str(artifact_path),
        "baseline_eligible": review["baseline_eligible"],
        "hard_blockers": review["hard_blockers"],
        "dangerous_patterns": review["dangerous_patterns"],
        "input_mismatches": review["input_mismatches"],
    }


def approve_for_baseline(
    db_path: str | Path,
    impl_request_id: str,
    *,
    approved_by: str,
    baseline_experiment_id: str | None = None,
    require_real_compile: bool = True,
    approval_scope: str = "baseline_only",
    allow_reuse: bool = False,
) -> dict[str, Any]:
    request = registry.get_implementation_request(db_path, impl_request_id)
    if not request:
        raise ValueError(f"implementation request not found: {impl_request_id}")

    implementations = registry.list_implementations(db_path, impl_request_id)
    if not implementations:
        raise ValueError(f"No implementations found for request {impl_request_id}; run compile-check first")

    ensure_member(approval_scope, APPROVAL_SCOPES, "approval_scope")

    current_impl = implementations[-1]
    errors: list[str] = []

    compile_status = current_impl.get("compile_status")
    if not compile_status:
        errors.append("Compile status is missing; run compile-check first")
    elif require_real_compile and compile_status != "passed":
        if compile_status == "mock_checked":
            errors.append("Compile was mock-checked; real compile 'passed' required for baseline approval")
        else:
            errors.append(f"Compile status is {compile_status}; 'passed' required for baseline approval")

    sandbox = Path(request["sandbox_dir"])
    try:
        assert_sandbox_path(sandbox)
    except SchemaValidationError as exc:
        errors.append(str(exc))

    forbidden = check_no_production_touch([Path(current_impl["generated_mq5_path"])])
    if forbidden:
        errors.extend(forbidden)

    generated_mq5 = Path(current_impl["generated_mq5_path"])
    if not generated_mq5.is_file():
        errors.append(f"Generated .mq5 file not found: {generated_mq5}")

    mq5_inputs = parse_mql5_inputs(generated_mq5) if generated_mq5.is_file() else []

    artifact_path = Path(request["request_artifact_path"])
    request_data: dict[str, Any] = {}
    if artifact_path.is_file():
        request_data = load_yaml(artifact_path)
    expected_inputs = request_data.get("expected_inputs") or []
    if not isinstance(expected_inputs, list):
        expected_inputs = []

    input_comparison = compare_inputs(mq5_inputs, expected_inputs, allow_extra_inputs=True)
    if not input_comparison["match"]:
        errors.extend(input_comparison["mismatches"])

    if errors:
        return {
            "implementation_request_id": impl_request_id,
            "approved": False,
            "errors": errors,
        }

    now = utc_now()
    registry.update_implementation(
        db_path,
        current_impl["implementation_id"],
        approved_for_baseline=1,
        approved_by=approved_by,
        approved_at=now,
        baseline_experiment_id=baseline_experiment_id,
        approval_scope=approval_scope,
        allow_reuse=1 if allow_reuse else 0,
    )
    registry.update_implementation_request(db_path, impl_request_id, status="approved_for_baseline")

    return {
        "implementation_request_id": impl_request_id,
        "implementation_id": current_impl["implementation_id"],
        "approved": True,
        "approved_by": approved_by,
        "approved_at": now,
        "baseline_only": True,
        "approval_scope": approval_scope,
        "allow_reuse": allow_reuse,
        "baseline_experiment_id": baseline_experiment_id,
        "note": "Approval is for bounded baseline research only. Code has not been promoted to automated/strategies/.",
    }


def inspect(db_path: str | Path, impl_request_id: str) -> dict[str, Any]:
    request = registry.get_implementation_request(db_path, impl_request_id)
    if not request:
        raise ValueError(f"implementation request not found: {impl_request_id}")

    implementations = registry.list_implementations(db_path, impl_request_id)
    current_impl = implementations[-1] if implementations else None

    info: dict[str, Any] = {
        "implementation_request_id": request["implementation_request_id"],
        "strategy_id": request["strategy_id"],
        "strategy_version": request["strategy_version"],
        "status": request["status"],
        "sandbox_dir": request["sandbox_dir"],
        "request_artifact_path": request["request_artifact_path"],
        "created_by": request["created_by"],
        "created_at": request["created_at"],
        "updated_at": request["updated_at"],
        "implementation": None,
    }

    if current_impl:
        info["implementation"] = {
            "implementation_id": current_impl["implementation_id"],
            "generated_mq5_path": current_impl["generated_mq5_path"],
            "code_sha256": current_impl.get("code_sha256"),
            "compile_status": current_impl.get("compile_status"),
            "diff_review_status": current_impl.get("diff_review_status"),
            "input_match_status": current_impl.get("input_match_status"),
            "approved_for_baseline": bool(current_impl.get("approved_for_baseline")),
            "approved_by": current_impl.get("approved_by"),
            "approved_at": current_impl.get("approved_at"),
            "baseline_experiment_id": current_impl.get("baseline_experiment_id"),
        }

    info["sandbox_exists"] = Path(request["sandbox_dir"]).is_dir()
    mq5_files = list(Path(request["sandbox_dir"]).rglob("*.mq5")) if info["sandbox_exists"] else []
    info["generated_mq5_files"] = [str(f) for f in mq5_files]

    return info


def require_generated_baseline_approval(
    db_path: str | Path,
    strategy_id: str,
    strategy_version: str,
    *,
    allow_mock_compile: bool = False,
    check_scope: bool = False,
    check_consumed: bool = False,
) -> dict[str, Any]:
    """Check whether a generated implementation is approved for baseline use.

    If the strategy has no implementation request at all, it is a normal
    (non-generated) strategy and the guard passes without action.

    If an implementation request exists, all of the following are required:
      - Generated .mq5 path is under the sandbox
      - Compile status is 'passed' (or 'mock_checked' when allow_mock_compile=True)
      - diff_review artifact exists on disk
      - input_match_status is 'match'
      - approved_for_baseline = 1
      - approval_scope is 'baseline_only' (when check_scope=True)
      - approval not already consumed (when check_consumed=True and allow_reuse=0)
    """
    request = registry.find_implementation_request(db_path, strategy_id, strategy_version)

    if not request:
        return {"approved": True, "note": "No implementation request found; not a generated strategy"}

    errors: list[str] = []

    sandbox_dir = Path(request["sandbox_dir"])
    try:
        assert_sandbox_path(sandbox_dir)
    except SchemaValidationError as exc:
        errors.append(str(exc))

    implementations = registry.list_implementations(db_path, request["implementation_request_id"])
    if not implementations:
        errors.append("No implementation record found; compile-check must be run first")
        return {
            "approved": False,
            "errors": errors,
            "implementation_request_id": request["implementation_request_id"],
        }

    current_impl = implementations[-1]

    compile_status = current_impl.get("compile_status")
    if not compile_status:
        errors.append("Compile status is missing; run compile-check first")
    elif compile_status == "failed":
        errors.append("Compile status is 'failed'; cannot use for baseline")
    elif compile_status == "mock_checked" and not allow_mock_compile:
        errors.append("Compile was mock-checked; real compile 'passed' required unless explicitly allowed")

    generated_mq5 = current_impl.get("generated_mq5_path")
    if generated_mq5:
        try:
            assert_sandbox_path(Path(generated_mq5))
        except SchemaValidationError as exc:
            errors.append(str(exc))

    review_path = IMPL_REQUESTS_DIR / request["implementation_request_id"] / "diff_review.yaml"
    if not review_path.is_file():
        errors.append("Diff review artifact not found; run diff-review first")

    input_match = current_impl.get("input_match_status")
    if not input_match:
        errors.append("Input match status is not set; run diff-review first")
    elif input_match == "mismatch":
        errors.append("Input match status is 'mismatch'; cannot use for baseline")

    if not current_impl.get("approved_for_baseline"):
        errors.append("Not approved for baseline; run approve-for-baseline first")

    if check_scope:
        approval_scope = current_impl.get("approval_scope", "baseline_only")
        if approval_scope != "baseline_only":
            errors.append(f"Approval scope is '{approval_scope}'; 'baseline_only' required for generated baseline")

    if check_consumed:
        usage_count = registry.count_approval_usage_for_implementation(
            db_path, current_impl["implementation_id"]
        )
        allow_reuse = bool(current_impl.get("allow_reuse"))
        if usage_count > 0 and not allow_reuse:
            errors.append(
                f"Approval already consumed ({usage_count} usage(s)). "
                "Reuse requires explicit allow_reuse flag on approval."
            )

    if errors:
        return {
            "approved": False,
            "errors": errors,
            "implementation_request_id": request["implementation_request_id"],
            "implementation_id": current_impl["implementation_id"],
        }

    return {
        "approved": True,
        "implementation_request_id": request["implementation_request_id"],
        "implementation_id": current_impl["implementation_id"],
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "note": "Generated implementation approved for baseline use",
    }
