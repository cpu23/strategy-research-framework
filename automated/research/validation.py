from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import registry
from .contracts import REQUIRED_RESULT_ARTIFACT_TYPES, resolve_stored_path
from .metrics import available_value, extract_metrics
from .schemas import GENERATED_SPECS_DIR, REPO_ROOT, load_yaml, resolve_hypothesis_file, validate_strategy_spec


GATE_PASS = "pass"
GATE_WARN = "warn"
GATE_FAIL = "fail"
GATE_NOT_AVAILABLE = "not_available"
GATE_NOT_IMPLEMENTED = "not_implemented"

REQUIRED_ARTIFACTS = {
    "trade_log": "trades.csv is required for trade diagnostics",
    "equity_curve": "equity.csv is required for equity diagnostics",
    "metrics_json": "run_summary.json is required for summary metrics",
    "raw_backtest_output": "terminal_run.log is required for run audit",
}

assert set(REQUIRED_ARTIFACTS) == REQUIRED_RESULT_ARTIFACT_TYPES


def _json_loads(value: str | None) -> Any:
    if not value:
        return None
    return json.loads(value)


def _artifact_by_type(artifacts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        latest[artifact["artifact_type"]] = artifact
    return latest


def _gate(status: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "reason": reason, **extra}


def _strategy_spec_for_experiment(experiment: dict[str, Any]) -> dict[str, Any] | None:
    spec_path = REPO_ROOT / "automated" / "specs" / "strategies" / f"{experiment['strategy_id']}.yaml"
    if not spec_path.is_file():
        spec_path = GENERATED_SPECS_DIR / f"{experiment['strategy_id']}.yaml"
    if not spec_path.is_file():
        return None
    spec = load_yaml(spec_path)
    validate_strategy_spec(spec)
    return spec


def _spec_validation_config(spec: dict[str, Any] | None) -> dict[str, Any]:
    validation = spec.get("validation", {}) if spec else {}
    return {
        "min_trades_required": validation.get("min_trades_required"),
        "min_trades_required_hard": validation.get("min_trades_required_hard"),
        "trade_count_unavailable_policy": validation.get("trade_count_unavailable_policy", "fail"),
        "largest_trade_concentration_warning_threshold": validation.get(
            "largest_trade_concentration_warning_threshold",
            0.20,
        ),
    }


def _identity_checks(experiment: dict[str, Any]) -> dict[str, Any]:
    dataset_hash_present = bool(experiment.get("dataset_hash") or experiment.get("dataset_bundle_hash"))
    return {
        "hypothesis": _gate(
            GATE_PASS if experiment.get("hypothesis_present") else GATE_FAIL,
            "hypothesis_id resolves to a hypothesis file"
            if experiment.get("hypothesis_present")
            else "hypothesis_id does not resolve to a hypothesis file",
            hypothesis_id=experiment.get("hypothesis_id"),
        ),
        "strategy": _gate(
            GATE_PASS if experiment.get("strategy_id") else GATE_FAIL,
            "strategy_id is present" if experiment.get("strategy_id") else "strategy_id is missing",
            strategy_id=experiment.get("strategy_id"),
            strategy_version=experiment.get("strategy_version"),
        ),
        "spec_hash": _gate(
            GATE_PASS if experiment.get("spec_hash") else GATE_FAIL,
            "spec_hash is present" if experiment.get("spec_hash") else "spec_hash is missing",
            spec_hash=experiment.get("spec_hash"),
        ),
        "parameter_set_hash": _gate(
            GATE_PASS if experiment.get("parameter_set_hash") else GATE_WARN,
            "parameter_set_hash is present" if experiment.get("parameter_set_hash") else "parameter_set_hash is missing",
            parameter_set_hash=experiment.get("parameter_set_hash"),
        ),
        "dataset_hash": _gate(
            GATE_PASS if dataset_hash_present else GATE_FAIL,
            "dataset hash is present" if dataset_hash_present else "dataset hash or dataset bundle hash is missing",
            dataset_id=experiment.get("dataset_id"),
            dataset_bundle_id=experiment.get("dataset_bundle_id"),
            dataset_hash=experiment.get("dataset_hash"),
            dataset_bundle_hash=experiment.get("dataset_bundle_hash"),
        ),
    }


def _artifact_checks(artifacts_by_type: dict[str, dict[str, Any]]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    for artifact_type, reason in REQUIRED_ARTIFACTS.items():
        artifact = artifacts_by_type.get(artifact_type)
        present = bool(artifact and resolve_stored_path(artifact["path"], REPO_ROOT).is_file())
        checks[artifact_type] = _gate(
            GATE_PASS if present else GATE_FAIL,
            f"{artifact_type} artifact is present" if present else reason,
            path=artifact.get("path") if artifact else None,
            hash=artifact.get("file_hash") if artifact else None,
        )
    for artifact_type in ["bars", "tester_agent_log", "compile_log", "artifact_manifest", "portfolio_report"]:
        artifact = artifacts_by_type.get(artifact_type)
        if artifact:
            checks[artifact_type] = _gate(
                GATE_PASS if resolve_stored_path(artifact["path"], REPO_ROOT).is_file() else GATE_WARN,
                f"{artifact_type} artifact linked",
                path=artifact.get("path"),
                hash=artifact.get("file_hash"),
            )
    return checks


def _sample_size_gate(metrics: dict[str, dict[str, Any]], config: dict[str, Any], experiment: dict[str, Any]) -> dict[str, Any]:
    total_trades = available_value(metrics, "total_trades")
    min_required = config.get("min_trades_required") or experiment.get("min_trades_required")
    hard_required = config.get("min_trades_required_hard")
    if total_trades is None:
        unavailable_metric = metrics.get("total_trades", {})
        policy = config.get("trade_count_unavailable_policy", "fail")
        return _gate(
            GATE_FAIL if policy == "fail" else GATE_WARN,
            unavailable_metric.get("reason") or "trade count is unavailable",
            total_trades=None,
            min_trades_required=min_required,
        )
    total_trades = int(total_trades)
    if hard_required is not None and total_trades < int(hard_required):
        return _gate(
            GATE_FAIL,
            f"trade count {total_trades} is below hard minimum {hard_required}",
            total_trades=total_trades,
            min_trades_required=min_required,
            min_trades_required_hard=hard_required,
        )
    if min_required is not None and total_trades < int(min_required):
        return _gate(
            GATE_WARN,
            f"trade count {total_trades} is below warning minimum {min_required}",
            total_trades=total_trades,
            min_trades_required=min_required,
            min_trades_required_hard=hard_required,
        )
    return _gate(
        GATE_PASS,
        f"trade count {total_trades} meets configured minimum",
        total_trades=total_trades,
        min_trades_required=min_required,
        min_trades_required_hard=hard_required,
    )


def _concentration_gate(metrics: dict[str, dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    concentration = available_value(metrics, "largest_trade_abs_pnl_pct_of_total_abs_pnl")
    threshold = config.get("largest_trade_concentration_warning_threshold", 0.20)
    if concentration is None:
        item = metrics.get("largest_trade_abs_pnl_pct_of_total_abs_pnl", {})
        return _gate(
            GATE_NOT_AVAILABLE,
            item.get("reason") or "largest-trade concentration cannot be computed",
            threshold=threshold,
            largest_trade_abs_pnl_pct_of_total_abs_pnl=None,
        )
    status = GATE_WARN if concentration > threshold else GATE_PASS
    return _gate(
        status,
        (
            f"largest trade concentration {concentration:.4f} exceeds threshold {threshold:.4f}"
            if status == GATE_WARN
            else f"largest trade concentration {concentration:.4f} is within threshold {threshold:.4f}"
        ),
        threshold=threshold,
        largest_trade_abs_pnl_pct_of_total_abs_pnl=concentration,
        largest_trade_pct_of_total_pnl=available_value(metrics, "largest_trade_pct_of_total_pnl"),
    )


def _long_short_imbalance_gate(metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    long_count = available_value(metrics, "long_trade_count")
    short_count = available_value(metrics, "short_trade_count")
    total = available_value(metrics, "total_trades")
    if long_count is None or short_count is None or not total:
        return _gate(GATE_NOT_AVAILABLE, "long/short counts or total trades are unavailable")
    dominant = max(long_count, short_count) / total
    return _gate(
        GATE_WARN if dominant > 0.90 and total >= 10 else GATE_PASS,
        f"dominant side share is {dominant:.2%}",
        long_trade_count=long_count,
        short_trade_count=short_count,
        dominant_side_share=dominant,
    )


def _cost_gate(spec: dict[str, Any] | None, experiment: dict[str, Any]) -> dict[str, Any]:
    costs = spec.get("costs", {}) if spec else {}
    documented = bool(experiment.get("cost_assumptions_documented")) and costs.get("assumptions_documented") is True
    slippage = costs.get("slippage", {}) if isinstance(costs.get("slippage"), dict) else {}
    commission = costs.get("commission", {}) if isinstance(costs.get("commission"), dict) else {}
    spread = costs.get("spread_source", {}) if isinstance(costs.get("spread_source"), dict) else {}
    return _gate(
        GATE_PASS if documented else GATE_FAIL,
        "cost assumptions are documented" if documented else "cost assumptions are missing or not documented",
        fee_bps=commission.get("fee_bps"),
        slippage_bps=slippage.get("slippage_bps"),
        slippage_points=slippage.get("value") if slippage.get("type") == "points" else None,
        spread_assumption=spread,
        stress_multiplier=costs.get("stress_multiplier"),
        notes=costs.get("notes"),
    )


def _execution_gate(spec: dict[str, Any] | None, experiment: dict[str, Any]) -> dict[str, Any]:
    execution = spec.get("execution_timing", {}) if spec else (_json_loads(experiment.get("execution_timing_json")) or {})
    required = ["signal_bar", "entry_bar", "assumed_fill_price"]
    documented = all(execution.get(key) for key in required)
    return _gate(
        GATE_PASS if documented else GATE_FAIL,
        "execution assumptions are documented" if documented else "execution timing assumptions are missing",
        signal_bar=execution.get("signal_bar"),
        entry_bar=execution.get("entry_bar"),
        assumed_fill_price=execution.get("assumed_fill_price"),
        notes=execution.get("exit_evaluation"),
    )


def _portfolio_correlation_gate(artifacts_by_type: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    artifact = artifacts_by_type.get("portfolio_report") if artifacts_by_type else None
    if not artifact:
        return {
            "status": GATE_NOT_IMPLEMENTED,
            "warnings": ["Portfolio correlation remains a placeholder until portfolio return streams exist"],
        }
    path = resolve_stored_path(artifact["path"], REPO_ROOT)
    if not path.is_file():
        return {"status": GATE_NOT_AVAILABLE, "warnings": [f"portfolio report artifact is missing: {path}"]}
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": GATE_NOT_AVAILABLE, "warnings": [f"portfolio report is not valid JSON: {path}"]}
    status = report.get("status")
    if status not in {GATE_PASS, GATE_WARN, GATE_FAIL, GATE_NOT_AVAILABLE}:
        status = GATE_WARN
    return {
        "status": status,
        "portfolio_id": report.get("portfolio_id"),
        "report_path": str(path),
        "warnings": report.get("warnings", []),
    }


def _sweep_summaries(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    for artifact in artifacts:
        if artifact.get("artifact_type") != "sweep_summary":
            continue
        path = resolve_stored_path(artifact["path"], REPO_ROOT)
        if not path.is_file():
            continue
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        sweep_type = summary.get("sweep_type")
        if sweep_type:
            summaries[sweep_type] = {
                "status": summary.get("status", GATE_WARN),
                "sweep_id": summary.get("sweep_id"),
                "summary_path": str(path),
                "key_metrics": summary.get("robustness", {}),
                "reason": summary.get("reason"),
            }
    return summaries


def _placeholder_advanced_gates(
    experiment: dict[str, Any],
    artifacts_by_type: dict[str, dict[str, Any]] | None = None,
    sweep_summaries: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parameter_diff = _json_loads(experiment.get("parameter_diff_json"))
    sweep_summaries = sweep_summaries or {}
    parameter_summary = sweep_summaries.get("parameter_robustness")
    cost_summary = sweep_summaries.get("cost_stress")
    walk_forward_summary = sweep_summaries.get("walk_forward_scaffold")
    return {
        "parameter_robustness": parameter_summary or {
            "status": GATE_NOT_IMPLEMENTED,
            "tested_parameters": [],
            "plateau_score": None,
            "fragility_warnings": [],
            "experiment_parameter_diff": parameter_diff,
            "warnings": ["Parameter robustness reruns are not implemented in phase 3"],
        },
        "cost_delay_stress": cost_summary or {
            "status": GATE_NOT_IMPLEMENTED,
            "cost_multipliers_tested": [],
            "one_bar_delay_tested": False,
            "degradation_score": None,
            "warnings": ["Cost/delay stress not implemented in phase 3"],
        },
        "walk_forward": walk_forward_summary or {
            "status": GATE_NOT_IMPLEMENTED,
            "warnings": ["Walk-forward validation not implemented in phase 3"],
        },
        "portfolio_correlation": _portfolio_correlation_gate(artifacts_by_type),
    }


def _collect_statuses(section: Any) -> list[str]:
    statuses: list[str] = []
    if isinstance(section, dict):
        if isinstance(section.get("status"), str):
            statuses.append(section["status"])
        for value in section.values():
            statuses.extend(_collect_statuses(value))
    elif isinstance(section, list):
        for value in section:
            statuses.extend(_collect_statuses(value))
    return statuses


def _overall_status(report: dict[str, Any]) -> str:
    hard_sections = [
        report["sections"]["identity_checks"],
        report["sections"]["artifact_checks"],
        report["sections"]["sample_size_gate"],
        report["sections"]["cost_assumption_gate"],
        report["sections"]["execution_assumption_gate"],
    ]
    warning_sections = [
        report["sections"]["trade_diagnostics"],
        report["sections"]["concentration_gate"],
        report["sections"]["placeholder_advanced_gates"],
    ]
    hard_statuses = [status for section in hard_sections for status in _collect_statuses(section)]
    if GATE_FAIL in hard_statuses:
        return GATE_FAIL
    warning_statuses = [status for section in warning_sections for status in _collect_statuses(section)]
    if GATE_WARN in hard_statuses or GATE_WARN in warning_statuses or GATE_NOT_IMPLEMENTED in warning_statuses or GATE_NOT_AVAILABLE in warning_statuses:
        return GATE_WARN
    return GATE_PASS


def _artifact_path(artifacts_by_type: dict[str, dict[str, Any]], artifact_type: str) -> str | None:
    artifact = artifacts_by_type.get(artifact_type)
    return str(resolve_stored_path(artifact["path"], REPO_ROOT)) if artifact else None


def build_validation_report(db_path: str | Path, experiment_id: str) -> dict[str, Any]:
    experiment = registry.get_experiment(db_path, experiment_id)
    if not experiment:
        raise ValueError(f"experiment not found: {experiment_id}")
    artifacts = registry.list_artifacts(db_path, experiment_id)
    artifacts_by_type = _artifact_by_type(artifacts)
    sweep_summaries = _sweep_summaries(artifacts)
    spec = _strategy_spec_for_experiment(experiment)
    config = _spec_validation_config(spec)
    parsed_metrics = extract_metrics(
        trade_log_path=_artifact_path(artifacts_by_type, "trade_log"),
        equity_curve_path=_artifact_path(artifacts_by_type, "equity_curve"),
        summary_path=_artifact_path(artifacts_by_type, "metrics_json"),
    )
    trade_diagnostics = {
        "metrics": parsed_metrics,
        "long_short_imbalance_gate": _long_short_imbalance_gate(parsed_metrics),
        "metric_availability_notes": [
            {"metric_name": name, "reason": item.get("reason")}
            for name, item in parsed_metrics.items()
            if item.get("availability") != "available"
        ],
    }
    report = {
        "schema_version": "validation_report_v2",
        "experiment_id": experiment_id,
        "strategy_id": experiment["strategy_id"],
        "hypothesis_id": experiment["hypothesis_id"],
        "dataset_id": experiment["dataset_id"],
        "dataset_bundle_id": experiment["dataset_bundle_id"],
        "spec_hash": experiment["spec_hash"],
        "parameter_set_hash": experiment["parameter_set_hash"],
        "generated_at": registry.utc_now(),
        "sections": {
            "identity_checks": _identity_checks(experiment),
            "artifact_checks": _artifact_checks(artifacts_by_type),
            "trade_diagnostics": trade_diagnostics,
            "sample_size_gate": _sample_size_gate(parsed_metrics, config, experiment),
            "concentration_gate": _concentration_gate(parsed_metrics, config),
            "cost_assumption_gate": _cost_gate(spec, experiment),
            "execution_assumption_gate": _execution_gate(spec, experiment),
            "placeholder_advanced_gates": _placeholder_advanced_gates(experiment, artifacts_by_type, sweep_summaries),
        },
    }
    report["gate_status"] = _overall_status(report)
    report["hard_failures"] = [
        item["reason"]
        for section_name in ["identity_checks", "artifact_checks", "sample_size_gate", "cost_assumption_gate", "execution_assumption_gate"]
        for item in (
            report["sections"][section_name].values()
            if isinstance(report["sections"][section_name], dict) and "status" not in report["sections"][section_name]
            else [report["sections"][section_name]]
        )
        if isinstance(item, dict) and item.get("status") == GATE_FAIL
    ]
    report["warnings"] = [
        item.get("reason") or "; ".join(item.get("warnings", [])) or f"{item.get('status')} gate requires attention"
        for section in report["sections"].values()
        for item in _flatten_gates(section)
        if item.get("status") in {GATE_WARN, GATE_NOT_AVAILABLE, GATE_NOT_IMPLEMENTED}
    ]
    report["recommended_next_action"] = (
        "Fix hard validation failures before interpreting this experiment."
        if report["hard_failures"]
        else "Phase 3 hard gates passed; treat warning and future gates as research debt before promotion."
    )
    return report


def _flatten_gates(value: Any) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if isinstance(value.get("status"), str):
            gates.append(value)
        for child in value.values():
            gates.extend(_flatten_gates(child))
    elif isinstance(value, list):
        for child in value:
            gates.extend(_flatten_gates(child))
    return gates


def _metrics_for_registry(report: dict[str, Any]) -> dict[str, Any]:
    metrics = report["sections"]["trade_diagnostics"]["metrics"]
    net_return = available_value(metrics, "net_return")
    max_drawdown = available_value(metrics, "max_drawdown")
    return {
        "period_type": "full",
        "net_return": net_return,
        "cagr": None,
        "sharpe": available_value(metrics, "sharpe"),
        "sortino": None,
        "max_drawdown": max_drawdown,
        "calmar": net_return / max_drawdown if net_return is not None and max_drawdown not in (None, 0) else None,
        "win_rate": available_value(metrics, "win_rate"),
        "avg_trade": available_value(metrics, "average_trade"),
        "median_trade": available_value(metrics, "median_trade"),
        "profit_factor": available_value(metrics, "profit_factor"),
        "exposure_time": None,
        "turnover": None,
        "trade_count": available_value(metrics, "total_trades"),
        "best_trade_pct_of_total": available_value(metrics, "largest_trade_pct_of_total_pnl"),
        "cost_sensitivity_score": None,
        "parameter_stability_score": None,
        "correlation_to_portfolio": None,
        "notes": "Parsed from phase 3 validation artifacts; unavailable fields left null.",
    }


def _status_for_report(report: dict[str, Any]) -> str:
    if report["gate_status"] == GATE_FAIL:
        return "failed"
    if report["gate_status"] == GATE_WARN:
        return "completed_with_warnings"
    return "completed"


def write_validation_report(db_path: str | Path, experiment_id: str, output_path: str | Path) -> dict[str, Any]:
    report = build_validation_report(db_path, experiment_id)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    registry.update_experiment(
        db_path,
        experiment_id,
        validation_report_path=str(path),
        gate_status=report["gate_status"],
        status=_status_for_report(report),
    )
    registry.attach_artifact(db_path, experiment_id, "validation_report", path)
    registry.upsert_experiment_metrics(db_path, experiment_id, _metrics_for_registry(report))
    return report
