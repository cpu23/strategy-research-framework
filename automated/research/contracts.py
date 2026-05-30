from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


LIFECYCLE_STATES = {
    "idea",
    "hypothesis_defined",
    "baseline_testing",
    "robustness_testing",
    "stat_review",
    "portfolio_review",
    "paper_trading",
    "incubation_capital",
    "production",
    "reduced",
    "retired",
    "archived",
}

ALLOWED_LIFECYCLE_TRANSITIONS = {
    "idea": {"hypothesis_defined", "archived"},
    "hypothesis_defined": {"baseline_testing", "archived"},
    "baseline_testing": {"robustness_testing", "archived"},
    "robustness_testing": {"stat_review", "archived"},
    "stat_review": {"portfolio_review", "archived"},
    "portfolio_review": {"paper_trading", "archived"},
    "paper_trading": {"incubation_capital", "archived"},
    "incubation_capital": {"production", "archived"},
    "production": {"reduced", "retired", "archived"},
    "reduced": {"production", "retired", "archived"},
    "retired": {"archived"},
    "archived": set(),
}

EXPERIMENT_STATUSES = {
    "planned",
    "prepared",
    "running",
    "completed",
    "completed_with_warnings",
    "failed",
    "invalid",
}

QUEUE_ITEM_STATUSES = {
    "queued",
    "running",
    "completed",
    "completed_with_warnings",
    "failed",
    "skipped",
}

GATE_STATUSES = {
    "incomplete",
    "pass",
    "warn",
    "fail",
    "not_available",
    "not_implemented",
}

LIFECYCLE_TRANSITION_STATUSES = {
    "proposed",
    "approved",
    "rejected",
    "applied",
}

STRICTNESS_MODES = {"lenient", "normal", "strict"}

SWEEP_TYPES = {
    "parameter_robustness",
    "cost_stress",
    "execution_delay_stress_scaffold",
    "walk_forward_scaffold",
}

SWEEP_STATUSES = EXPERIMENT_STATUSES

CORE_ARTIFACT_TYPES = {
    "raw_backtest_output",
    "trade_log",
    "equity_curve",
    "metrics_json",
    "validation_report",
    "portfolio_report",
    "artifact_manifest",
    "run_context",
    "experiment_plan",
    "strategy_diff_proposal",
    "statistical_review",
    "portfolio_review",
    "risk_execution_review",
    "red_team_review",
    "research_librarian_summary",
    "implementation_task",
    "sweep_plan",
    "sweep_summary",
    "review_request",
    "queue_run_summary",
    "morning_report",
}

RUNNER_SUPPORT_ARTIFACT_TYPES = {
    "bars",
    "tester_agent_log",
    "compile_log",
    "mt5_report",
    "runner_config",
    "runner_stdout",
    "runner_stderr",
}

AGENT_SUPPORT_ARTIFACT_TYPES = {
    "run_execution_record",
    "robustness_review",
}

ADVANCED_REVIEW_ARTIFACT_TYPES = {
    "paper_trading_report",
    "production_readiness_report",
}

IMPLEMENTATION_ARTIFACT_TYPES = {
    "implementation_request",
    "diff_review",
    "implementation_compile_log",
}

INTAKE_ARTIFACT_TYPES = {
    "hypothesis_set",
    "strategy_spec_generated",
    "review_packet",
}

GENERATED_BASELINE_ARTIFACT_TYPES = {
    "generated_baseline_review",
}

GENERATED_ROBUSTNESS_ARTIFACT_TYPES = {
    "generated_robustness_review",
}

GENERATED_CANDIDATE_ARTIFACT_TYPES = {
    "generated_candidate_decision_packet",
}

GENERATED_FINAL_HOLDOUT_ARTIFACT_TYPES = {
    "generated_final_holdout_review",
}

RESEARCH_LIBRARY_ARTIFACT_TYPES = {
    "research_source_record",
    "edge_thesis",
    "edge_thesis_extraction_report",
}

HYPOTHESIS_MUTATION_ARTIFACT_TYPES = {
    "mutation_recipe",
    "generated_hypothesis_batch",
    "hypothesis_screening_report",
}

RESEARCH_CAMPAIGN_PLANNING_ARTIFACT_TYPES = {
    "generated_research_campaign_plan",
    "campaign_asset_timeframe_matrix",
    "campaign_budget_report",
    "ranked_manual_baseline_review_queue",
}

STRATEGY_SIMILARITY_ARTIFACT_TYPES = {
    "generated_strategy_similarity_report",
    "hypothesis_diversity_report",
    "similarity_cluster_report",
}

RESEARCH_CAMPAIGN_ANALYSIS_ARTIFACT_TYPES = {
    "generated_research_campaign_report",
    "edge_family_meta_analysis",
    "asset_timeframe_effectiveness_report",
    "similarity_cluster_effectiveness_report",
}

