from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import registry
from .schemas import REPO_ROOT, load_yaml

GENERATED_BASELINE_REVIEW_SCHEMA = "generated_baseline_review_v1"

ALLOWED_RECOMMENDATIONS = {
    "reject",
    "revise_strategy_spec",
    "revise_implementation",
    "run_robustness_sweep_next",
    "defer",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def red_team_check_generated_baseline(
    metrics: dict[str, Any] | None,
    validation_report: dict[str, Any] | None,
    diff_review_warnings: list[str] | None,
    compile_status: str | None,
    universe: list[str] | None,
    timeframes: list[str] | None,
    min_trades_required: int = 10,
) -> dict[str, Any]:
    warnings: list[str] = []
    risk_flags: list[str] = []

    trade_count = (metrics or {}).get("trade_count") if metrics else None
    profit_factor = (metrics or {}).get("profit_factor") if metrics else None
    net_return = (metrics or {}).get("net_return") if metrics else None
    max_drawdown = (metrics or {}).get("max_drawdown") if metrics else None

    if trade_count is not None and trade_count < min_trades_required:
        warnings.append(f"Suspiciously low trade count: {trade_count} (min {min_trades_required})")
        risk_flags.append("low_trade_count")

    if profit_factor is not None and profit_factor < 1.0:
        warnings.append(f"Profit factor {profit_factor:.2f} is below breakeven")
        risk_flags.append("profit_factor_below_breakeven")
    elif profit_factor is not None and profit_factor < 1.5:
        warnings.append(f"Profit factor {profit_factor:.2f} is marginal; may be noise-driven")
        risk_flags.append("profit_factor_marginal")

    if net_return is not None and net_return < 0:
        warnings.append(f"Net return is negative: {net_return:.4f}")
        risk_flags.append("negative_net_return")

    if max_drawdown is not None and max_drawdown > 30:
        warnings.append(f"High max drawdown: {max_drawdown:.1f}%")
        risk_flags.append("high_drawdown")

    if validation_report:
        cost_gate = (
            validation_report.get("sections", {})
            .get("cost_assumption_gate", {})
            if isinstance(validation_report.get("sections"), dict)
            else {}
        )
        cost_ok = (
            validation_report.get("cost_assumptions_documented")
            or validation_report.get("costs", {}).get("assumptions_documented")
            or cost_gate.get("status") == "pass"
        )
        if not cost_ok:
            warnings.append("Cost/slippage assumptions not documented or not detectable")
            risk_flags.append("missing_cost_assumptions")

    if universe and len(universe) <= 1:
        warnings.append(f"Single-symbol result ({universe[0] if universe else 'unknown'}) may not generalize")
        risk_flags.append("single_symbol")

    if timeframes and len(timeframes) <= 1:
        tf = timeframes[0] if timeframes else "unknown"
        warnings.append(f"Single-timeframe result ({tf}) may not generalize")
        risk_flags.append("single_timeframe")

    if compile_status == "mock_checked":
        warnings.append("Result depends on mock compile; not verified on real MT5")
        risk_flags.append("mock_compile")

    if diff_review_warnings:
        for w in diff_review_warnings:
            warnings.append(f"Diff review warning: {w}")
        risk_flags.append("diff_review_warnings")

    if not risk_flags:
        overall = "low_risk"
    elif len(risk_flags) <= 2:
        overall = "medium_risk"
    else:
        overall = "high_risk"

    return {
        "warnings": warnings,
        "risk_flags": risk_flags,
        "overall_assessment": overall,
    }


def build_generated_baseline_review_packet(
    db_path: str | Path,
    *,
    experiment_id: str,
    strategy_id: str,
    strategy_version: str,
    implementation_request_id: str | None,
    implementation_id: str | None,
    approval_status: str,
    approval_usage: dict[str, Any] | None,
    runner_mode: str,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    experiment = registry.get_experiment(db_path, experiment_id)
    if not experiment:
        raise ValueError(f"experiment not found: {experiment_id}")

    metrics = registry.get_experiment_metrics(db_path, experiment_id)
    headline_metrics: dict[str, Any] = {}
    metrics_raw = experiment.get("headline_metrics_json") or "{}"
    try:
        headline_metrics = json.loads(metrics_raw) if isinstance(metrics_raw, str) else {}
    except (json.JSONDecodeError, TypeError):
        headline_metrics = {}

    validation_report = None
    vrp = experiment.get("validation_report_path")
    if vrp:
        vrp_path = Path(vrp)
        if not vrp_path.is_absolute():
            vrp_path = REPO_ROOT / vrp_path
        if vrp_path.is_file():
            try:
                validation_report = json.loads(vrp_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, FileNotFoundError):
                pass

    diff_review_warnings: list[str] = []
    artifact_rows = registry.list_artifacts(db_path, experiment_id)
    for art in artifact_rows:
        if art.get("artifact_type") in ("diff_review",):
            dr_path = Path(art["path"])
            if not dr_path.is_absolute():
                dr_path = REPO_ROOT / dr_path
            if dr_path.is_file():
                try:
                    dr_data = yaml.safe_load(dr_path.read_text(encoding="utf-8"))
                    if isinstance(dr_data, dict):
                        for dp in dr_data.get("dangerous_patterns", []):
                            sev = dp.get("severity", "unknown")
                            desc = dp.get("description", dp.get("id", "unknown"))
                            diff_review_warnings.append(f"[{sev}] {desc}")
                except Exception:
                    pass

    red_team = red_team_check_generated_baseline(
        metrics=dict(metrics) if metrics else None,
        validation_report=validation_report,
        diff_review_warnings=diff_review_warnings,
        compile_status=experiment.get("implementation_mode"),
        universe=[experiment.get("universe_json", "{}")],
        timeframes=[experiment.get("timeframe", "")],
        min_trades_required=experiment.get("min_trades_required", 10),
    )

    hard_gates: dict[str, Any] = {}
    if validation_report:
        hard_gates = {
            "hard_failures": validation_report.get("hard_failures", []),
            "warnings": validation_report.get("warnings", []),
            "gate_status": validation_report.get("gate_status", "not_available"),
        }

    artifact_paths = [str(art["path"]) for art in artifact_rows]

    recommendation = _compute_recommendation(
        experiment=experiment,
        metrics=dict(metrics) if metrics else None,
        validation_report=validation_report,
        red_team=red_team,
    )

    packet: dict[str, Any] = {
        "schema_version": GENERATED_BASELINE_REVIEW_SCHEMA,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "implementation_request_id": implementation_request_id,
        "implementation_id": implementation_id,
        "approval_status": approval_status,
        "approval_usage": approval_usage,
        "experiment_id": experiment_id,
        "runner_mode": runner_mode,
        "baseline_metrics": headline_metrics,
        "validation_gate_status": experiment.get("gate_status", "incomplete"),
        "hard_gate_results": hard_gates,
        "warnings": [],
        "diff_review_warnings": diff_review_warnings,
        "red_team_results": red_team,
        "artifact_paths": artifact_paths,
        "recommendation": recommendation,
    }

    out_dir = Path(output_dir) if output_dir else REPO_ROOT / "automated" / "research_runs" / experiment_id / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    packet_path = out_dir / "generated_baseline_review.yaml"
    packet_path.write_text(yaml.safe_dump(packet, sort_keys=False), encoding="utf-8")

    registry.attach_artifact(db_path, experiment_id, "generated_baseline_review", packet_path)

    return {
        "packet_path": str(packet_path),
        "packet": packet,
    }


def _compute_recommendation(
    experiment: dict[str, Any],
    metrics: dict[str, Any] | None,
    validation_report: dict[str, Any] | None,
    red_team: dict[str, Any],
) -> str:
    status = experiment.get("status", "incomplete")

    if status == "failed":
        return "reject"

    if validation_report:
        hard_failures = validation_report.get("hard_failures", [])
        if hard_failures:
            return "reject"

    if red_team.get("overall_assessment") == "high_risk":
        risk_flags = red_team.get("risk_flags", [])
        if "profit_factor_below_breakeven" in risk_flags or "negative_net_return" in risk_flags:
            return "reject"
        return "defer"

    pf = (metrics or {}).get("profit_factor") if metrics else None
    net_return = (metrics or {}).get("net_return") if metrics else None

    if pf is not None and pf >= 1.5 and net_return is not None and net_return > 0:
        return "run_robustness_sweep_next"

    if pf is not None and pf >= 1.0 and net_return is not None and net_return > 0:
        return "defer"

    if red_team.get("overall_assessment") == "medium_risk":
        return "revise_implementation"

    return "defer"
