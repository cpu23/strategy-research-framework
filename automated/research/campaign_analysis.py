from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


OUTCOME_CATEGORIES = {
    "not_started",
    "pre_baseline_rejected",
    "baseline_failed",
    "baseline_warned",
    "baseline_passed",
    "robustness_failed",
    "robustness_warned",
    "robustness_passed",
    "final_holdout_failed",
    "final_holdout_warned",
    "final_holdout_passed",
    "manual_review_required",
    "insufficient_evidence",
}

ADVISORY_RECOMMENDATIONS = {
    "reject",
    "revise_thesis",
    "revise_mutation_recipe",
    "run_more_prebaseline_research",
    "request_manual_baseline_review",
    "request_manual_final_holdout_review",
    "defer",
}


def load_campaign_artifacts(paths: list[Path]) -> list[dict]:
    records: list[dict] = []
    for path in paths:
        payload = Path(path).read_text(encoding="utf-8")
        if Path(path).suffix.lower() == ".json":
            data = json.loads(payload)
        else:
            data = yaml.safe_load(payload)
        if isinstance(data, dict):
            records.append(data)
        elif isinstance(data, list):
            records.extend(item for item in data if isinstance(item, dict))
    return records


def group_results_by_edge_family(records: list[dict]) -> dict:
    return _group(records, lambda record: _first(record, "edge_family", "strategy_family", default="unknown"))


def group_results_by_asset_timeframe(records: list[dict]) -> dict:
    return _group(records, lambda record: f"{record.get('symbol') or 'unknown'}:{record.get('timeframe') or 'unknown'}")


def group_results_by_similarity_cluster(records: list[dict]) -> dict:
    return _group(records, lambda record: record.get("similarity_cluster_id") or "unknown")


def summarize_outcomes(records: list[dict]) -> dict:
    counts = {category: 0 for category in sorted(OUTCOME_CATEGORIES)}
    failure_modes: dict[str, int] = {}
    for record in records:
        outcome = _classify_outcome(record)
        counts[outcome] += 1
        failure_mode = classify_failure_mode(record)
        failure_modes[failure_mode] = failure_modes.get(failure_mode, 0) + 1
    return {
        "record_count": len(records),
        "outcome_counts": counts,
        "failure_mode_counts": failure_modes,
    }


def classify_failure_mode(record: dict) -> str:
    if _status(record, "compile_status") == "failed":
        return "compile_failure"
    if _status(record, "diff_review_status") in {"failed", "fail", "rejected"}:
        return "diff_review_failure"
    if _status(record, "validation_status", "baseline_status") in {"failed", "fail"}:
        return "baseline_failure"
    if _status(record, "robustness_status") in {"failed", "fail", "unstable"}:
        return "robustness_instability"
    if _status(record, "final_holdout_status") in {"failed", "fail"}:
        return "final_holdout_failure"
    if _status(record, "similarity_status") in {"redundant", "duplicate", "deprioritized"}:
        return "similarity_redundancy"
    sample_size = record.get("sample_size", record.get("trade_count"))
    min_sample = record.get("min_sample_size", record.get("min_trades_required", 0))
    if isinstance(sample_size, (int, float)) and isinstance(min_sample, (int, float)) and sample_size < min_sample:
        return "insufficient_sample_size"
    return "unknown"


def build_edge_family_meta_analysis(records: list[dict]) -> dict:
    groups = group_results_by_edge_family(records)
    return {
        "artifact_type": "edge_family_meta_analysis",
        "groups": {
            key: _group_summary(key, value, "edge_family")
            for key, value in sorted(groups.items())
        },
        "authority": _analysis_authority(),
    }


def build_asset_timeframe_effectiveness_report(records: list[dict]) -> dict:
    groups = group_results_by_asset_timeframe(records)
    return {
        "artifact_type": "asset_timeframe_effectiveness_report",
        "groups": {
            key: _group_summary(key, value, "asset_timeframe")
            for key, value in sorted(groups.items())
        },
        "authority": _analysis_authority(),
    }


