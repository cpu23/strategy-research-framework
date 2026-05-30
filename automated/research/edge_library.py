from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .hashing import stable_hash
from .schemas import SchemaValidationError, load_yaml


RESEARCH_SOURCE_RECORD_SCHEMA = "research_source_record_v1"
EDGE_THESIS_SCHEMA = "edge_thesis_v1"
EDGE_THESIS_EXTRACTION_REPORT_SCHEMA = "edge_thesis_extraction_report_v1"

SOURCE_TYPES = {
    "academic_paper",
    "case_study",
    "practitioner_writeup",
    "internal_observation",
    "post_trade_review",
    "manual_note",
}

EXTRACTION_STATUSES = {"pending", "extracted", "rejected"}

EDGE_FAMILIES = {
    "trend",
    "mean_reversion",
    "volatility_expansion",
    "liquidity",
    "session",
    "carry",
    "cross_asset",
    "news_drift",
    "calendar",
    "microstructure",
    "other",
}

EVIDENCE_STRENGTHS = {"weak", "medium", "strong", "unknown"}
EDGE_STATUSES = {"active", "archived", "rejected"}

MT5_TIMEFRAME_RE = re.compile(r"^(M[1-9][0-9]*|H[1-9][0-9]*|D1|W1|MN1)$")

SOURCE_REQUIRED_FIELDS = {
    "source_id",
    "source_type",
    "title",
    "authors",
    "published_date",
    "url_or_reference",
    "summary",
    "markets_discussed",
    "timeframes_discussed",
    "key_claims",
    "limitations",
    "extraction_status",
    "created_at",
}

EDGE_REQUIRED_FIELDS = {
    "edge_id",
    "source_ids",
    "edge_family",
    "mechanism",
    "testable_prediction",
    "asset_classes",
    "candidate_symbols",
    "candidate_timeframes",
    "market_regimes",
    "mutation_axes",
    "implementation_constraints",
    "failure_modes",
    "risk_warnings",
    "evidence_strength",
    "status",
    "created_at",
}


def load_research_source(path: Path) -> dict[str, Any]:
    return validate_research_source(load_yaml(path))


