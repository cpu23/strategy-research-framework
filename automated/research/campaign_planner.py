from __future__ import annotations

from pathlib import Path
from typing import Any

from .edge_library import MT5_TIMEFRAME_RE
from .hashing import stable_hash
from .schemas import SchemaValidationError, load_yaml


CAMPAIGN_CONFIG_SCHEMA = "research_campaign_config_v1"
GENERATED_RESEARCH_CAMPAIGN_PLAN_SCHEMA = "generated_research_campaign_plan_v1"
CAMPAIGN_BUDGET_REPORT_SCHEMA = "campaign_budget_report_v1"
RANKED_MANUAL_REVIEW_QUEUE_SCHEMA = "ranked_manual_baseline_review_queue_v1"

DATASET_POLICIES = {"frozen_registered_only", "explicit_dataset_map", "manual_resolution_required"}
COST_MODEL_POLICIES = {"frozen_from_strategy_spec"}
VALIDATION_THRESHOLD_POLICIES = {"frozen"}
RUNNER_POLICIES = {"frozen"}


def load_campaign_config(path: Path) -> dict[str, Any]:
    return validate_campaign_config(load_yaml(path))


def validate_campaign_config(config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise SchemaValidationError("campaign_config must be a mapping")
    for key in [
        "campaign_id",
        "edge_ids",
        "hypothesis_batch_ids",
        "asset_timeframe_grid",
        "controls",
        "budgets",
        "similarity_policy",
        "outputs",
    ]:
        if key not in config:
            raise SchemaValidationError(f"campaign_config.{key} is required")
    if not isinstance(config["campaign_id"], str) or not config["campaign_id"].startswith("CAMP_"):
        raise SchemaValidationError("campaign_config.campaign_id must use CAMP_ prefix")
    _require_prefixed_list(config, "edge_ids", "EDGE_")
    _require_list(config, "hypothesis_batch_ids")
    _validate_grid(config["asset_timeframe_grid"])

    controls = config["controls"]
    if not isinstance(controls, dict):
        raise SchemaValidationError("campaign_config.controls must be a mapping")
    _require_policy(controls, "dataset_policy", DATASET_POLICIES)
    _require_policy(controls, "cost_model_policy", COST_MODEL_POLICIES)
    _require_policy(controls, "validation_threshold_policy", VALIDATION_THRESHOLD_POLICIES)
    _require_policy(controls, "runner_policy", RUNNER_POLICIES)

    budgets = config["budgets"]
    if not isinstance(budgets, dict):
        raise SchemaValidationError("campaign_config.budgets must be a mapping")
    for key in [
        "max_total_planned_specs",
        "max_per_edge",
        "max_per_asset_timeframe",
        "max_per_similarity_cluster_per_asset_timeframe",
    ]:
        value = budgets.get(key)
        if not isinstance(value, int) or value <= 0:
            raise SchemaValidationError(f"campaign_config.budgets.{key} must be a positive integer")

    sim = config["similarity_policy"]
    if not isinstance(sim, dict):
        raise SchemaValidationError("campaign_config.similarity_policy must be a mapping")
    if sim.get("allow_similarity") is not True:
        raise SchemaValidationError("campaign_config.similarity_policy.allow_similarity must be true")
    for key in ["max_per_cluster_total", "max_per_cluster_per_asset_timeframe"]:
        value = sim.get(key)
        if not isinstance(value, int) or value <= 0:
            raise SchemaValidationError(f"campaign_config.similarity_policy.{key} must be a positive integer")

    if not isinstance(config["outputs"], dict):
        raise SchemaValidationError("campaign_config.outputs must be a mapping")
    return config


def build_asset_timeframe_matrix(config: dict[str, Any]) -> dict[str, Any]:
    config = validate_campaign_config(dict(config))
    seen: set[tuple[str, str]] = set()
    pairs: list[dict[str, str]] = []
    for row in config["asset_timeframe_grid"]:
        symbol = _normalize_symbol(row["symbol"])
        for timeframe in row["timeframes"]:
            pair = (symbol, timeframe)
            if pair in seen:
                continue
            seen.add(pair)
            pairs.append({"symbol": symbol, "timeframe": timeframe})
    pairs.sort(key=lambda item: (item["symbol"], item["timeframe"]))
    return {
        "artifact_type": "campaign_asset_timeframe_matrix",
        "campaign_id": config["campaign_id"],
        "asset_timeframe_pairs": pairs,
        "pair_count": len(pairs),
    }


def select_hypotheses_for_campaign(batch: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    config = validate_campaign_config(dict(config))
    matrix = build_asset_timeframe_matrix(config)["asset_timeframe_pairs"]
    selected: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if batch.get("batch_id") not in config["hypothesis_batch_ids"]:
        return {"selected": [], "rejected": [{"reason": "batch_not_in_campaign", "batch_id": batch.get("batch_id")}]}
    if batch.get("edge_id") not in config["edge_ids"]:
        return {"selected": [], "rejected": [{"reason": "edge_not_in_campaign", "edge_id": batch.get("edge_id")}]}

    hypotheses = batch.get("accepted_hypotheses") or [
        item for item in batch.get("hypotheses", []) if item.get("screening_status") == "accepted"
    ]
    for hypothesis in hypotheses:
        hyp_symbols = {_normalize_symbol(symbol) for symbol in hypothesis.get("candidate_symbols", [])}
        hyp_timeframes = set(hypothesis.get("candidate_timeframes", []))
        matched = False
        for target in matrix:
            if target["symbol"] in hyp_symbols and target["timeframe"] in hyp_timeframes:
                matched = True
                selected.append(_planned_item(config["campaign_id"], batch, hypothesis, target))
        if not matched:
            rejected.append({"hypothesis_id": hypothesis.get("hypothesis_id"), "reason": "no_compatible_asset_timeframe"})
    selected.sort(key=lambda item: item["planned_spec_id"])
    return {"selected": selected, "rejected": rejected}


def apply_campaign_budgets(selected: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    config = validate_campaign_config(dict(config))
    budgets = config["budgets"]
    sim = config["similarity_policy"]
    planned: list[dict[str, Any]] = []
    capped: list[dict[str, Any]] = []
    counts = {
        "total": 0,
        "edge": {},
        "asset_timeframe": {},
        "cluster_total": {},
        "cluster_asset_timeframe": {},
    }

    for item in sorted(selected, key=lambda row: row["planned_spec_id"]):
        edge_id = item["edge_id"]
        atf = f"{item['symbol']}:{item['timeframe']}"
        cluster = item.get("similarity_cluster_id") or "SIM_UNKNOWN"
        cluster_atf = f"{cluster}:{atf}"
        reason = _budget_cap_reason(counts, budgets, sim, edge_id, atf, cluster, cluster_atf)
        row = dict(item)
        if reason:
            row["planning_status"] = "budget_capped"
            row["budget_cap_reason"] = reason
            capped.append(row)
            continue
        counts["total"] += 1
        counts["edge"][edge_id] = counts["edge"].get(edge_id, 0) + 1
        counts["asset_timeframe"][atf] = counts["asset_timeframe"].get(atf, 0) + 1
        counts["cluster_total"][cluster] = counts["cluster_total"].get(cluster, 0) + 1
        counts["cluster_asset_timeframe"][cluster_atf] = counts["cluster_asset_timeframe"].get(cluster_atf, 0) + 1
        row["planning_status"] = "planned"
        planned.append(row)

    return {
        "planned_specs": planned,
        "budget_capped": capped,
        "budget_summary": {
            "planned": len(planned),
            "budget_capped": len(capped),
            "max_total_planned_specs": budgets["max_total_planned_specs"],
        },
    }


def build_campaign_plan(config: dict[str, Any], hypothesis_batches: list[dict[str, Any]]) -> dict[str, Any]:
    config = validate_campaign_config(dict(config))
    matrix = build_asset_timeframe_matrix(config)
    selected: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for batch in hypothesis_batches:
        result = select_hypotheses_for_campaign(batch, config)
        selected.extend(result["selected"])
        rejected.extend(result["rejected"])
    budgeted = apply_campaign_budgets(selected, config)
    return {
        "schema_version": GENERATED_RESEARCH_CAMPAIGN_PLAN_SCHEMA,
        "artifact_type": "generated_research_campaign_plan",
        "campaign_id": config["campaign_id"],
        "edge_ids": list(config["edge_ids"]),
        "hypothesis_batch_ids": list(config["hypothesis_batch_ids"]),
        "controls": dict(config["controls"]),
        "asset_timeframe_matrix": matrix,
        "planned_specs": budgeted["planned_specs"],
        "budget_capped": budgeted["budget_capped"],
        "selection_rejections": rejected,
        "budget_summary": budgeted["budget_summary"],
        "authority": _planning_authority(),
    }


def build_campaign_budget_report(plan: dict[str, Any]) -> dict[str, Any]:
    capped = list(plan.get("budget_capped", []))
    reasons: dict[str, int] = {}
    for item in capped:
        reason = item.get("budget_cap_reason", "unknown")
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "schema_version": CAMPAIGN_BUDGET_REPORT_SCHEMA,
        "artifact_type": "campaign_budget_report",
        "campaign_id": plan.get("campaign_id"),
        "planned_count": len(plan.get("planned_specs", [])),
        "budget_capped_count": len(capped),
        "budget_cap_reasons": reasons,
        "authority": dict(plan.get("authority", _planning_authority())),
    }


def build_ranked_manual_review_queue(plan: dict[str, Any]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(sorted(plan.get("planned_specs", []), key=_rank_key), 1):
        entries.append(
            {
                "rank": index,
                "planned_spec_id": item["planned_spec_id"],
                "hypothesis_id": item["hypothesis_id"],
                "lineage": dict(item["lineage"]),
                "target": {"symbol": item["symbol"], "timeframe": item["timeframe"]},
                "similarity_cluster_id": item.get("similarity_cluster_id"),
                "advisory_only": True,
            }
        )
    return {
        "schema_version": RANKED_MANUAL_REVIEW_QUEUE_SCHEMA,
        "artifact_type": "ranked_manual_baseline_review_queue",
        "campaign_id": plan.get("campaign_id"),
        "entries": entries,
        "authority": dict(plan.get("authority", _planning_authority())),
    }


def _planned_item(campaign_id: str, batch: dict[str, Any], hypothesis: dict[str, Any], target: dict[str, str]) -> dict[str, Any]:
    lineage = dict(hypothesis.get("lineage", {}))
    payload = {
        "campaign_id": campaign_id,
        "hypothesis_id": hypothesis.get("hypothesis_id"),
        "symbol": target["symbol"],
        "timeframe": target["timeframe"],
    }
    return {
        "planned_spec_id": f"PLAN_{stable_hash(payload)[:16].upper()}",
        "campaign_id": campaign_id,
        "batch_id": batch.get("batch_id"),
        "edge_id": lineage.get("edge_id") or batch.get("edge_id"),
        "hypothesis_id": hypothesis.get("hypothesis_id"),
        "lineage": lineage,
        "symbol": target["symbol"],
        "timeframe": target["timeframe"],
        "strategy_family": hypothesis.get("strategy_family"),
        "similarity_cluster_id": hypothesis.get("similarity_cluster_id"),
        "cluster_rank": hypothesis.get("cluster_rank"),
    }


def _budget_cap_reason(
    counts: dict[str, Any],
    budgets: dict[str, int],
    sim: dict[str, int],
    edge_id: str,
    atf: str,
    cluster: str,
    cluster_atf: str,
) -> str | None:
    if counts["total"] >= budgets["max_total_planned_specs"]:
        return "max_total_planned_specs"
    if counts["edge"].get(edge_id, 0) >= budgets["max_per_edge"]:
        return "max_per_edge"
    if counts["asset_timeframe"].get(atf, 0) >= budgets["max_per_asset_timeframe"]:
        return "max_per_asset_timeframe"
    if counts["cluster_asset_timeframe"].get(cluster_atf, 0) >= budgets["max_per_similarity_cluster_per_asset_timeframe"]:
        return "max_per_similarity_cluster_per_asset_timeframe"
    if counts["cluster_total"].get(cluster, 0) >= sim["max_per_cluster_total"]:
        return "max_per_cluster_total"
    if counts["cluster_asset_timeframe"].get(cluster_atf, 0) >= sim["max_per_cluster_per_asset_timeframe"]:
        return "max_per_cluster_per_asset_timeframe"
    return None


def _validate_grid(grid: Any) -> None:
    if not isinstance(grid, list) or not grid:
        raise SchemaValidationError("campaign_config.asset_timeframe_grid must be a non-empty list")
    for row in grid:
        if not isinstance(row, dict):
            raise SchemaValidationError("campaign_config.asset_timeframe_grid entries must be mappings")
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise SchemaValidationError("campaign_config.asset_timeframe_grid[].symbol is required")
        timeframes = row.get("timeframes")
        if not isinstance(timeframes, list) or not timeframes:
            raise SchemaValidationError("campaign_config.asset_timeframe_grid[].timeframes must be a non-empty list")
        for timeframe in timeframes:
            if not isinstance(timeframe, str) or not MT5_TIMEFRAME_RE.match(timeframe):
                raise SchemaValidationError(f"invalid campaign timeframe: {timeframe!r}")


def _require_prefixed_list(config: dict[str, Any], key: str, prefix: str) -> list[str]:
    values = _require_list(config, key)
    if not values or any(not isinstance(item, str) or not item.startswith(prefix) for item in values):
        raise SchemaValidationError(f"campaign_config.{key} entries must use {prefix} prefix")
    return values


def _require_list(config: dict[str, Any], key: str) -> list[Any]:
    value = config.get(key)
    if not isinstance(value, list) or not value:
        raise SchemaValidationError(f"campaign_config.{key} must be a non-empty list")
    return value


def _require_policy(controls: dict[str, Any], key: str, allowed: set[str]) -> str:
    value = controls.get(key)
    if value not in allowed:
        raise SchemaValidationError(f"campaign_config.controls.{key} must be one of {sorted(allowed)}; got {value!r}")
    return value


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def _planning_authority() -> dict[str, bool]:
    return {
        "planning_only": True,
        "execution_authority": False,
        "dataset_mutation_authority": False,
        "cost_mutation_authority": False,
        "threshold_mutation_authority": False,
        "runner_mutation_authority": False,
        "baseline_decision_authority": False,
        "final_holdout_decision_authority": False,
        "state_transition_authority": False,
        "live_trading_authority": False,
    }


def _rank_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("edge_id", ""),
        item.get("similarity_cluster_id", ""),
        item.get("cluster_rank") or 0,
        item.get("symbol", ""),
        item.get("timeframe", ""),
        item.get("hypothesis_id", ""),
    )