def build_similarity_cluster_effectiveness_report(records: list[dict]) -> dict:
    groups = group_results_by_similarity_cluster(records)
    return {
        "artifact_type": "similarity_cluster_effectiveness_report",
        "groups": {
            key: _group_summary(key, value, "similarity_cluster")
            for key, value in sorted(groups.items())
        },
        "authority": _analysis_authority(),
    }


def build_generated_research_campaign_report(inputs: dict) -> dict:
    records = _extract_records(inputs)
    return {
        "artifact_type": "generated_research_campaign_report",
        "campaign_id": inputs.get("campaign_id") or _campaign_id_from_inputs(inputs),
        "outcome_summary": summarize_outcomes(records),
        "edge_family_meta_analysis": build_edge_family_meta_analysis(records),
        "asset_timeframe_effectiveness": build_asset_timeframe_effectiveness_report(records),
        "similarity_cluster_effectiveness": build_similarity_cluster_effectiveness_report(records),
        "recommendations": _campaign_recommendations(records),
        "authority": _analysis_authority(),
    }


def _classify_outcome(record: dict) -> str:
    explicit = record.get("outcome")
    if explicit in OUTCOME_CATEGORIES:
        return explicit
    if record.get("status") == "not_started":
        return "not_started"
    if record.get("pre_baseline_status") in {"rejected", "failed", "fail"}:
        return "pre_baseline_rejected"
    final_status = _status(record, "final_holdout_status")
    if final_status in {"passed", "pass"}:
        return "final_holdout_passed"
    if final_status in {"warned", "warn"}:
        return "final_holdout_warned"
    if final_status in {"failed", "fail"}:
        return "final_holdout_failed"
    robust_status = _status(record, "robustness_status")
    if robust_status in {"passed", "pass"}:
        return "robustness_passed"
    if robust_status in {"warned", "warn"}:
        return "robustness_warned"
    if robust_status in {"failed", "fail", "unstable"}:
        return "robustness_failed"
    baseline_status = _status(record, "validation_status", "baseline_status")
    if baseline_status in {"passed", "pass"}:
        return "baseline_passed"
    if baseline_status in {"warned", "warn"}:
        return "baseline_warned"
    if baseline_status in {"failed", "fail"}:
        return "baseline_failed"
    if record.get("manual_review_required") is True:
        return "manual_review_required"
    return "insufficient_evidence"


def _campaign_recommendations(records: list[dict]) -> list[dict]:
    summary = summarize_outcomes(records)
    counts = summary["outcome_counts"]
    recommendations: list[dict] = []
    if counts["baseline_failed"] or counts["pre_baseline_rejected"]:
        recommendations.append({"recommendation": "revise_mutation_recipe", "reason": "early_stage_failures"})
    if counts["robustness_failed"] or counts["robustness_warned"]:
        recommendations.append({"recommendation": "revise_thesis", "reason": "robustness_instability"})
    if counts["baseline_passed"] or counts["robustness_passed"]:
        recommendations.append({"recommendation": "request_manual_baseline_review", "reason": "promising_pre_holdout_results"})
    if counts["final_holdout_warned"]:
        recommendations.append({"recommendation": "request_manual_final_holdout_review", "reason": "holdout_warning_needs_review"})
    if not recommendations:
        recommendations.append({"recommendation": "run_more_prebaseline_research", "reason": "insufficient_evidence"})
    for item in recommendations:
        if item["recommendation"] not in ADVISORY_RECOMMENDATIONS:
            item["recommendation"] = "defer"
    return recommendations


def _group_summary(group_key: str, records: list[dict], dimension: str) -> dict:
    outcome_summary = summarize_outcomes(records)
    score = _research_priority_score(records, outcome_summary)
    return {
        dimension: group_key,
        "record_count": len(records),
        "outcome_summary": outcome_summary,
        "research_priority_score": score,
        "recommendation": _recommendation_from_score(score["score"], outcome_summary),
    }


