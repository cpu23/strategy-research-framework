from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from statistics import median, stdev
from typing import Any

import yaml

from . import registry
from . import implementation as impl_mod
from .contracts import spec_references_production_path
from .schemas import REPO_ROOT, SANDBOX_ROOT, GENERATED_SPECS_DIR, STRATEGIES_ROOT, load_yaml

GENERATED_ROBUSTNESS_REVIEW_SCHEMA = "generated_robustness_review_v1"

ALLOWED_ROBUSTNESS_RECOMMENDATIONS = {
    "reject",
    "revise_strategy_spec",
    "revise_implementation",
    "run_additional_bounded_sweep",
    "consider_lifecycle_candidate",
    "defer",
}

ALLOWED_SWEEP_PARAMETERS = {
    "InpAtrPeriod",
    "InpStopLossAtr",
    "InpTakeProfitAtr",
    "InpRiskPerTrade",
    "InpMinBreakDistanceAtr",
}

STRATEGIES_DIR = REPO_ROOT / "automated" / "strategies"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def require_generated_robustness_eligibility(
    db_path: str | Path,
    strategy_id: str,
    strategy_version: str,
    *,
    allow_mock_compile: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []

    request = registry.find_implementation_request(db_path, strategy_id, strategy_version)
    if not request:
        errors.append("No implementation request found")
        return {"eligible": False, "errors": errors}

    impl_request_id = request["implementation_request_id"]

    implementations = registry.list_implementations(db_path, impl_request_id)
    if not implementations:
        errors.append("No implementation record found")
        return {"eligible": False, "errors": errors}

    current_impl = implementations[-1]

    if not current_impl.get("approved_for_baseline"):
        errors.append("Not approved for baseline")

    approval_scope = current_impl.get("approval_scope", "baseline_only")
    if approval_scope != "baseline_only":
        errors.append(f"Approval scope is '{approval_scope}'; 'baseline_only' required")

    impl_id = current_impl["implementation_id"]
    usages = registry.list_approval_usage_for_implementation(db_path, impl_id)
    completed_usages = [u for u in usages if u.get("status") == "completed"]
    if not completed_usages:
        errors.append("No completed approval usage record found; baseline experiment must complete first")

    baseline_experiment_id = current_impl.get("baseline_experiment_id")
    if not baseline_experiment_id:
        for u in completed_usages:
            if u.get("experiment_id"):
                baseline_experiment_id = u["experiment_id"]
                break
    if not baseline_experiment_id:
        errors.append("No baseline experiment id found; baseline run must complete first")

    if baseline_experiment_id:
        baseline_review_dir = (
            REPO_ROOT / "automated" / "research_runs" / baseline_experiment_id / "reports"
        )
        baseline_review_path = baseline_review_dir / "generated_baseline_review.yaml"
        if not baseline_review_path.is_file():
            baseline_review_path = (
                REPO_ROOT / "automated" / "research_runs" / baseline_experiment_id / "generated_baseline_review.yaml"
            )
        if not baseline_review_path.is_file():
            errors.append(f"generated_baseline_review artifact not found for experiment {baseline_experiment_id}")

    compile_status = current_impl.get("compile_status")
    if not compile_status:
        errors.append("Compile status is missing")
    elif compile_status == "failed":
        errors.append("Compile status is 'failed'")
    elif compile_status == "mock_checked" and not allow_mock_compile:
        errors.append("Compile was mock-checked; real compile 'passed' required unless explicitly allowed")

    input_match = current_impl.get("input_match_status")
    if not input_match:
        errors.append("Input match status is not set")
    elif input_match == "mismatch":
        errors.append("Input match status is 'mismatch'")

    review_path = (
        REPO_ROOT / "automated" / "implementation_requests" / impl_request_id / "diff_review.yaml"
    )
    if not review_path.is_file():
        errors.append("Diff review artifact not found")

    generated_mq5 = current_impl.get("generated_mq5_path", "")
    if generated_mq5:
        try:
            impl_mod.assert_sandbox_path(Path(generated_mq5))
        except Exception:
            errors.append(f"Generated .mq5 path outside sandbox: {generated_mq5}")
    else:
        errors.append("No generated .mq5 path recorded")

    spec_path = GENERATED_SPECS_DIR / f"{strategy_id}.yaml"
    if spec_path.is_file():
        try:
            offending = spec_references_production_path(spec_path, STRATEGIES_ROOT)
            if offending is not None:
                errors.append(f"Generated spec points to automated/strategies/ in {offending}")
        except Exception:
            errors.append(f"Could not parse generated spec: {spec_path}")

    if errors:
        return {
            "eligible": False,
            "errors": errors,
            "implementation_request_id": impl_request_id,
            "implementation_id": impl_id,
        }

    return {
        "eligible": True,
        "implementation_request_id": impl_request_id,
        "implementation_id": impl_id,
        "baseline_experiment_id": baseline_experiment_id,
        "note": "Generated strategy is eligible for robustness sweep",
    }


def validate_sweep_parameters(parameters: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for key in parameters:
        if key not in ALLOWED_SWEEP_PARAMETERS:
            warnings.append(f"Parameter '{key}' is not in the allowed set; blocked")
    return warnings


def has_blocked_mutations(sweep_config: dict[str, Any]) -> list[str]:
    blocked: list[str] = []
    config = sweep_config or {}

    if config.get("symbol"):
        blocked.append("symbol sweep is blocked")
    if config.get("timeframe"):
        blocked.append("timeframe sweep is blocked")
    if config.get("dataset_id"):
        blocked.append("dataset mutation is blocked")
    if config.get("cost_multipliers"):
        blocked.append("cost/slippage mutation is blocked")
    if config.get("cost_config"):
        blocked.append("cost/slippage mutation is blocked")

    validation_changes = config.get("allow_validation_threshold_changes") or config.get("validation")
    if validation_changes:
        blocked.append("validation threshold mutation is blocked")

    if config.get("allow_code_regeneration"):
        blocked.append("code regeneration during sweep is blocked")

    return blocked


def red_team_robustness_check(
    sweep_summary: dict[str, Any] | None,
    children_metrics: dict[str, dict[str, Any]] | None,
    baseline_metrics: dict[str, Any] | None = None,
    child_count: int = 0,
    children_failed: int = 0,
) -> dict[str, Any]:
    warnings: list[str] = []
    risk_flags: list[str] = []

    sweep_summary = sweep_summary or {}
    children_metrics = children_metrics or {}
    baseline_metrics = baseline_metrics or {}

    net_returns = [
        m.get("net_return") for m in children_metrics.values()
        if m.get("net_return") is not None
    ]
    profit_factors = [
        m.get("profit_factor") for m in children_metrics.values()
        if m.get("profit_factor") is not None
    ]
    trade_counts = [
        m.get("trade_count") for m in children_metrics.values()
        if m.get("trade_count") is not None
    ]

    if net_returns and len(net_returns) >= 3:
        mean_nr = sum(net_returns) / len(net_returns)
        positive_count = sum(1 for v in net_returns if v > 0)
        negative_count = sum(1 for v in net_returns if v <= 0)
        if negative_count > positive_count and positive_count <= 1:
            warnings.append("One good child surrounded by weak children: only 1 child has positive net return")
            risk_flags.append("isolated_good_child")
        try:
            dispersion = stdev(net_returns) / abs(mean_nr) if abs(mean_nr) > 1e-9 else 0
            if dispersion > 2.0:
                warnings.append(f"High metric dispersion: CV={dispersion:.2f}")
                risk_flags.append("high_dispersion")
        except Exception:
            pass

    if net_returns:
        positive_proportion = sum(1 for v in net_returns if v > 0) / len(net_returns)
        if positive_proportion < 0.3 and len(net_returns) >= 3:
            warnings.append(f"Most children fail validation: only {positive_proportion:.0%} have positive net return")
            risk_flags.append("most_children_fail")

    if profit_factors and len(profit_factors) >= 3:
        pf_mean = sum(profit_factors) / len(profit_factors)
        pf_above_1 = sum(1 for v in profit_factors if v > 1)
        if pf_above_1 < len(profit_factors) * 0.3:
            warnings.append("Profit factor unstable: fewer than 30% of children have PF > 1")
            risk_flags.append("profit_factor_unstable")
        try:
            pf_stdev = stdev(profit_factors)
            if pf_stdev > 1.5:
                warnings.append(f"Profit factor instability: standard deviation {pf_stdev:.2f}")
                risk_flags.append("profit_factor_high_variance")
        except Exception:
            pass

    if trade_counts:
        median_tc = median(trade_counts)
        if median_tc < 10:
            warnings.append(f"Low trade count across many children: median {median_tc:.0f} trades")
            risk_flags.append("low_trade_count")

    baseline_net = baseline_metrics.get("net_return")
    if baseline_net is not None and net_returns:
        median_nr = median(net_returns)
        if median_nr < baseline_net * 0.8:
            warnings.append(f"Baseline was better than most children: baseline net={baseline_net:.2f}, median child net={median_nr:.2f}")
            risk_flags.append("baseline_better_than_children")

    if child_count < 4:
        warnings.append(f"Sweep too small to conclude robustness: {child_count} children")
        risk_flags.append("sweep_too_small")

    if children_failed > 0:
        failure_rate = children_failed / max(child_count, 1)
        if failure_rate > 0.3:
            warnings.append(f"Excessive failed child runs: {children_failed}/{child_count} failed")
            risk_flags.append("excessive_failures")

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


def _compute_recommendation(
    red_team: dict[str, Any],
    children_completed: int,
    children_failed: int,
    profit_factors: list[float | None],
    net_returns: list[float | None],
) -> str:
    if children_completed == 0:
        return "reject"

    if red_team.get("overall_assessment") == "high_risk":
        return "reject"

    if children_failed > 0 and children_failed >= children_completed:
        return "reject"

    valid_pfs = [pf for pf in profit_factors if pf is not None]
    valid_nrs = [nr for nr in net_returns if nr is not None]

    if valid_pfs and valid_nrs:
        median_pf = median(valid_pfs)
        median_nr = median(valid_nrs)
        if median_pf >= 1.3 and median_nr > 0:
            return "consider_lifecycle_candidate"
        if median_pf >= 1.0 and median_nr > 0:
            return "run_additional_bounded_sweep"

    if red_team.get("overall_assessment") == "medium_risk":
        return "revise_strategy_spec"

    return "defer"


def _child_metrics_from_summary(
    sweep_summary: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    children_metrics: dict[str, dict[str, Any]] = {}
    for child in sweep_summary.get("children", []):
        exp_id = child.get("child_experiment_id") or child.get("experiment_id", "")
        metrics = child.get("metrics") or {}
        children_metrics[exp_id] = {
            "net_return": metrics.get("net_return"),
            "profit_factor": metrics.get("profit_factor"),
            "trade_count": metrics.get("trade_count"),
        }
    return children_metrics


def build_generated_robustness_review_packet(
    db_path: str | Path,
    *,
    sweep_id: str,
    baseline_experiment_id: str,
    strategy_id: str,
    strategy_version: str,
    implementation_request_id: str | None = None,
    implementation_id: str | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    sweep = registry.get_sweep(db_path, sweep_id)
    if not sweep:
        raise ValueError(f"sweep not found: {sweep_id}")

    children = registry.list_sweep_children(db_path, sweep_id)
    parent_experiment_id = sweep["parent_experiment_id"]

    child_experiment_ids = [c["child_experiment_id"] for c in children]

    child_metrics_raw: dict[str, dict[str, Any]] = {}
    children_completed = 0
    children_failed = 0
    for child in children:
        exp_id = child["child_experiment_id"]
        status = child.get("status", "")
        metrics = registry.get_experiment_metrics(db_path, exp_id) or {}
        child_metrics_raw[exp_id] = {
            "net_return": metrics.get("net_return"),
            "profit_factor": metrics.get("profit_factor"),
            "trade_count": metrics.get("trade_count"),
        }
        if status == "completed" or metrics.get("net_return") is not None:
            children_completed += 1
        elif status == "failed":
            children_failed += 1

    param_grid: dict[str, list[Any]] = {}
    for child in children:
        diff = {}
        try:
            raw = child.get("parameter_diff_json") or "{}"
            diff = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            pass
        for key, item in diff.items():
            to_val = item.get("to") if isinstance(item, dict) else item
            param_grid.setdefault(key, set())
            param_grid[key].add(str(to_val))
    param_grid_summary = {k: sorted(v) for k, v in param_grid.items()}

    profit_factors = [
        m["profit_factor"] for m in child_metrics_raw.values()
        if m["profit_factor"] is not None
    ]
    net_returns = [
        m["net_return"] for m in child_metrics_raw.values()
        if m["net_return"] is not None
    ]
    trade_counts = [
        m["trade_count"] for m in child_metrics_raw.values()
        if m["trade_count"] is not None
    ]

    best_pf = max(profit_factors) if profit_factors else None
    worst_pf = min(profit_factors) if profit_factors else None
    median_pf = median(profit_factors) if profit_factors else None
    best_nr = max(net_returns) if net_returns else None
    worst_nr = min(net_returns) if net_returns else None
    median_nr = median(net_returns) if net_returns else None

    trade_count_distribution = {}
    if trade_counts:
        trade_count_distribution = {
            "min": min(trade_counts),
            "max": max(trade_counts),
            "median": median(trade_counts),
        }

    validation_pass = sum(
        1 for m in child_metrics_raw.values()
        if m.get("net_return") is not None and m.get("net_return", 0) > 0
    )
    validation_fail = children_completed - validation_pass

    baseline_metrics = {}
    baseline_exp = registry.get_experiment(db_path, baseline_experiment_id)
    if baseline_exp:
        try:
            bm_raw = baseline_exp.get("headline_metrics_json") or "{}"
            baseline_metrics = json.loads(bm_raw) if isinstance(bm_raw, str) else (bm_raw or {})
        except Exception:
            pass

    red_team = red_team_robustness_check(
        sweep_summary=sweep,
        children_metrics=child_metrics_raw,
        baseline_metrics=baseline_metrics,
        child_count=len(children),
        children_failed=children_failed,
    )

    recommendation = _compute_recommendation(
        red_team=red_team,
        children_completed=children_completed,
        children_failed=children_failed,
        profit_factors=profit_factors,
        net_returns=net_returns,
    )

    packet: dict[str, Any] = {
        "schema_version": GENERATED_ROBUSTNESS_REVIEW_SCHEMA,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "implementation_request_id": implementation_request_id,
        "implementation_id": implementation_id,
        "baseline_experiment_id": baseline_experiment_id,
        "sweep_parent_experiment_id": parent_experiment_id,
        "child_experiment_ids": child_experiment_ids,
        "parameter_grid_summary": param_grid_summary,
        "children_completed": children_completed,
        "children_failed": children_failed,
        "best_profit_factor": best_pf,
        "worst_profit_factor": worst_pf,
        "median_profit_factor": median_pf,
        "best_net_return": best_nr,
        "worst_net_return": worst_nr,
        "median_net_return": median_nr,
        "trade_count_distribution": trade_count_distribution,
        "validation_pass_count": validation_pass,
        "validation_fail_count": validation_fail,
        "robustness_warnings": red_team.get("warnings", []),
        "red_team_assessment": red_team,
        "recommendation": recommendation,
    }

    out_dir = Path(output_dir) if output_dir else (
        REPO_ROOT / "automated" / "research_runs" / parent_experiment_id / "reports"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    packet_path = out_dir / "generated_robustness_review.yaml"
    packet_path.write_text(yaml.safe_dump(packet, sort_keys=False), encoding="utf-8")

    registry.attach_artifact(db_path, parent_experiment_id, "generated_robustness_review", packet_path)

    return {
        "packet_path": str(packet_path),
        "packet": packet,
    }
