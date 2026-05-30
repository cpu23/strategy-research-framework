from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import compiler, implementation as impl_mod, registry
from . import backtest_readiness
from . import readiness_review
from .schemas import REPO_ROOT, STRATEGIES_ROOT

FORBIDDEN_INTERPRETATIONS = [
    "not_baseline_evidence",
    "not_robustness_evidence",
    "not_final_holdout_evidence",
    "not_candidate_evidence",
    "not_lifecycle_evidence",
    "not_production_evidence",
    "not_live_trading_evidence",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check_out_dir(out_dir: Path) -> None:
    out_resolved = out_dir.resolve()
    strategies_str = str(STRATEGIES_ROOT.resolve())
    if str(out_resolved).startswith(strategies_str + "/") or str(out_resolved) == strategies_str:
        raise ValueError(
            f"Output directory must not be under automated/strategies/: {out_dir}"
        )


def _normalize_compile_evidence(
    compile_result: dict[str, Any],
    request: dict[str, Any] | None,
) -> dict[str, Any]:
    warnings: list[str] = []
    normalized: dict[str, Any] = {
        "mode": "real_compile",
        "status": compile_result.get("compile_status", "failed"),
        "impl_request_id": compile_result.get("implementation_request_id"),
        "implementation_id": compile_result.get("implementation_id"),
        "generated_mq5_path": compile_result.get("generated_mq5_path"),
        "code_sha256": compile_result.get("code_sha256"),
        "input_digests": compile_result.get("input_digests", {}),
        "errors": compile_result.get("errors", []),
    }

    if request:
        normalized["strategy_id"] = request.get("strategy_id") or ""
        normalized["version"] = request.get("strategy_version") or ""
    else:
        normalized["strategy_id"] = ""
        normalized["version"] = ""

    for key in ("exit_code", "stdout", "stderr", "started_at", "finished_at", "duration_seconds"):
        if key in compile_result:
            normalized[key] = compile_result[key]

    has_raw = any(k in compile_result for k in ("exit_code", "stdout", "stderr", "started_at"))
    if not has_raw:
        warnings.append("compile_check_output_lacked_raw_subprocess_fields")

    if warnings:
        normalized["_normalization_warnings"] = warnings

    return normalized


def _build_summary(
    *,
    impl_request_id: str,
    implementation_id: str | None = None,
    strategy_id: str | None = None,
    version: str | None = None,
    compile_status: str | None,
    backtest_readiness_status: str | None = None,
    readiness_review_packet_path: str | None = None,
    output_paths: dict[str, str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    status = "failed"
    if (
        compile_status == "passed"
        and backtest_readiness_status == "passed"
        and readiness_review_packet_path is not None
    ):
        status = "passed"

    summary: dict[str, Any] = {
        "artifact_type": "real_toolchain_rehearsal_summary",
        "status": status,
        "impl_request_id": impl_request_id,
        "implementation_id": implementation_id or "",
        "strategy_id": strategy_id or "",
        "version": version or "",
        "compile_status": compile_status or "",
        "backtest_readiness_status": backtest_readiness_status or "",
        "readiness_review_packet_path": readiness_review_packet_path or "",
        "output_paths": output_paths or {},
        "warnings": warnings or [],
        "forbidden_interpretations": list(FORBIDDEN_INTERPRETATIONS),
    }
    return summary


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_toolchain_rehearsal(
    db_path: str | Path,
    impl_request_id: str,
    *,
    compile_config_path: str | Path,
    backtest_readiness_config_path: str | Path,
    out_dir: str | Path,
    runner_script: str | Path | None = None,
) -> dict[str, Any]:
    out_path = Path(out_dir).resolve()
    _check_out_dir(out_path)

    compile_evidence_path = out_path / "compile_evidence.json"
    bt_evidence_path = out_path / "backtest_readiness_evidence.json"
    packet_path = out_path / "readiness_review_packet.json"
    summary_path = out_path / "real_toolchain_rehearsal_summary.json"

    request = registry.get_implementation_request(db_path, impl_request_id)

    compile_config = compiler.load_real_compile_config(compile_config_path)
    compile_result = impl_mod.compile_check(
        db_path, impl_request_id,
        mock=False,
        real_compile_config=compile_config,
        compile_config_path=str(compile_config_path),
    )

    compile_evidence = _normalize_compile_evidence(compile_result, request)
    _write_json(compile_evidence_path, compile_evidence)

    if compile_result.get("compile_status") != "passed":
        summary = _build_summary(
            impl_request_id=impl_request_id,
            implementation_id=compile_result.get("implementation_id"),
            strategy_id=request.get("strategy_id") if request else None,
            version=request.get("strategy_version") if request else None,
            compile_status=compile_result.get("compile_status", "failed"),
            backtest_readiness_status=None,
            output_paths={"compile_evidence": str(compile_evidence_path.resolve())},
            warnings=["Compile verification failed; backtest readiness and review packet were not produced"],
        )
        _write_json(summary_path, summary)
        return summary

    bt_config = backtest_readiness.load_real_backtest_readiness_config(
        backtest_readiness_config_path
    )
    bt_result = backtest_readiness.run_real_backtest_readiness(
        db_path,
        impl_request_id,
        bt_config,
        runner_script=runner_script,
        readiness_config_path=str(backtest_readiness_config_path),
    )
    _write_json(bt_evidence_path, bt_result)

    bt_status = bt_result.get("status", "failed")
    warnings: list[str] = []

    if bt_status != "passed":
        warnings.append(
            f"Backtest readiness status is {bt_status!r}; "
            "review packet will reflect this failure"
        )

    try:
        packet = readiness_review.build_readiness_review_packet(
            compile_evidence=compile_evidence,
            backtest_readiness_evidence=bt_result,
            impl_request_id=impl_request_id,
            strategy_id=compile_evidence.get("strategy_id"),
            version=compile_evidence.get("version"),
        )
    except (ValueError, FileNotFoundError) as exc:
        packet = None
        warnings.append(f"Readiness review packet creation failed: {exc}")

    if packet is not None:
        _write_json(packet_path, packet)

    summary = _build_summary(
        impl_request_id=impl_request_id,
        implementation_id=compile_result.get("implementation_id"),
        strategy_id=request.get("strategy_id") if request else None,
        version=request.get("strategy_version") if request else None,
        compile_status="passed",
        backtest_readiness_status=bt_status,
        readiness_review_packet_path=str(packet_path.resolve()) if packet is not None else None,
        output_paths={
            "compile_evidence": str(compile_evidence_path.resolve()),
            "backtest_readiness_evidence": str(bt_evidence_path.resolve()),
            "readiness_review_packet": str(packet_path.resolve()) if packet is not None else "",
            "summary": str(summary_path.resolve()),
        },
        warnings=warnings,
    )
    _write_json(summary_path, summary)
    return summary
