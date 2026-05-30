from __future__ import annotations

import re
from typing import Any, Callable

from .hashing import stable_hash


DEFAULT_THRESHOLDS = {
    "duplicate": 0.95,
    "near_variant": 0.75,
    "same_family_different_expression": 0.50,
}

DEFAULT_DIVERSITY_BUDGETS = {
    "max_duplicates": 1,
    "max_near_variants_per_cluster": 4,
    "max_same_family_per_campaign": 20,
    "min_distinct_edge_families": 3,
}


def normalize_similarity_tokens(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, dict):
        tokens: set[str] = set()
        for key in sorted(value):
            tokens |= normalize_similarity_tokens(key)
            tokens |= normalize_similarity_tokens(value[key])
        return tokens
    if isinstance(value, (list, tuple, set, frozenset)):
        tokens = set()
        for item in value:
            tokens |= normalize_similarity_tokens(item)
        return tokens
    return {part for part in re.split(r"[^a-zA-Z0-9_]+", str(value).lower()) if part}


def weighted_jaccard(a: dict, b: dict, weights: dict) -> float:
    score = 0.0
    total = 0.0
    for field, weight in weights.items():
        left = normalize_similarity_tokens(a.get(field))
        right = normalize_similarity_tokens(b.get(field))
        if not left and not right:
            continue
        total += float(weight)
        if left or right:
            score += float(weight) * (len(left & right) / len(left | right))
    if total == 0:
        return 0.0
    return round(score / total, 6)


def score_edge_thesis_similarity(t1: dict, t2: dict) -> dict:
    weights = {
        "edge_family": 2.0,
        "mechanism": 1.5,
        "testable_prediction": 1.5,
        "mutation_axes": 1.0,
        "asset_classes": 1.0,
        "market_regimes": 1.0,
        "failure_modes": 1.0,
    }
    score = weighted_jaccard(t1, t2, weights)
    return {
        "item_type": "edge_thesis",
        "left_id": _item_id(t1),
        "right_id": _item_id(t2),
        "score": score,
        "classification": classify_similarity(score, DEFAULT_THRESHOLDS),
        "recommendation": _recommendation(classify_similarity(score, DEFAULT_THRESHOLDS)),
    }


def score_hypothesis_similarity(h1: dict, h2: dict) -> dict:
    weights = {
        "edge_id": 2.0,
        "strategy_family": 2.0,
        "entry_logic_summary": 1.5,
        "exit_logic_summary": 1.2,
        "risk_model_summary": 1.2,
        "filters": 1.0,
        "candidate_symbols": 1.0,
        "candidate_timeframes": 1.0,
        "mutation_axes": 2.0,
        "lineage": 1.0,
    }
    score = weighted_jaccard(h1, h2, weights)
    return {
        "item_type": "hypothesis",
        "left_id": _item_id(h1),
        "right_id": _item_id(h2),
        "score": score,
        "classification": classify_similarity(score, DEFAULT_THRESHOLDS),
        "recommendation": _recommendation(classify_similarity(score, DEFAULT_THRESHOLDS)),
    }


def classify_similarity(score: float, thresholds: dict) -> str:
    if score is None:
        return "unknown"
    duplicate = thresholds.get("duplicate", DEFAULT_THRESHOLDS["duplicate"])
    near = thresholds.get("near_variant", DEFAULT_THRESHOLDS["near_variant"])
    same = thresholds.get(
        "same_family_different_expression",
        DEFAULT_THRESHOLDS["same_family_different_expression"],
    )
    if score >= duplicate:
        return "duplicate"
    if score >= near:
        return "near_variant"
    if score >= same:
        return "same_family_different_expression"
    return "different_family"


