from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .contracts import IMPLEMENTATION_REQUEST_STATUSES, LIFECYCLE_STATES

REPO_ROOT = Path(__file__).resolve().parents[2]


class SchemaValidationError(ValueError):
    """Raised when a research YAML document violates a phase 1 contract."""


def load_yaml(path: str | Path) -> dict[str, Any]:
    yaml_path = Path(path)
    with yaml_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise SchemaValidationError(f"{yaml_path} must contain a YAML mapping")
    return data


def _require_mapping(data: dict[str, Any], key: str, context: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise SchemaValidationError(f"{context}.{key} must be a mapping")
    return value


def _require_list(data: dict[str, Any], key: str, context: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list) or not value:
        raise SchemaValidationError(f"{context}.{key} must be a non-empty list")
    return value


def _require_scalar(data: dict[str, Any], key: str, context: str) -> Any:
    value = data.get(key)
    if value is None or value == "":
        raise SchemaValidationError(f"{context}.{key} is required")
    if isinstance(value, (dict, list)):
        raise SchemaValidationError(f"{context}.{key} must be a scalar value")
    return value


def _resolve_repo_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def validate_hypothesis(data: dict[str, Any]) -> dict[str, Any]:
    required_scalars = [
        "hypothesis_id",
        "name",
        "status",
        "mechanism",
        "expected_edge",
        "initial_test",
        "invalidation_rule",
        "created_at",
        "updated_at",
    ]
    for key in required_scalars:
        _require_scalar(data, key, "hypothesis")

    for key in ["timeframes", "markets", "predictions", "failure_modes"]:
        _require_list(data, key, "hypothesis")

    return data


def validate_strategy_spec(data: dict[str, Any], *, require_files: bool = True) -> dict[str, Any]:
    required_scalars = [
        "strategy_id",
        "strategy_version",
        "hypothesis_id",
        "status",
        "timeframe",
        "created_at",
        "updated_at",
    ]
    for key in required_scalars:
        _require_scalar(data, key, "strategy")

    _require_list(data, "universe", "strategy")
    _require_mapping(data, "entry", "strategy")
    _require_mapping(data, "exit", "strategy")
    _require_mapping(data, "risk", "strategy")

    implementation = _require_mapping(data, "implementation", "strategy")
    if implementation.get("engine") != "mt5":
        raise SchemaValidationError("strategy.implementation.engine must be mt5 in phase 1")
    generation_mode = implementation.get("generation_mode")
    if generation_mode not in {"wrapped_existing_files", "generated_from_spec"}:
        raise SchemaValidationError(
            "strategy.implementation.generation_mode must be wrapped_existing_files or generated_from_spec"
        )
    if generation_mode == "generated_from_spec":
        raise SchemaValidationError("generated_from_spec is reserved for a later phase")
    files = _require_mapping(implementation, "files", "strategy.implementation")
    for key in ["config", "parameters", "expert_advisor"]:
        value = _require_scalar(files, key, "strategy.implementation.files")
        if require_files and not _resolve_repo_path(str(value)).is_file():
            raise SchemaValidationError(f"strategy.implementation.files.{key} does not exist: {value}")

    costs = _require_mapping(data, "costs", "strategy")
    if costs.get("assumptions_documented") is not True:
        raise SchemaValidationError("strategy.costs.assumptions_documented must be true")
    for key in ["spread_source", "slippage", "commission"]:
        _require_mapping(costs, key, "strategy.costs")

    execution_timing = _require_mapping(data, "execution_timing", "strategy")
    for key in ["signal_bar", "entry_bar", "assumed_fill_price"]:
        _require_scalar(execution_timing, key, "strategy.execution_timing")

    validation = _require_mapping(data, "validation", "strategy")
    min_trades = validation.get("min_trades_required")
    if not isinstance(min_trades, int) or min_trades < 1:
        raise SchemaValidationError("strategy.validation.min_trades_required must be a positive integer")

    research_budget = _require_mapping(data, "research_budget", "strategy")
    for key in ["max_structural_variants", "max_parameter_sets", "max_filter_additions", "max_agent_iterations", "max_complexity_score"]:
        value = research_budget.get(key)
        if not isinstance(value, int) or value < 0:
            raise SchemaValidationError(f"strategy.research_budget.{key} must be a non-negative integer")

    lifecycle = _require_mapping(data, "lifecycle", "strategy")
    state = _require_scalar(lifecycle, "state", "strategy.lifecycle")
    if state not in LIFECYCLE_STATES:
        raise SchemaValidationError(f"strategy.lifecycle.state must be one of {sorted(LIFECYCLE_STATES)}")
    next_states = lifecycle.get("allowed_next_states", [])
    if next_states and (not isinstance(next_states, list) or any(item not in LIFECYCLE_STATES for item in next_states)):
        raise SchemaValidationError("strategy.lifecycle.allowed_next_states must contain lifecycle states")
    _require_scalar(data, "invalidation_rule", "strategy")

    return data


SANDBOX_ROOT = REPO_ROOT / "automated" / "generated_strategies"
STRATEGIES_ROOT = REPO_ROOT / "automated" / "strategies"
GENERATED_SPECS_DIR = REPO_ROOT / "automated" / "generated_specs"
HYPOTHESES_DIR = REPO_ROOT / "hypotheses"

FORBIDDEN_SANDBOX_PREFIXES = [
    REPO_ROOT / "automated" / "strategies",
    REPO_ROOT / "automated" / "research",
    REPO_ROOT / "automated" / "scripts",
    REPO_ROOT / "automated" / "specs",
    REPO_ROOT / "automated" / "runs",
    REPO_ROOT / "automated" / "reports",
    REPO_ROOT / "tests",
    REPO_ROOT / "hypotheses",
]

FORBIDDEN_SANDBOX_FILES = {
    REPO_ROOT / "automated" / "research_registry.sqlite",
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "_context.md",
    REPO_ROOT / "_project.md",
}


def validate_implementation_request(data: dict[str, Any]) -> dict[str, Any]:
    scalar_required = [
        "implementation_request_id",
        "strategy_id",
        "strategy_version",
        "sandbox_dir",
        "created_by",
        "created_at",
    ]
    for key in scalar_required:
        if key not in data or data[key] is None or data.get(key) == "":
            raise SchemaValidationError(f"implementation_request.{key} is required")

    status = data.get("status", "proposed")
    if status not in IMPLEMENTATION_REQUEST_STATUSES:
        raise SchemaValidationError(
            f"implementation_request.status must be one of {sorted(IMPLEMENTATION_REQUEST_STATUSES)}; got {status!r}"
        )

    sandbox_dir = _resolve_repo_path(str(data["sandbox_dir"]))
    sandbox_str = str(sandbox_dir.resolve())
    expected_prefix = str(SANDBOX_ROOT.resolve())
    if not sandbox_str.startswith(expected_prefix + "/") and sandbox_str != expected_prefix:
        raise SchemaValidationError(
            f"implementation_request.sandbox_dir must be under {SANDBOX_ROOT}; got {sandbox_dir}"
        )
    if str(STRATEGIES_ROOT.resolve()) in sandbox_str:
        raise SchemaValidationError(
            f"implementation_request.sandbox_dir must not be under {STRATEGIES_ROOT}; got {sandbox_dir}"
        )

    generated_files = data.get("generated_files", [])
    if not isinstance(generated_files, list) or not generated_files:
        raise SchemaValidationError("implementation_request.generated_files must be a non-empty list")

    expected_inputs = data.get("expected_inputs", [])
    if expected_inputs is not None:
        if not isinstance(expected_inputs, list):
            raise SchemaValidationError("implementation_request.expected_inputs must be a list")
        for inp in expected_inputs:
            if not isinstance(inp, dict):
                raise SchemaValidationError("implementation_request.expected_inputs entries must be mappings")
            if not inp.get("name"):
                raise SchemaValidationError("implementation_request.expected_inputs[].name is required")
            if inp.get("required", False) and not inp.get("type"):
                raise SchemaValidationError(
                    f"implementation_request.expected_inputs[].type is required for required input {inp['name']}"
                )

    parameters = data.get("parameters")
    if parameters is not None and not isinstance(parameters, dict):
        raise SchemaValidationError("implementation_request.parameters must be a mapping")

    created_at = data.get("created_at", "")
    try:
        datetime.fromisoformat(created_at)
    except (ValueError, TypeError):
        raise SchemaValidationError(f"implementation_request.created_at must be ISO 8601; got {created_at!r}")

    return data


def resolve_hypothesis_file(hypothesis_id: str, root: str | Path = REPO_ROOT) -> Path | None:
    hypotheses_dir = Path(root) / "hypotheses"
    if not hypotheses_dir.is_dir():
        return None
    for path in sorted(hypotheses_dir.glob("*.yaml")):
        try:
            data = load_yaml(path)
        except Exception:
            continue
        if data.get("hypothesis_id") == hypothesis_id:
            return path
    return None