def _research_priority_score(records: list[dict], outcome_summary: dict) -> dict:
    total = max(len(records), 1)
    counts = outcome_summary["outcome_counts"]
    evidence_quality = (
        counts["baseline_passed"] + counts["robustness_passed"] + counts["final_holdout_passed"]
    ) / total
    cross_asset_consistency = len({record.get("symbol") for record in records if record.get("symbol")}) / max(total, 1)
    timeframe_consistency = len({record.get("timeframe") for record in records if record.get("timeframe")}) / max(total, 1)
    robustness_quality = (counts["robustness_passed"] + 0.5 * counts["robustness_warned"]) / total
    diversity_value = len({record.get("similarity_cluster_id") for record in records if record.get("similarity_cluster_id")}) / max(total, 1)
    components = {
        "evidence_quality": round(min(evidence_quality, 1.0), 6),
        "cross_asset_consistency": round(min(cross_asset_consistency, 1.0), 6),
        "timeframe_consistency": round(min(timeframe_consistency, 1.0), 6),
        "robustness_quality": round(min(robustness_quality, 1.0), 6),
        "diversity_value": round(min(diversity_value, 1.0), 6),
    }
    weights = {
        "evidence_quality": 0.35,
        "cross_asset_consistency": 0.20,
        "timeframe_consistency": 0.15,
        "robustness_quality": 0.20,
        "diversity_value": 0.10,
    }
    score = sum(components[key] * weights[key] for key in weights)
    return {"score": round(score, 6), "components": components, "weights": weights}


def _recommendation_from_score(score: float, outcome_summary: dict) -> str:
    counts = outcome_summary["outcome_counts"]
    if counts["baseline_failed"] > counts["baseline_passed"]:
        return "revise_mutation_recipe"
    if counts["robustness_failed"] or counts["final_holdout_failed"]:
        return "revise_thesis"
    if score >= 0.50:
        return "request_manual_baseline_review"
    if counts["insufficient_evidence"]:
        return "run_more_prebaseline_research"
    return "defer"


def _extract_records(inputs: dict) -> list[dict]:
    if isinstance(inputs.get("records"), list):
        return [record for record in inputs["records"] if isinstance(record, dict)]
    records: list[dict] = []
    for key in [
        "campaign_plan",
        "generated_hypothesis_batch",
        "similarity_report",
        "validation_reports",
        "generated_baseline_reviews",
        "generated_robustness_reviews",
        "generated_final_holdout_reviews",
        "candidate_decision_packets",
    ]:
        value = inputs.get(key)
        if isinstance(value, dict):
            if isinstance(value.get("planned_specs"), list):
                records.extend(item for item in value["planned_specs"] if isinstance(item, dict))
            elif isinstance(value.get("hypotheses"), list):
                records.extend(item for item in value["hypotheses"] if isinstance(item, dict))
            else:
                records.append(value)
        elif isinstance(value, list):
            records.extend(item for item in value if isinstance(item, dict))
    return records


def _campaign_id_from_inputs(inputs: dict) -> str | None:
    plan = inputs.get("campaign_plan")
    if isinstance(plan, dict):
        return plan.get("campaign_id")
    return None


def _group(records: list[dict], key_fn) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for record in records:
        key = key_fn(record) or "unknown"
        grouped.setdefault(str(key), []).append(record)
    return grouped


def _first(record: dict, *keys: str, default: str = "unknown") -> Any:
    for key in keys:
        value = record.get(key)
        if value:
            return value
    lineage = record.get("lineage")
    if isinstance(lineage, dict):
        for key in keys:
            value = lineage.get(key)
            if value:
                return value
    return default


def _status(record: dict, *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str):
            return value.lower()
    return ""


def _analysis_authority() -> dict[str, bool]:
    return {
        "advisory_only": True,
        "baseline_decision_authority": False,
        "final_holdout_decision_authority": False,
        "state_transition_authority": False,
        "queue_execution_authority": False,
        "live_trading_authority": False,
    }