def validate_research_source(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise SchemaValidationError("research_source must be a mapping")
    _require_fields(record, SOURCE_REQUIRED_FIELDS, "research_source")

    _require_prefixed_string(record, "source_id", "SRC_", "research_source")
    _require_enum(record, "source_type", SOURCE_TYPES, "research_source")
    _require_non_empty_string(record, "title", "research_source")
    _require_list(record, "authors", "research_source")
    _require_nullable_scalar(record, "published_date", "research_source")
    _require_non_empty_string(record, "url_or_reference", "research_source")
    _require_non_empty_string(record, "summary", "research_source")
    for key in ["markets_discussed", "timeframes_discussed", "key_claims", "limitations"]:
        _require_list(record, key, "research_source")
    _require_enum(record, "extraction_status", EXTRACTION_STATUSES, "research_source")
    _require_non_empty_string(record, "created_at", "research_source")

    optional_list = record.get("tags", [])
    if optional_list is not None and not isinstance(optional_list, list):
        raise SchemaValidationError("research_source.tags must be a list when present")
    return record


def load_edge_thesis(path: Path) -> dict[str, Any]:
    return validate_edge_thesis(load_yaml(path))


def validate_edge_thesis(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise SchemaValidationError("edge_thesis must be a mapping")
    _require_fields(record, EDGE_REQUIRED_FIELDS, "edge_thesis")

    _require_prefixed_string(record, "edge_id", "EDGE_", "edge_thesis")
    source_ids = _require_list(record, "source_ids", "edge_thesis")
    for source_id in source_ids:
        if not isinstance(source_id, str) or not source_id.startswith("SRC_"):
            raise SchemaValidationError("edge_thesis.source_ids entries must use SRC_ prefix")
    _require_enum(record, "edge_family", EDGE_FAMILIES, "edge_thesis")
    _require_non_empty_string(record, "mechanism", "edge_thesis")
    _require_non_empty_string(record, "testable_prediction", "edge_thesis")
    for key in [
        "asset_classes",
        "candidate_symbols",
        "candidate_timeframes",
        "market_regimes",
        "mutation_axes",
        "implementation_constraints",
        "failure_modes",
        "risk_warnings",
    ]:
        _require_list(record, key, "edge_thesis")
    for timeframe in record["candidate_timeframes"]:
        if not isinstance(timeframe, str) or not MT5_TIMEFRAME_RE.match(timeframe):
            raise SchemaValidationError(f"edge_thesis.candidate_timeframes contains invalid MT5 timeframe: {timeframe!r}")
    _require_enum(record, "evidence_strength", EVIDENCE_STRENGTHS, "edge_thesis")
    _require_enum(record, "status", EDGE_STATUSES, "edge_thesis")
    _require_non_empty_string(record, "created_at", "edge_thesis")
    return record


def edge_thesis_id_from_content(thesis: dict[str, Any]) -> str:
    payload = {
        "source_ids": sorted(thesis.get("source_ids", [])),
        "edge_family": thesis.get("edge_family"),
        "mechanism": thesis.get("mechanism"),
        "testable_prediction": thesis.get("testable_prediction"),
        "mutation_axes": thesis.get("mutation_axes", []),
    }
    return f"EDGE_{stable_hash(payload)[:16].upper()}"


def build_edge_thesis_extraction_report(source: dict[str, Any], theses: list[dict[str, Any]]) -> dict[str, Any]:
    source_record = validate_research_source(dict(source))
    validated_theses = [validate_edge_thesis(dict(thesis)) for thesis in theses]

    warnings: list[str] = []
    source_id = source_record["source_id"]
    for thesis in validated_theses:
        if source_id not in thesis["source_ids"]:
            warnings.append(f"thesis {thesis['edge_id']} does not reference source {source_id}")

    return {
        "schema_version": EDGE_THESIS_EXTRACTION_REPORT_SCHEMA,
        "artifact_type": "edge_thesis_extraction_report",
        "source_id": source_id,
        "source_digest": stable_hash(source_record),
        "extracted_edge_ids": [thesis["edge_id"] for thesis in validated_theses],
        "edge_count": len(validated_theses),
        "warnings": warnings,
        "authority": {
            "research_library_only": True,
            "may_generate_strategy_implementation": False,
            "baseline_decision_authority": False,
            "final_holdout_decision_authority": False,
            "state_transition_authority": False,
            "may_promote_to_live": False,
        },
    }


def _require_fields(record: dict[str, Any], required: set[str], context: str) -> None:
    missing = sorted(key for key in required if key not in record)
    if missing:
        raise SchemaValidationError(f"{context} missing required fields: {missing}")


def _require_non_empty_string(record: dict[str, Any], key: str, context: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SchemaValidationError(f"{context}.{key} must be a non-empty string")
    return value


def _require_prefixed_string(record: dict[str, Any], key: str, prefix: str, context: str) -> str:
    value = _require_non_empty_string(record, key, context)
    if not value.startswith(prefix):
        raise SchemaValidationError(f"{context}.{key} must use {prefix} prefix")
    return value


def _require_enum(record: dict[str, Any], key: str, allowed: set[str], context: str) -> str:
    value = _require_non_empty_string(record, key, context)
    if value not in allowed:
        raise SchemaValidationError(f"{context}.{key} must be one of {sorted(allowed)}; got {value!r}")
    return value


def _require_list(record: dict[str, Any], key: str, context: str) -> list[Any]:
    value = record.get(key)
    if not isinstance(value, list):
        raise SchemaValidationError(f"{context}.{key} must be a list")
    return value


def _require_nullable_scalar(record: dict[str, Any], key: str, context: str) -> Any:
    value = record.get(key)
    if isinstance(value, (dict, list)):
        raise SchemaValidationError(f"{context}.{key} must be a scalar or null")
    return value