def cluster_items_by_similarity(items: list[dict], scorer: Callable[[dict, dict], dict], thresholds: dict) -> dict:
    cluster_threshold = thresholds.get("near_variant", DEFAULT_THRESHOLDS["near_variant"])
    representatives: list[dict] = []
    clusters: list[dict] = []

    for item in sorted(items, key=_item_id):
        assigned_index: int | None = None
        assigned_score = 1.0
        assigned_classification = "duplicate"
        for index, representative in enumerate(representatives):
            result = scorer(item, representative)
            if result["score"] >= cluster_threshold:
                assigned_index = index
                assigned_score = result["score"]
                assigned_classification = classify_similarity(result["score"], thresholds)
                break
        if assigned_index is None:
            representatives.append(item)
            cluster_id = f"SIMC_{stable_hash({'representative': _item_id(item)})[:12].upper()}"
            clusters.append(
                {
                    "cluster_id": cluster_id,
                    "representative_item_id": _item_id(item),
                    "representative_item": item,
                    "members": [],
                }
            )
            assigned_index = len(clusters) - 1
        member = {
            "item_id": _item_id(item),
            "item": item,
            "score_to_representative": assigned_score,
            "classification_to_representative": assigned_classification,
        }
        clusters[assigned_index]["members"].append(member)

    for cluster in clusters:
        cluster["size"] = len(cluster["members"])
        cluster["member_ids"] = [member["item_id"] for member in cluster["members"]]
    return {
        "artifact_type": "similarity_cluster_report",
        "thresholds": dict(thresholds),
        "clusters": clusters,
        "cluster_count": len(clusters),
    }


def build_similarity_report(items: list[dict], item_type: str, thresholds: dict) -> dict:
    scorer = _scorer_for(item_type)
    ordered = sorted(items, key=_item_id)
    comparisons: list[dict] = []
    for left_index, left in enumerate(ordered):
        for right in ordered[left_index + 1 :]:
            result = scorer(left, right)
            result["classification"] = classify_similarity(result["score"], thresholds)
            result["recommendation"] = _recommendation(result["classification"])
            comparisons.append(result)
    clusters = cluster_items_by_similarity(ordered, scorer, thresholds)
    return {
        "artifact_type": "generated_strategy_similarity_report",
        "item_type": item_type,
        "item_count": len(items),
        "thresholds": dict(thresholds),
        "comparisons": comparisons,
        "clusters": clusters["clusters"],
        "behavior_similarity_schema": {
            "status": "placeholder",
            "future_fields": ["trade_overlap", "return_correlation", "drawdown_overlap"],
        },
        "authority": _advisory_authority(),
    }


def build_diversity_report(similarity_report: dict, budgets: dict) -> dict:
    budgets = {**DEFAULT_DIVERSITY_BUDGETS, **(budgets or {})}
    decisions: list[dict] = []
    family_counts: dict[str, int] = {}

    for cluster in similarity_report.get("clusters", []):
        near_kept = 0
        duplicates_kept = 0
        for rank, member in enumerate(cluster.get("members", []), 1):
            item = member["item"]
            family = item.get("edge_family") or item.get("strategy_family") or "unknown"
            family_counts[family] = family_counts.get(family, 0) + 1
            classification = member["classification_to_representative"]
            status = "kept"
            reason = "representative"
            if classification == "duplicate":
                duplicates_kept += 1
                if duplicates_kept > budgets["max_duplicates"]:
                    status = "rejected_duplicate"
                    reason = "duplicate_budget_exceeded"
            elif classification == "near_variant":
                near_kept += 1
                if near_kept <= budgets["max_near_variants_per_cluster"]:
                    status = "kept_with_cap"
                    reason = "near_variant_allowed_within_cap"
                else:
                    status = "deprioritized"
                    reason = "near_variant_budget_exceeded"
            elif classification == "same_family_different_expression":
                if family_counts[family] > budgets["max_same_family_per_campaign"]:
                    status = "deprioritized"
                    reason = "same_family_campaign_budget_exceeded"
                else:
                    status = "manual_review"
                    reason = "same_family_different_expression"
            elif classification == "different_family":
                status = "kept"
                reason = "diversifying_family"
            else:
                status = "manual_review"
                reason = "unknown_similarity"
            decisions.append(
                {
                    "item_id": member["item_id"],
                    "cluster_id": cluster["cluster_id"],
                    "cluster_rank": rank,
                    "classification": classification,
                    "decision": status,
                    "reason": reason,
                }
            )

    distinct_families = len([family for family in family_counts if family != "unknown"])
    warnings: list[str] = []
    if distinct_families < budgets["min_distinct_edge_families"]:
        warnings.append("min_distinct_edge_families_not_met")

    return {
        "artifact_type": "hypothesis_diversity_report",
        "item_type": similarity_report.get("item_type"),
        "budgets": budgets,
        "decisions": decisions,
        "summary": {
            "kept": _count_decisions(decisions, "kept"),
            "kept_with_cap": _count_decisions(decisions, "kept_with_cap"),
            "deprioritized": _count_decisions(decisions, "deprioritized"),
            "rejected_duplicate": _count_decisions(decisions, "rejected_duplicate"),
            "manual_review": _count_decisions(decisions, "manual_review"),
        },
        "warnings": warnings,
        "authority": _advisory_authority(),
    }