AGENT_WORKSPACE_ARTIFACT_TYPES = {
    "agent_workspace_validation_report",
}

ARTIFACT_TYPES = (
    CORE_ARTIFACT_TYPES
    | RUNNER_SUPPORT_ARTIFACT_TYPES
    | AGENT_SUPPORT_ARTIFACT_TYPES
    | ADVANCED_REVIEW_ARTIFACT_TYPES
    | IMPLEMENTATION_ARTIFACT_TYPES
    | INTAKE_ARTIFACT_TYPES
    | GENERATED_BASELINE_ARTIFACT_TYPES
    | GENERATED_ROBUSTNESS_ARTIFACT_TYPES
    | GENERATED_CANDIDATE_ARTIFACT_TYPES
    | GENERATED_FINAL_HOLDOUT_ARTIFACT_TYPES
    | RESEARCH_LIBRARY_ARTIFACT_TYPES
    | HYPOTHESIS_MUTATION_ARTIFACT_TYPES
    | RESEARCH_CAMPAIGN_PLANNING_ARTIFACT_TYPES
    | STRATEGY_SIMILARITY_ARTIFACT_TYPES
    | RESEARCH_CAMPAIGN_ANALYSIS_ARTIFACT_TYPES
    | AGENT_WORKSPACE_ARTIFACT_TYPES
)

IMPLEMENTATION_REQUEST_STATUSES = {
    "proposed",
    "validated",
    "compiled",
    "reviewed",
    "approved_for_baseline",
    "rejected",
}

COMPILE_STATUSES = {"passed", "failed", "mock_checked"}

REQUIRED_RESULT_ARTIFACT_TYPES = {
    "trade_log",
    "equity_curve",
    "metrics_json",
    "raw_backtest_output",
}


def ensure_member(value: str, allowed: set[str], field_name: str) -> None:
    if value not in allowed:
        raise ValueError(f"{field_name} must be one of {sorted(allowed)}; got {value!r}")


def portable_path(path: str | Path, repo_root: Path) -> str:
    """Store repo-owned paths as relative paths, leaving external/temp paths absolute."""
    path_obj = Path(path)
    try:
        return str(path_obj.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path_obj)


APPROVAL_SCOPES = {"baseline_only", "final_holdout_only"}

APPROVAL_USAGE_STATUSES = {"pending", "completed", "failed"}


def resolve_stored_path(path: str | Path, repo_root: Path) -> Path:
    path_obj = Path(path)
    return path_obj if path_obj.is_absolute() else repo_root / path_obj


def load_review_artifact(
    db_path: str | Path,
    experiment_id: str,
    artifact_type: str,
    repo_root: Path,
) -> dict[str, Any] | None:
    from . import registry

    artifacts = registry.list_artifacts(db_path, experiment_id)
    for art in artifacts:
        if art.get("artifact_type") == artifact_type:
            path_str = art["path"]
            review_path = resolve_stored_path(path_str, repo_root)
            if review_path.is_file():
                try:
                    data = yaml.safe_load(review_path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        return data
                except Exception:
                    pass
    return None


def check_approval_usable(
    approval: dict[str, Any] | None,
    *,
    scope_label: str = "final_holdout_only",
    cli_command: str = "generated-final-holdout approve",
    approval_id: str | None = None,
) -> str | None:
    """Check whether an approval is usable.

    Returns None if usable, otherwise an error message string.
    """
    if not approval:
        if approval_id:
            return f"scope approval not found: {approval_id}"
        return f"No {scope_label} approval found; run {cli_command} first"
    used = bool(approval.get("used"))
    allow_reuse = bool(approval.get("allow_reuse"))
    if used and not allow_reuse:
        return (
            f"Scope approval {approval['approval_id']} already consumed"
            f" (experiment {approval.get('used_by_experiment_id')})."
            " Reuse requires explicit allow_reuse flag."
        )
    return None


def spec_references_production_path(spec_path: Path, strategies_root: Path) -> str | None:
    """Check if a generated spec references automated/strategies/ paths.

    Returns the first offending implementation files entry (e.g. 'implementation.files.expert_advisor: /path'),
    or None if no forbidden reference is found.
    """
    if not spec_path.is_file():
        return None
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    impl_files = spec.get("implementation", {}).get("files", {})
    root_abs = str(strategies_root.resolve())
    root_rel = "automated/strategies/"
    for key in ("expert_advisor", "config", "parameters"):
        val = str(impl_files.get(key, ""))
        normalized = val.replace("\\", "/")
        if root_abs in normalized or root_rel in normalized:
            return f"implementation.files.{key}: {val}"
    return None
