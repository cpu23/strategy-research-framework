from __future__ import annotations

import itertools
import re
from pathlib import Path
from typing import Any

from .edge_library import validate_edge_thesis
from .hashing import stable_hash
from .schemas import SchemaValidationError, load_yaml


MUTATION_RECIPE_SCHEMA = "mutation_recipe_v1"
GENERATED_HYPOTHESIS_BATCH_SCHEMA = "generated_hypothesis_batch_v1"
HYPOTHESIS_SCREENING_REPORT_SCHEMA = "hypothesis_screening_report_v1"

SIMILARITY_THRESHOLD = 0.72


def load_mutation_recipe(path: Path) -> dict[str, Any]:
    return validate_mutation_recipe(load_yaml(path))


def validate_mutation_recipe(recipe: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(recipe, dict):
        raise SchemaValidationError("mutation_recipe must be a mapping")
    for key in ["recipe_id", "edge_id", "mutation_budget", "axes", "constraints"]:
        if key not in recipe:
            raise SchemaValidationError(f"mutation_recipe.{key} is required")

    _require_prefixed(recipe, "recipe_id", "MUT_")
    _require_prefixed(recipe, "edge_id", "EDGE_")

    budget = recipe["mutation_budget"]
    if not isinstance(budget, dict):
        raise SchemaValidationError("mutation_recipe.mutation_budget must be a mapping")
    for key in ["max_hypotheses", "max_per_similarity_cluster"]:
        value = budget.get(key)
        if not isinstance(value, int) or value <= 0:
            raise SchemaValidationError(f"mutation_recipe.mutation_budget.{key} must be a positive integer")
    min_diversity = budget.get("min_family_diversity")
    if min_diversity is not None and (not isinstance(min_diversity, int) or min_diversity <= 0):
        raise SchemaValidationError("mutation_recipe.mutation_budget.min_family_diversity must be a positive integer")

    axes = recipe["axes"]
    if not isinstance(axes, dict) or not axes:
        raise SchemaValidationError("mutation_recipe.axes must be a non-empty mapping")
    for axis_name, axis_def in axes.items():
        if not isinstance(axis_name, str) or not axis_name:
            raise SchemaValidationError("mutation_recipe.axes keys must be non-empty strings")
        if not isinstance(axis_def, dict):
            raise SchemaValidationError(f"mutation_recipe.axes.{axis_name} must be a mapping")
        values = axis_def.get("allowed_values")
        if not isinstance(values, list) or not values:
            raise SchemaValidationError(f"mutation_recipe.axes.{axis_name}.allowed_values must be a non-empty list")
        if any(isinstance(value, (dict, list)) or value in (None, "") for value in values):
            raise SchemaValidationError(f"mutation_recipe.axes.{axis_name}.allowed_values entries must be scalar values")

    if not isinstance(recipe["constraints"], list):
        raise SchemaValidationError("mutation_recipe.constraints must be a list")
    return recipe


def generate_mutation_signatures(edge_thesis: dict[str, Any], recipe: dict[str, Any]) -> list[dict[str, Any]]:
    thesis = validate_edge_thesis(dict(edge_thesis))
    recipe = validate_mutation_recipe(dict(recipe))
    if recipe["edge_id"] != thesis["edge_id"]:
        raise SchemaValidationError("mutation_recipe.edge_id must match edge_thesis.edge_id")

    axes = recipe["axes"]
    axis_names = sorted(axes)
    value_lists = [list(axes[name]["allowed_values"]) for name in axis_names]
    max_hypotheses = recipe["mutation_budget"]["max_hypotheses"]
    signatures: list[dict[str, Any]] = []
    for combo in itertools.product(*value_lists):
        axis_values = dict(zip(axis_names, combo))
        payload = {
            "edge_id": thesis["edge_id"],
            "recipe_id": recipe["recipe_id"],
            "axis_values": axis_values,
        }
        signatures.append(
            {
                "edge_id": thesis["edge_id"],
                "recipe_id": recipe["recipe_id"],
                "axis_values": axis_values,
                "mutation_signature": stable_hash(payload)[:20],
            }
        )
        if len(signatures) >= max_hypotheses:
            break
    return signatures


def build_generated_hypothesis(edge_thesis: dict[str, Any], recipe: dict[str, Any], signature: dict[str, Any]) -> dict[str, Any]:
    thesis = validate_edge_thesis(dict(edge_thesis))
    recipe = validate_mutation_recipe(dict(recipe))
    if recipe["edge_id"] != thesis["edge_id"] or signature.get("edge_id") != thesis["edge_id"]:
        raise SchemaValidationError("hypothesis lineage must use the same edge_id")
    if signature.get("recipe_id") != recipe["recipe_id"]:
        raise SchemaValidationError("hypothesis signature recipe_id must match recipe.recipe_id")

    axis_values = dict(signature.get("axis_values") or {})
    mutation_signature = signature.get("mutation_signature") or stable_hash(axis_values)[:20]
    hypothesis_id = f"HYP_GEN_{stable_hash({'edge_id': thesis['edge_id'], 'recipe_id': recipe['recipe_id'], 'signature': mutation_signature})[:16].upper()}"

    entry_bits = _axis_summary(axis_values, ["breakout_trigger", "entry_trigger", "compression_measure", "trend_filter", "session_filter"])
    exit_bits = _axis_summary(axis_values, ["exit_model"])
    stop_bits = _axis_summary(axis_values, ["stop_model", "risk_model"])

    return {
        "hypothesis_id": hypothesis_id,
        "lineage": {
            "source_ids": list(thesis["source_ids"]),
            "edge_id": thesis["edge_id"],
            "recipe_id": recipe["recipe_id"],
            "mutation_signature": mutation_signature,
        },
        "mechanism": thesis["mechanism"],
        "prediction": thesis["testable_prediction"],
        "strategy_family": thesis["edge_family"],
        "asset_class_applicability": list(thesis["asset_classes"]),
        "candidate_symbols": list(thesis["candidate_symbols"]),
        "candidate_timeframes": list(thesis["candidate_timeframes"]),
        "entry_logic_summary": entry_bits or "entry expression follows thesis mechanism",
        "exit_logic_summary": exit_bits or "exit expression follows recipe constraints",
        "risk_model_summary": stop_bits or "risk model bounded by recipe constraints",
        "invalidation_rule": "Reject if bounded research tests do not support the thesis prediction.",
        "mutation_axes": axis_values,
        "similarity_cluster_id": None,
        "similarity_score_to_cluster_center": None,
        "cluster_rank": None,
        "screening_status": "pending",
        "warning_reasons": [],
    }


def score_hypothesis_similarity(h1: dict[str, Any], h2: dict[str, Any]) -> float:
    weighted_fields = {
        "edge_id": 2.0,
        "strategy_family": 2.0,
        "candidate_symbols": 1.0,
        "candidate_timeframes": 1.0,
        "entry_logic_summary": 1.5,
        "exit_logic_summary": 1.2,
        "risk_model_summary": 1.2,
        "mutation_axes": 2.0,
    }
    total_weight = 0.0
    score = 0.0
    for field, weight in weighted_fields.items():
        left = _tokens(h1.get(field))
        right = _tokens(h2.get(field))
        if not left and not right:
            continue
        total_weight += weight
        if left or right:
            score += weight * (len(left & right) / len(left | right))
    if total_weight == 0:
        return 0.0
    return round(score / total_weight, 6)


def assign_similarity_clusters(hypotheses: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
    representatives: list[dict[str, Any]] = []
    clustered: list[dict[str, Any]] = []
    cluster_counts: dict[str, int] = {}

    for hypothesis in sorted(hypotheses, key=lambda item: item["hypothesis_id"]):
        assigned_id: str | None = None
        assigned_score = 0.0
        for index, representative in enumerate(representatives, 1):
            score = score_hypothesis_similarity(hypothesis, representative)
            if score >= threshold:
                assigned_id = f"SIM_{index:03d}"
                assigned_score = score
                break
        if assigned_id is None:
            representatives.append(hypothesis)
            assigned_id = f"SIM_{len(representatives):03d}"
            assigned_score = 1.0
        cluster_counts[assigned_id] = cluster_counts.get(assigned_id, 0) + 1
        item = dict(hypothesis)
        item["similarity_cluster_id"] = assigned_id
        item["similarity_score_to_cluster_center"] = round(assigned_score, 6)
        item["cluster_rank"] = cluster_counts[assigned_id]
        clustered.append(item)
    return clustered


def apply_similarity_budget(hypotheses: list[dict[str, Any]], max_per_cluster: int) -> dict[str, Any]:
    if not isinstance(max_per_cluster, int) or max_per_cluster <= 0:
        raise SchemaValidationError("max_per_cluster must be a positive integer")

    accepted: list[dict[str, Any]] = []
    capped: list[dict[str, Any]] = []
    cluster_counts: dict[str, int] = {}
    for hypothesis in sorted(hypotheses, key=lambda item: (item.get("similarity_cluster_id") or "", item.get("cluster_rank") or 0, item["hypothesis_id"])):
        cluster_id = hypothesis.get("similarity_cluster_id") or "SIM_UNKNOWN"
        cluster_counts[cluster_id] = cluster_counts.get(cluster_id, 0) + 1
        item = dict(hypothesis)
        if cluster_counts[cluster_id] <= max_per_cluster:
            item["screening_status"] = "accepted"
            accepted.append(item)
        else:
            item["screening_status"] = "capped_by_similarity_budget"
            item["warning_reasons"] = list(item.get("warning_reasons", [])) + ["similarity_cluster_budget_exceeded"]
            capped.append(item)

    return {
        "accepted": accepted,
        "rejected": [],
        "capped_by_similarity_budget": capped,
        "screening_summary": {
            "accepted": len(accepted),
            "rejected": 0,
            "capped_by_similarity_budget": len(capped),
        },
    }


def build_generated_hypothesis_batch(edge_thesis: dict[str, Any], recipe: dict[str, Any]) -> dict[str, Any]:
    thesis = validate_edge_thesis(dict(edge_thesis))
    recipe = validate_mutation_recipe(dict(recipe))
    signatures = generate_mutation_signatures(thesis, recipe)
    hypotheses = [build_generated_hypothesis(thesis, recipe, signature) for signature in signatures]
    clustered = assign_similarity_clusters(hypotheses, SIMILARITY_THRESHOLD)
    budgeted = apply_similarity_budget(clustered, recipe["mutation_budget"]["max_per_similarity_cluster"])
    all_hypotheses = budgeted["accepted"] + budgeted["capped_by_similarity_budget"]
    batch_id = f"HBATCH_{stable_hash({'edge_id': thesis['edge_id'], 'recipe_id': recipe['recipe_id'], 'hypotheses': [h['hypothesis_id'] for h in all_hypotheses]})[:16].upper()}"
    return {
        "schema_version": GENERATED_HYPOTHESIS_BATCH_SCHEMA,
        "artifact_type": "generated_hypothesis_batch",
        "batch_id": batch_id,
        "edge_id": thesis["edge_id"],
        "recipe_id": recipe["recipe_id"],
        "hypotheses": all_hypotheses,
        "accepted_hypotheses": budgeted["accepted"],
        "rejected_hypotheses": budgeted["rejected"],
        "capped_by_similarity_budget": budgeted["capped_by_similarity_budget"],
        "screening_summary": budgeted["screening_summary"],
        "authority": {
            "research_planning_only": True,
            "code_generation_authority": False,
            "baseline_decision_authority": False,
            "final_holdout_decision_authority": False,
            "state_transition_authority": False,
            "live_trading_authority": False,
        },
    }


def build_hypothesis_screening_report(batch: dict[str, Any]) -> dict[str, Any]:
    hypotheses = list(batch.get("hypotheses", []))
    accepted = [item for item in hypotheses if item.get("screening_status") == "accepted"]
    capped = [item for item in hypotheses if item.get("screening_status") == "capped_by_similarity_budget"]
    rejected = list(batch.get("rejected_hypotheses", []))
    return {
        "schema_version": HYPOTHESIS_SCREENING_REPORT_SCHEMA,
        "artifact_type": "hypothesis_screening_report",
        "batch_id": batch.get("batch_id"),
        "edge_id": batch.get("edge_id"),
        "recipe_id": batch.get("recipe_id"),
        "accepted_hypotheses": [item["hypothesis_id"] for item in accepted],
        "rejected_hypotheses": [item.get("hypothesis_id") for item in rejected],
        "capped_by_similarity_hypotheses": [item["hypothesis_id"] for item in capped],
        "warning_reasons": sorted({reason for item in capped + rejected for reason in item.get("warning_reasons", [])}),
        "lineage_summary": {
            "source_ids": sorted({src for item in hypotheses for src in item.get("lineage", {}).get("source_ids", [])}),
            "edge_id": batch.get("edge_id"),
            "recipe_id": batch.get("recipe_id"),
        },
        "authority": dict(batch.get("authority", {})),
    }


def _require_prefixed(data: dict[str, Any], key: str, prefix: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.startswith(prefix):
        raise SchemaValidationError(f"mutation_recipe.{key} must use {prefix} prefix")
    return value


def _axis_summary(axis_values: dict[str, Any], names: list[str]) -> str:
    parts = [f"{name}={axis_values[name]}" for name in names if name in axis_values]
    return "; ".join(parts)


def _tokens(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, dict):
        tokens: set[str] = set()
        for key, val in value.items():
            tokens |= _tokens(key)
            tokens |= _tokens(val)
        return tokens
    if isinstance(value, (list, tuple, set)):
        tokens = set()
        for item in value:
            tokens |= _tokens(item)
        return tokens
    return {part for part in re.split(r"[^a-zA-Z0-9_]+", str(value).lower()) if part}
