from __future__ import annotations

from pathlib import Path
from typing import Any

from . import registry
from .contracts import ARTIFACT_TYPES
from .schemas import REPO_ROOT, SchemaValidationError, load_yaml


CONTRACTS_DIR = REPO_ROOT / "automated" / "specs" / "agents"

AGENT_ARTIFACT_TYPES = {
    "experiment_designer": "experiment_plan",
    "strategy_spec_agent": "strategy_diff_proposal",
    "backtest_runner": "run_execution_record",
    "robustness_agent": "robustness_review",
    "statistical_reviewer": "statistical_review",
    "portfolio_reviewer": "portfolio_review",
    "risk_execution_reviewer": "risk_execution_review",
    "red_team_reviewer": "red_team_review",
    "research_librarian": "research_librarian_summary",
}

VALID_ARTIFACT_TYPES = ARTIFACT_TYPES

ORDINARY_RESEARCH_ALLOWED_ACTIONS = {
    "create_hypothesis_draft",
    "propose_experiment_plan",
    "propose_strategy_spec_diff",
    "propose_parameter_diff",
    "run_approved_backtest",
    "generate_validation_report",
    "generate_portfolio_report",
    "write_structured_review_artifact",
    "propose_lifecycle_transition",
}

ORDINARY_RESEARCH_FORBIDDEN_ACTIONS = {
    "edit_mql5",
    "change_order_execution_logic",
    "change_indicator_implementation",
    "change_backtest_engine_logic",
    "change_data_cleaning_logic",
    "change_metric_definitions",
    "remove_bad_assets_without_structural_rationale",
    "change_validation_period_after_results_without_override",
}


SCHEMAS: dict[str, dict[str, Any]] = {
    "experiment_designer": {
        "required": [
            "agent_role",
            "experiment_plan_id",
            "hypothesis_id",
            "strategy_id",
            "primary_test",
            "secondary_tests",
            "required_data",
            "train_period",
            "validation_period",
            "test_period",
            "parameters_allowed",
            "parameters_locked",
            "pass_fail_criteria",
            "risks",
            "created_at",
        ],
        "lists": ["secondary_tests", "required_data", "parameters_allowed", "parameters_locked", "risks"],
    },
    "strategy_spec_agent": {
        "required": [
            "agent_role",
            "strategy_id",
            "proposed_diff",
            "diff_type",
            "rationale",
            "expected_effect",
            "risks",
            "complexity_impact",
            "budget_impact",
            "requires_implementation_task",
        ],
        "enum": {"diff_type": {"parameter_diff", "structural_diff"}},
        "bools": ["requires_implementation_task"],
        "lists": ["risks"],
    },
    "statistical_reviewer": {
        "required": [
            "agent_role",
            "experiment_id",
            "sample_size_assessment",
            "number_of_trials",
            "multiple_testing_risk",
            "non_normal_return_concerns",
            "overlapping_trade_concerns",
            "train_test_leakage_concerns",
            "decision",
            "required_followups",
        ],
        "enum": {"decision": {"pass", "fail", "needs_more_work"}},
        "lists": ["required_followups"],
    },
    "portfolio_reviewer": {
        "required": [
            "agent_role",
            "experiment_id",
            "portfolio_id",
            "correlation_summary",
            "tail_correlation_summary",
            "drawdown_overlap_summary",
            "marginal_contribution_summary",
            "duplicate_exposure_warning",
            "decision",
            "required_followups",
        ],
        "enum": {"decision": {"pass", "fail", "needs_more_work"}},
        "bools": ["duplicate_exposure_warning"],
        "lists": ["required_followups"],
    },
    "risk_execution_reviewer": {
        "required": [
            "agent_role",
            "experiment_id",
            "bar_close_execution_realism",
            "next_bar_sensitivity",
            "liquidity_assumptions",
            "position_concentration",
            "gap_or_weekend_risk",
            "funding_borrow_roll_costs",
            "decision",
            "required_followups",
        ],
        "enum": {"decision": {"pass", "fail", "needs_more_work"}},
        "lists": ["required_followups"],
    },
    "red_team_reviewer": {
        "required": [
            "agent_role",
            "experiment_id",
            "possible_lookahead",
            "survivorship_concerns",
            "hidden_beta_trend_vol_exposure",
            "overfit_filter_concerns",
            "single_trade_dependence",
            "adjacent_parameter_failure",
            "rejection_reasons",
            "decision",
            "required_followups",
        ],
        "enum": {"decision": {"pass", "reject", "needs_more_work"}},
        "lists": ["rejection_reasons", "required_followups"],
    },
    "research_librarian": {
        "required": [
            "agent_role",
            "hypothesis_id",
            "strategy_id",
            "tested_summary",
            "failed_variants",
            "passed_variants",
            "exhausted_hypotheses",
            "duplicate_ideas",
            "suggested_next_actions",
        ],
        "lists": ["failed_variants", "passed_variants", "exhausted_hypotheses", "duplicate_ideas", "suggested_next_actions"],
    },
    "implementation_task": {
        "required": [
            "implementation_task_id",
            "requested_by",
            "reason",
            "files_to_change",
            "expected_behavior_change",
            "tests_required",
            "human_approved",
            "created_at",
            "status",
        ],
        "enum": {"status": {"proposed", "approved", "rejected", "completed"}},
        "bools": ["human_approved"],
        "lists": ["files_to_change", "tests_required"],
    },
}


