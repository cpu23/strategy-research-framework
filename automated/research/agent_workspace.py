from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .contracts import ARTIFACT_TYPES
from .hashing import file_sha256, stable_hash
from .schemas import REPO_ROOT, STRATEGIES_ROOT, SchemaValidationError, load_yaml


AGENT_WORKSPACE_ROOT = REPO_ROOT / "automated" / "agent_workspace"
INBOX_DIR = AGENT_WORKSPACE_ROOT / "inbox"
WORKING_DIR = AGENT_WORKSPACE_ROOT / "working"
OUTBOX_DIR = AGENT_WORKSPACE_ROOT / "outbox"
REJECTED_DIR = AGENT_WORKSPACE_ROOT / "rejected"
LOGS_DIR = AGENT_WORKSPACE_ROOT / "logs"

AGENT_WORKSPACE_BUNDLE_SCHEMA = "agent_workspace_bundle_v1"
AGENT_WORKSPACE_VALIDATION_REPORT_SCHEMA = "agent_workspace_validation_report_v1"

FORBIDDEN_AUTHORITY_KEYS = {
    "approval_status",
    "approval_usage",
    "baseline_approval",
    "final_holdout_approval",
    "lifecycle_proposal",
    "lifecycle_transition",
    "state_transition",
    "production_authority",
    "live_trading_authority",
}

FORBIDDEN_VALUES = {
    "promote_to_production",
    "production_candidate",
    "live_trading_candidate",
}

SAFE_RECOMMENDATIONS = {
    "reject",
    "revise_thesis",
    "revise_mutation_recipe",
    "run_more_prebaseline_research",
    "request_manual_review",
    "request_manual_baseline_review",
    "request_manual_final_holdout_review",
    "defer",
    "keep",
    "keep_with_cap",
    "deprioritize",
    "manual_review",
}

ROLE_ARTIFACT_PERMISSIONS = {
    "research_librarian": {
        "research_source_record",
        "edge_thesis",
        "edge_thesis_extraction_report",
    },
    "experiment_designer": {
        "mutation_recipe",
        "generated_hypothesis_batch",
        "hypothesis_screening_report",
        "generated_research_campaign_plan",
        "campaign_asset_timeframe_matrix",
        "campaign_budget_report",
        "ranked_manual_baseline_review_queue",
    },
    "statistical_reviewer": {
        "generated_strategy_similarity_report",
        "hypothesis_diversity_report",
        "similarity_cluster_report",
        "generated_research_campaign_report",
        "edge_family_meta_analysis",
        "asset_timeframe_effectiveness_report",
        "similarity_cluster_effectiveness_report",
    },
    "portfolio_reviewer": {
        "generated_strategy_similarity_report",
        "hypothesis_diversity_report",
        "similarity_cluster_report",
        "generated_research_campaign_report",
        "edge_family_meta_analysis",
        "asset_timeframe_effectiveness_report",
        "similarity_cluster_effectiveness_report",
    },
    "red_team_reviewer": {
        "generated_strategy_similarity_report",
        "hypothesis_diversity_report",
        "similarity_cluster_report",
        "generated_research_campaign_report",
    },
}