def _scorer_for(item_type: str) -> Callable[[dict, dict], dict]:
    if item_type == "edge_thesis":
        return score_edge_thesis_similarity
    if item_type == "hypothesis":
        return score_hypothesis_similarity
    if item_type == "spec":
        return _score_spec_shape_similarity
    return _score_generic_similarity


def _score_spec_shape_similarity(s1: dict, s2: dict) -> dict:
    weights = {
        "indicator_list": 1.5,
        "parameter_names": 1.5,
        "entry_logic_summary": 1.5,
        "exit_logic_summary": 1.2,
        "risk_model_summary": 1.2,
    }
    score = weighted_jaccard(s1, s2, weights)
    return {
        "item_type": "spec",
        "left_id": _item_id(s1),
        "right_id": _item_id(s2),
        "score": score,
        "classification": classify_similarity(score, DEFAULT_THRESHOLDS),
        "recommendation": _recommendation(classify_similarity(score, DEFAULT_THRESHOLDS)),
    }


def _score_generic_similarity(i1: dict, i2: dict) -> dict:
    left = normalize_similarity_tokens(i1)
    right = normalize_similarity_tokens(i2)
    score = 0.0 if not left and not right else len(left & right) / len(left | right)
    score = round(score, 6)
    return {
        "item_type": "unknown",
        "left_id": _item_id(i1),
        "right_id": _item_id(i2),
        "score": score,
        "classification": classify_similarity(score, DEFAULT_THRESHOLDS),
        "recommendation": _recommendation(classify_similarity(score, DEFAULT_THRESHOLDS)),
    }


def _item_id(item: dict) -> str:
    for key in ["edge_id", "hypothesis_id", "spec_id", "strategy_id", "id"]:
        value = item.get(key)
        if value:
            return str(value)
    lineage = item.get("lineage")
    if isinstance(lineage, dict) and lineage.get("mutation_signature"):
        return f"HYP_{lineage['mutation_signature']}"
    return f"ITEM_{stable_hash(item)[:12].upper()}"


def _recommendation(classification: str) -> str:
    return {
        "duplicate": "reject_duplicate",
        "near_variant": "keep_with_cap",
        "same_family_different_expression": "keep",
        "different_family": "keep",
        "unknown": "manual_review",
    }.get(classification, "manual_review")


def _count_decisions(decisions: list[dict], status: str) -> int:
    return sum(1 for item in decisions if item.get("decision") == status)


def _advisory_authority() -> dict[str, bool]:
    return {
        "advisory_only": True,
        "pass_fail_gate": False,
        "baseline_decision_authority": False,
        "final_holdout_decision_authority": False,
        "state_transition_authority": False,
        "live_trading_authority": False,
    }