def load_contract(role_name: str) -> dict[str, Any]:
    path = CONTRACTS_DIR / f"{role_name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"agent contract not found: {role_name}")
    contract = load_yaml(path)
    validate_contract(contract)
    return contract


def list_contracts() -> list[dict[str, Any]]:
    if not CONTRACTS_DIR.is_dir():
        return []
    contracts = []
    for path in sorted(CONTRACTS_DIR.glob("*.yaml")):
        contract = load_yaml(path)
        validate_contract(contract)
        contracts.append(
            {
                "role_name": contract["role_name"],
                "artifact_type": contract["artifact_type"],
                "can_modify_code": contract.get("can_modify_code") is True,
                "can_modify_mql5": contract.get("can_modify_mql5") is True,
                "path": str(path),
            }
        )
    return contracts


def validate_contract(contract: dict[str, Any]) -> dict[str, Any]:
    required = [
        "role_name",
        "purpose",
        "allowed_actions",
        "forbidden_actions",
        "required_inputs",
        "output_schema",
        "artifact_type",
        "can_modify_code",
        "requires_implementation_task",
    ]
    for key in required:
        if key not in contract:
            raise SchemaValidationError(f"agent_contract.{key} is required")
    role = contract["role_name"]
    if role not in AGENT_ARTIFACT_TYPES:
        raise SchemaValidationError(f"unknown agent role: {role}")
    if contract["artifact_type"] != AGENT_ARTIFACT_TYPES[role]:
        raise SchemaValidationError(f"agent_contract.artifact_type must be {AGENT_ARTIFACT_TYPES[role]}")
    if contract.get("can_modify_mql5", False) is not False:
        raise SchemaValidationError("agent_contract.can_modify_mql5 must be false")
    for key in ["allowed_actions", "forbidden_actions", "required_inputs"]:
        if not isinstance(contract[key], list):
            raise SchemaValidationError(f"agent_contract.{key} must be a list")
    if not isinstance(contract["output_schema"], dict):
        raise SchemaValidationError("agent_contract.output_schema must be a mapping")
    return contract