CANONICAL_IMPORT_DIRS = {
    "research_source_record": REPO_ROOT / "automated" / "specs" / "research_sources",
    "edge_thesis": REPO_ROOT / "automated" / "specs" / "edge_theses",
    "mutation_recipe": REPO_ROOT / "automated" / "specs" / "mutation_recipes",
    "generated_research_campaign_plan": REPO_ROOT / "automated" / "specs" / "research_campaigns",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_agent_bundle_manifest(bundle_path: str | Path) -> dict[str, Any]:
    manifest_path = Path(bundle_path) / "manifest.yaml"
    if not manifest_path.is_file():
        raise SchemaValidationError(f"agent bundle manifest not found: {manifest_path}")
    return load_yaml(manifest_path)


def validate_agent_output_bundle(bundle_path: str | Path) -> dict[str, Any]:
    bundle = Path(bundle_path)
    manifest = load_agent_bundle_manifest(bundle)
    errors: list[str] = []
    warnings: list[str] = []
    accepted_artifacts: list[dict[str, Any]] = []
    rejected_artifacts: list[dict[str, Any]] = []

    bundle_id = manifest.get("bundle_id")
    role = manifest.get("agent_role")
    if manifest.get("schema_version") != AGENT_WORKSPACE_BUNDLE_SCHEMA:
        errors.append("manifest.schema_version must be agent_workspace_bundle_v1")
    if not isinstance(bundle_id, str) or not bundle_id.startswith("AWB_"):
        errors.append("manifest.bundle_id must use AWB_ prefix")
    if role not in ROLE_ARTIFACT_PERMISSIONS:
        errors.append(f"manifest.agent_role is not allowed for workspace import: {role!r}")
    if _has_forbidden_authority(manifest):
        errors.append("manifest contains forbidden authority fields")
    if _has_forbidden_allowed_recommendation(manifest):
        errors.append("manifest allowed recommendations contain forbidden values")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("manifest.artifacts must be a non-empty list")
        artifacts = []

    for index, artifact in enumerate(artifacts):
        result = _validate_artifact_entry(bundle, role, artifact, index)
        if result["status"] == "accepted":
            accepted_artifacts.append(result)
        else:
            rejected_artifacts.append(result)
            errors.extend(result["errors"])

    status = "accepted" if not errors else "rejected"
    return {
        "schema_version": AGENT_WORKSPACE_VALIDATION_REPORT_SCHEMA,
        "artifact_type": "agent_workspace_validation_report",
        "bundle_id": bundle_id,
        "agent_role": role,
        "bundle_path": str(bundle),
        "status": status,
        "accepted_artifacts": accepted_artifacts if status == "accepted" else [],
        "rejected_artifacts": rejected_artifacts,
        "errors": errors,
        "warnings": warnings,
        "created_at": utc_now(),
        "authority": {
            "workspace_protocol_only": True,
            "execution_authority": False,
            "queue_execution_authority": False,
            "baseline_decision_authority": False,
            "final_holdout_decision_authority": False,
            "state_transition_authority": False,
            "production_authority": False,
            "live_trading_authority": False,
        },
    }


def import_accepted_agent_bundle(bundle_path: str | Path, *, repo_root: str | Path = REPO_ROOT) -> dict[str, Any]:
    report = validate_agent_output_bundle(bundle_path)
    if report["status"] != "accepted":
        raise SchemaValidationError("cannot import rejected agent bundle")
    repo = Path(repo_root).resolve()
    imports: list[dict[str, Any]] = []
    for artifact in report["accepted_artifacts"]:
        artifact_type = artifact["artifact_type"]
        if artifact_type not in CANONICAL_IMPORT_DIRS:
            continue
        canonical_dir = (repo / CANONICAL_IMPORT_DIRS[artifact_type].relative_to(REPO_ROOT)).resolve()
        _reject_if_under_strategies(canonical_dir)
        canonical_dir.mkdir(parents=True, exist_ok=True)
        target_name = _safe_target_name(artifact.get("target_name") or Path(artifact["path"]).name)
        destination = (canonical_dir / target_name).resolve()
        _ensure_under(destination, canonical_dir, "canonical import destination")
        _reject_if_under_strategies(destination)
        shutil.copy2(artifact["path"], destination)
        imports.append(
            {
                "artifact_type": artifact_type,
                "source_path": artifact["path"],
                "canonical_path": str(destination),
                "file_hash": file_sha256(destination),
            }
        )
    report["canonical_imports"] = imports
    return report


def quarantine_rejected_bundle(
    bundle_path: str | Path,
    validation_report: dict[str, Any] | None = None,
    *,
    rejected_dir: str | Path = REJECTED_DIR,
) -> dict[str, Any]:
    bundle = Path(bundle_path)
    report = validation_report or validate_agent_output_bundle(bundle)
    if report.get("status") != "rejected":
        raise SchemaValidationError("only rejected bundles can be quarantined")
    root = Path(rejected_dir)
    root.mkdir(parents=True, exist_ok=True)
    bundle_id = report.get("bundle_id") or f"AWB_UNKNOWN_{stable_hash(str(bundle))[:8].upper()}"
    destination = (root / _safe_target_name(str(bundle_id))).resolve()
    _ensure_under(destination, root.resolve(), "quarantine destination")
    if destination.exists():
        destination = (root / f"{_safe_target_name(str(bundle_id))}_{stable_hash(report)[:8]}").resolve()
    shutil.copytree(bundle, destination)
    report_path = destination / "validation_report.yaml"
    report_path.write_text(yaml.safe_dump(report, sort_keys=False), encoding="utf-8")
    return {
        "bundle_id": bundle_id,
        "status": "quarantined",
        "quarantine_path": str(destination),
        "validation_report_path": str(report_path),
    }


def write_agent_workspace_validation_report(report: dict[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(report, sort_keys=False), encoding="utf-8")
    return path


def _validate_artifact_entry(bundle: Path, role: str | None, artifact: Any, index: int) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(artifact, dict):
        return {"status": "rejected", "index": index, "errors": [f"artifact[{index}] must be a mapping"]}
    artifact_type = artifact.get("artifact_type")
    if artifact_type not in ARTIFACT_TYPES:
        errors.append(f"artifact[{index}].artifact_type is not registered: {artifact_type!r}")
    if role in ROLE_ARTIFACT_PERMISSIONS and artifact_type not in ROLE_ARTIFACT_PERMISSIONS[role]:
        errors.append(f"{role} cannot submit artifact type {artifact_type!r}")
    raw_path = artifact.get("path")
    try:
        artifact_path = _resolve_bundle_path(bundle, raw_path)
    except SchemaValidationError as exc:
        artifact_path = None
        errors.append(str(exc))
    if artifact_path is not None:
        if not artifact_path.is_file():
            errors.append(f"artifact[{index}].path does not exist: {raw_path}")
        if artifact_path.suffix.lower() == ".mq5":
            errors.append("agent workspace artifacts must not be .mq5 files")
        _reject_path_text(raw_path, errors, index)
        if artifact_path.is_file() and artifact_path.suffix.lower() in {".yaml", ".yml"}:
            try:
                artifact_data = load_yaml(artifact_path)
                if _has_forbidden_authority(artifact_data):
                    errors.append(f"artifact[{index}] content contains forbidden authority fields")
                if _has_forbidden_allowed_recommendation(artifact_data):
                    errors.append(f"artifact[{index}] content allowed recommendations contain forbidden values")
            except Exception as exc:
                errors.append(f"artifact[{index}] content is not valid YAML mapping: {exc}")
    if _has_forbidden_authority(artifact):
        errors.append(f"artifact[{index}] contains forbidden authority fields")
    if _has_forbidden_allowed_recommendation(artifact):
        errors.append(f"artifact[{index}] allowed recommendations contain forbidden values")
    target_name = artifact.get("target_name")
    if target_name is not None:
        try:
            _safe_target_name(str(target_name))
        except SchemaValidationError as exc:
            errors.append(str(exc))

    status = "accepted" if not errors else "rejected"
    return {
        "status": status,
        "index": index,
        "artifact_type": artifact_type,
        "path": str(artifact_path) if artifact_path is not None else str(raw_path),
        "target_name": target_name,
        "errors": errors,
        "canonical_importable": artifact_type in CANONICAL_IMPORT_DIRS,
    }


def _resolve_bundle_path(bundle: Path, raw_path: Any) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise SchemaValidationError("artifact.path must be a non-empty string")
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise SchemaValidationError("artifact.path must be relative to the bundle")
    resolved = (bundle / candidate).resolve()
    _ensure_under(resolved, bundle.resolve(), "artifact path")
    return resolved


def _ensure_under(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise SchemaValidationError(f"{label} escapes allowed root: {path}") from exc


def _reject_if_under_strategies(path: Path) -> None:
    try:
        path.resolve().relative_to(STRATEGIES_ROOT.resolve())
    except ValueError:
        return
    raise SchemaValidationError("agent workspace must not write under automated/strategies")


def _reject_path_text(raw_path: Any, errors: list[str], index: int) -> None:
    text = str(raw_path).replace("\\", "/")
    if "automated/strategies" in text:
        errors.append(f"artifact[{index}].path must not target automated/strategies")


def _safe_target_name(value: str) -> str:
    name = Path(value).name
    if name != value or not name or name in {".", ".."}:
        raise SchemaValidationError("target_name must be a filename without directories")
    if name.endswith(".mq5"):
        raise SchemaValidationError("target_name must not be an .mq5 file")
    return name


def _has_forbidden_authority(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in FORBIDDEN_AUTHORITY_KEYS:
                return True
            if _has_forbidden_authority(item):
                return True
    elif isinstance(value, list):
        return any(_has_forbidden_authority(item) for item in value)
    return False


def _has_forbidden_allowed_recommendation(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    allowed = value.get("allowed_recommendations")
    if isinstance(allowed, list):
        if any(item in FORBIDDEN_VALUES or item not in SAFE_RECOMMENDATIONS for item in allowed):
            return True
    return any(_has_forbidden_allowed_recommendation(item) for item in value.values() if isinstance(item, dict))
