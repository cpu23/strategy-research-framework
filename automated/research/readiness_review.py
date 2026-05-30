from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schemas import REPO_ROOT, STRATEGIES_ROOT

READINESS_REVIEW_SCHEMA = "generated_readiness_review_v1"

ALLOWED_MANUAL_ACTIONS = {
    "manual_review_only",
    "defer",
    "revise_environment",
    "revise_generated_artifacts",
}

FORBIDDEN_INTERPRETATIONS = [
    "not_baseline_evidence",
    "not_robustness_evidence",
    "not_final_holdout_evidence",
    "not_candidate_evidence",
    "not_lifecycle_evidence",
    "not_production_evidence",
    "not_live_trading_evidence",
]

FORBIDDEN_ACTION_VALUES = {
    "approve_baseline",
    "approve_final_holdout",
    "request_human_review_for_final_holdout",
    "promote_to_production",
    "production_candidate",
    "live_trading_candidate",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_evidence(path: str | Path, label: str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"{label} evidence not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} evidence is not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise ValueError(f"{label} evidence must be a JSON object")
    return data


def _check_string(data: dict[str, Any], key: str, label: str) -> str | None:
    val = data.get(key)
    if val is not None and not isinstance(val, str):
        raise ValueError(f"{label}.{key} must be a string, got {type(val).__name__}")
    return val


def _validate_compile_evidence(
    evidence: dict[str, Any],
    label: str,
) -> tuple[str | None, dict[str, str]]:
    mode = evidence.get("mode")
    if mode not in ("real_compile",):
        raise ValueError(f"{label}.mode must be 'real_compile', got {mode!r}")
    status = evidence.get("status")
    if status not in ("passed", "failed"):
        raise ValueError(f"{label}.status must be 'passed' or 'failed', got {status!r}")
    input_digests = evidence.get("input_digests", {})
    if not isinstance(input_digests, dict):
        input_digests = {}
    errors = evidence.get("errors", [])
    return status, input_digests


def _validate_readiness_evidence(
    evidence: dict[str, Any],
    label: str,
) -> tuple[str | None, dict[str, str]]:
    mode = evidence.get("mode")
    if mode not in ("real_backtest_readiness",):
        raise ValueError(f"{label}.mode must be 'real_backtest_readiness', got {mode!r}")
    status = evidence.get("status")
    if status not in ("passed", "failed", "timed_out"):
        raise ValueError(f"{label}.status must be 'passed' or 'failed' or 'timed_out', got {status!r}")
    input_digests = evidence.get("input_digests", {})
    if not isinstance(input_digests, dict):
        input_digests = {}
    errors = evidence.get("errors", [])
    return status, input_digests


def _check_consistency(
    compile_evidence: dict[str, Any],
    readiness_evidence: dict[str, Any],
    impl_request_id: str | None,
) -> list[str]:
    warnings: list[str] = []

    for label, ev in [("compile", compile_evidence), ("backtest_readiness", readiness_evidence)]:
        ev_req_id = _check_string(ev, "impl_request_id", label)
        if impl_request_id and ev_req_id and ev_req_id != impl_request_id:
            warnings.append(
                f"{label} evidence impl_request_id {ev_req_id!r} does not match "
                f"CLI argument {impl_request_id!r}"
            )

    comp_impl_id = _check_string(compile_evidence, "implementation_id", "compile")
    bt_impl_id = _check_string(readiness_evidence, "implementation_id", "backtest_readiness")
    if comp_impl_id and bt_impl_id and comp_impl_id != bt_impl_id:
        warnings.append(
            f"implementation_id mismatch: compile={comp_impl_id!r} vs "
            f"backtest_readiness={bt_impl_id!r}"
        )

    comp_sid = _check_string(compile_evidence, "strategy_id", "compile")
    bt_sid = _check_string(readiness_evidence, "strategy_id", "backtest_readiness")
    if comp_sid and bt_sid and comp_sid != bt_sid:
        warnings.append(
            f"strategy_id mismatch: compile={comp_sid!r} vs "
            f"backtest_readiness={bt_sid!r}"
        )

    comp_ver = _check_string(compile_evidence, "version", "compile")
    bt_ver = _check_string(readiness_evidence, "version", "backtest_readiness")
    if comp_ver and bt_ver and comp_ver != bt_ver:
        warnings.append(
            f"version mismatch: compile={comp_ver!r} vs "
            f"backtest_readiness={bt_ver!r}"
        )

    return warnings


def _check_path_safety(
    compile_evidence: dict[str, Any],
    readiness_evidence: dict[str, Any],
) -> dict[str, Any]:
    generated_sandbox_only = True
    production_paths_touched = False

    strategies_str = str(STRATEGIES_ROOT.resolve())

    for label, ev in [("compile", compile_evidence), ("backtest_readiness", readiness_evidence)]:
        generated_path = _check_string(ev, "generated_mq5_path", label)
        if generated_path:
            resolved = str(Path(generated_path).resolve())
            if resolved.startswith(strategies_str + "/") or resolved == strategies_str:
                production_paths_touched = True
                generated_sandbox_only = False

        report_paths = ev.get("report_paths", [])
        if isinstance(report_paths, list):
            for rp in report_paths:
                if isinstance(rp, str):
                    resolved_rp = str(Path(rp).resolve())
                    if resolved_rp.startswith(strategies_str + "/") or resolved_rp == strategies_str:
                        production_paths_touched = True

        log_paths = ev.get("log_paths", [])
        if isinstance(log_paths, list):
            for lp in log_paths:
                if isinstance(lp, str):
                    resolved_lp = str(Path(lp).resolve())
                    if resolved_lp.startswith(strategies_str + "/") or resolved_lp == strategies_str:
                        production_paths_touched = True

    return {
        "generated_sandbox_only": generated_sandbox_only,
        "production_paths_touched": production_paths_touched,
    }


def build_readiness_review_packet(
    *,
    strategy_id: str | None = None,
    version: str | None = None,
    impl_request_id: str | None = None,
    compile_evidence_path: str | Path | None = None,
    compile_evidence: dict[str, Any] | None = None,
    backtest_readiness_evidence_path: str | Path | None = None,
    backtest_readiness_evidence: dict[str, Any] | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    if compile_evidence is None:
        if compile_evidence_path is None:
            raise ValueError("compile_evidence or compile_evidence_path is required")
        compile_evidence = _load_evidence(compile_evidence_path, "compile")
    if backtest_readiness_evidence is None:
        if backtest_readiness_evidence_path is None:
            raise ValueError("backtest_readiness_evidence or backtest_readiness_evidence_path is required")
        backtest_readiness_evidence = _load_evidence(backtest_readiness_evidence_path, "backtest_readiness")

    compile_status, compile_digests = _validate_compile_evidence(compile_evidence, "compile")

    bt_status, bt_digests = _validate_readiness_evidence(backtest_readiness_evidence, "backtest_readiness")

    if impl_request_id is None:
        impl_request_id = _check_string(compile_evidence, "impl_request_id", "compile") or ""
    if strategy_id is None:
        strategy_id = _check_string(compile_evidence, "strategy_id", "compile") or ""
    if version is None:
        version = _check_string(compile_evidence, "version", "compile") or ""

    consistency_warnings = _check_consistency(
        compile_evidence, backtest_readiness_evidence, impl_request_id or None,
    )

    path_safety = _check_path_safety(compile_evidence, backtest_readiness_evidence)

    implementation_id = _check_string(compile_evidence, "implementation_id", "compile") or ""

    packet: dict[str, Any] = {
        "artifact_type": "generated_readiness_review",
        "schema_version": READINESS_REVIEW_SCHEMA,
        "review_packet_id": (
            f"REVIEW_PACKET_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            f"_{uuid.uuid4().hex[:6].upper()}"
        ),
        "generated_at": utc_now(),
        "strategy_id": strategy_id or "",
        "version": version or "",
        "impl_request_id": impl_request_id or "",
        "implementation_id": implementation_id,
        "compile_readiness": {
            "status": compile_status,
            "evidence_path": str(Path(compile_evidence_path).resolve()) if compile_evidence_path else None,
            "input_digests": compile_digests,
        },
        "backtest_readiness": {
            "status": bt_status,
            "evidence_path": str(Path(backtest_readiness_evidence_path).resolve()) if backtest_readiness_evidence_path else None,
            "input_digests": bt_digests,
        },
        "path_safety": path_safety,
        "warnings": consistency_warnings,
        "proposed_next_manual_action": "manual_review_only",
        "forbidden_interpretations": list(FORBIDDEN_INTERPRETATIONS),
    }

    if output_path:
        out = Path(output_path)
        strategies_str = str(STRATEGIES_ROOT.resolve())
        if str(out.resolve()).startswith(strategies_str + "/") or str(out.resolve()) == strategies_str:
            raise ValueError(
                f"Output path must not be under automated/strategies/: {out}"
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(packet, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    return packet