def validate_output(data: dict[str, Any]) -> dict[str, Any]:
    role = data.get("agent_role")
    schema_key = role if role in SCHEMAS else "implementation_task" if data.get("implementation_task_id") else None
    if not schema_key:
        raise SchemaValidationError("agent output must include a known agent_role or implementation_task_id")
    schema = SCHEMAS[schema_key]
    for key in schema["required"]:
        if key not in data or data[key] in (None, ""):
            raise SchemaValidationError(f"{schema_key}.{key} is required")
    for key in schema.get("lists", []):
        if not isinstance(data.get(key), list):
            raise SchemaValidationError(f"{schema_key}.{key} must be a list")
    for key in schema.get("bools", []):
        if not isinstance(data.get(key), bool):
            raise SchemaValidationError(f"{schema_key}.{key} must be true or false")
    for key, allowed in schema.get("enum", {}).items():
        if data.get(key) not in allowed:
            raise SchemaValidationError(f"{schema_key}.{key} must be one of {sorted(allowed)}")
    if role and role != schema_key:
        raise SchemaValidationError(f"agent_role must be {schema_key}")
    if schema_key != "implementation_task":
        expected_artifact_type = AGENT_ARTIFACT_TYPES[schema_key]
    else:
        expected_artifact_type = "implementation_task"
    return {
        "status": "valid",
        "agent_role": role,
        "artifact_type": expected_artifact_type,
        "schema": schema_key,
    }


def validate_output_file(path: str | Path) -> dict[str, Any]:
    data = load_yaml(path)
    result = validate_output(data)
    result["path"] = str(Path(path))
    return result


def attach_output(db_path: str | Path, *, experiment_id: str, path: str | Path) -> dict[str, Any]:
    data = load_yaml(path)
    validation = validate_output(data)
    artifact_type = validation["artifact_type"]
    if artifact_type not in VALID_ARTIFACT_TYPES:
        raise SchemaValidationError(f"unsupported agent artifact type: {artifact_type}")
    role = validation.get("agent_role") or "implementation_task"
    registry.attach_artifact(db_path, experiment_id, artifact_type, path)
    registry.attach_agent_artifact(
        db_path,
        experiment_id=experiment_id,
        agent_role=role,
        artifact_type=artifact_type,
        path=path,
    )
    return {"experiment_id": experiment_id, "artifact_type": artifact_type, "agent_role": role, "path": str(Path(path))}


def permissions_for_role(role_name: str) -> dict[str, Any]:
    contract = load_contract(role_name)
    return {
        "role_name": role_name,
        "allowed_actions": contract["allowed_actions"],
        "forbidden_actions": contract["forbidden_actions"],
        "can_modify_code": contract.get("can_modify_code") is True,
        "can_modify_mql5": False,
        "requires_implementation_task": contract.get("requires_implementation_task") is True,
        "ordinary_research_allowed_actions": sorted(ORDINARY_RESEARCH_ALLOWED_ACTIONS),
        "ordinary_research_forbidden_actions": sorted(ORDINARY_RESEARCH_FORBIDDEN_ACTIONS),
    }


def check_permission(
    *,
    role_name: str,
    action: str,
    files: list[str] | None = None,
    ordinary_research: bool = True,
    has_implementation_task: bool = False,
) -> dict[str, Any]:
    contract = load_contract(role_name)
    files = files or []
    mql5_files = [item for item in files if Path(item).suffix.lower() == ".mq5"]
    blockers: list[str] = []
    if ordinary_research and action in ORDINARY_RESEARCH_FORBIDDEN_ACTIONS:
        blockers.append(f"{action} is forbidden during ordinary research")
    if ordinary_research and action not in ORDINARY_RESEARCH_ALLOWED_ACTIONS:
        blockers.append(f"{action} is not listed as an ordinary research action")
    if mql5_files and not has_implementation_task:
        blockers.append("direct .mq5 edits require an implementation task")
    if mql5_files and contract.get("can_modify_mql5", False) is not True:
        blockers.append(f"{role_name} cannot directly modify MQL5 files")
    if action not in contract.get("allowed_actions", []):
        blockers.append(f"{action} is not allowed for {role_name}")
    if action in contract.get("forbidden_actions", []):
        blockers.append(f"{action} is forbidden for {role_name}")
    return {
        "role_name": role_name,
        "action": action,
        "files": files,
        "status": "denied" if blockers else "allowed",
        "blockers": blockers,
    }
