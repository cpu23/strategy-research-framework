from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from . import agents, generated_baseline, generated_candidate, generated_final_holdout, generated_robustness, implementation as impl_mod, intake, lifecycle, registry, runner, sweeps, validation
from .contracts import ARTIFACT_TYPES, QUEUE_ITEM_STATUSES, check_approval_usable, resolve_stored_path
from .portfolio import write_configured_portfolio_report, write_portfolio_report
from .schemas import (
    REPO_ROOT,
    SANDBOX_ROOT,
    SchemaValidationError,
    GENERATED_SPECS_DIR,
    load_yaml,
    resolve_hypothesis_file,
    validate_hypothesis,
    validate_strategy_spec,
)


QUEUE_SPEC_DIR = REPO_ROOT / "automated" / "specs" / "research_queue"
QUEUE_RUN_ROOT = REPO_ROOT / "automated" / "research_runs" / "queue_runs"

TASK_TYPES = {
    "baseline_experiment",
    "parameter_robustness",
    "cost_stress",
    "portfolio_review",
    "red_team_review",
    "research_summary",
    "implementation_request",
    "implementation_compile_check",
    "implementation_review",
    "hypothesis_generation",
    "strategy_spec_generation",
    "implementation_materialization",
    "research_review_packet",
    "generated_baseline_experiment",
    "generated_baseline_review",
    "generated_robustness_sweep",
    "generated_robustness_review",
    "generated_candidate_decision_packet",
    "generated_final_holdout_experiment",
    "generated_final_holdout_review",
}

INTAKE_TASK_TYPES = {
    "hypothesis_generation",
    "strategy_spec_generation",
    "implementation_materialization",
    "research_review_packet",
}

FORBIDDEN_PERMISSION_DEFAULTS = {
    "allow_mql5_edits": False,
    "allow_dataset_changes": False,
    "allow_validation_threshold_changes": False,
    "allow_lifecycle_apply": False,
    "allow_final_holdout": False,
}

PERMISSION_DEFAULTS = {
    "allow_runner_execution": False,
    **FORBIDDEN_PERMISSION_DEFAULTS,
    "allow_lifecycle_propose": True,
}

PERMISSION_KEYS = set(PERMISSION_DEFAULTS)

BUDGET_DEFAULTS = {
    "max_experiments": 1,
    "max_child_experiments": 0,
    "max_runtime_minutes": 60,
    "max_parameters_changed_per_child": 1,
    "max_sweeps": 0,
    "max_failed_runs": 0,
    "max_disk_usage_mb": 512,
    "require_one_variable_at_a_time": True,
}

AGENT_REVIEW_TASKS = {
    "red_team_review": "red_team_reviewer",
    "portfolio_review": "portfolio_reviewer",
    "research_summary": "research_librarian",
}


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()


def _repo_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else REPO_ROOT / path


def _safe_repo_relative_path(path_value: str, field_name: str) -> Path:
    path = Path(path_value)
    if path.is_absolute() or path_value.startswith("~"):
        raise SchemaValidationError(f"queue.{field_name} must be a repo-relative path")
    if ".." in path.parts:
        raise SchemaValidationError(f"queue.{field_name} may not contain path traversal")
    return REPO_ROOT / path


def _validate_queue_identifier(value: str, field_name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value) or ".." in value or "/" in value or "\\" in value:
        raise SchemaValidationError(f"queue.{field_name} must be an identifier, not a path")


def _queue_run_id(queue_id: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"QUEUE_RUN_{stamp}_{_slug(queue_id)[:48]}_{uuid.uuid4().hex[:6].upper()}"


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _write_json_new(path: Path, data: dict[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text_new(path: Path, text: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_queue(path: str | Path) -> dict[str, Any]:
    queue_path = _repo_path(path)
    data = load_yaml(queue_path)
    if isinstance(data.get("items"), list):
        queue_id = str(data.get("queue_id") or queue_path.stem)
        _validate_queue_identifier(queue_id, "queue_id")
        items = []
        for index, item in enumerate(data["items"]):
            if not isinstance(item, dict):
                raise SchemaValidationError("queue.items entries must be mappings")
            item = dict(item)
            item.setdefault("queue_id", f"{queue_id}_{index:03d}")
            items.append(item)
        return {"queue_id": queue_id, "items": items, "source_path": str(queue_path)}
    data.setdefault("queue_id", queue_path.stem)
    _validate_queue_identifier(str(data["queue_id"]), "queue_id")
    return {"queue_id": str(data["queue_id"]), "items": [data], "source_path": str(queue_path)}


def _strategy_spec_path(strategy_id: str) -> Path:
    path = REPO_ROOT / "automated" / "specs" / "strategies" / f"{strategy_id}.yaml"
    if not path.is_file():
        raise SchemaValidationError(f"referenced strategy does not exist: {strategy_id}")
    return path


def _generated_strategy_spec_path(strategy_id: str) -> Path:
    path = GENERATED_SPECS_DIR / f"{strategy_id}.yaml"
    if not path.is_file():
        raise SchemaValidationError(f"generated strategy spec does not exist: {strategy_id}")
    return path


def _validated_strategy(strategy_id: str) -> dict[str, Any]:
    spec = load_yaml(_strategy_spec_path(strategy_id))
    validate_strategy_spec(spec)
    if spec["strategy_id"] != strategy_id:
        raise SchemaValidationError(f"strategy_id mismatch in strategy spec: {strategy_id}")
    return spec


def _normalize_budget(item: dict[str, Any]) -> dict[str, Any]:
    budget = dict(BUDGET_DEFAULTS)
    supplied = item.get("budget") or {}
    if not isinstance(supplied, dict):
        raise SchemaValidationError("queue.budget must be a mapping")
    budget.update(supplied)
    for key in [
        "max_experiments",
        "max_child_experiments",
        "max_runtime_minutes",
        "max_parameters_changed_per_child",
        "max_sweeps",
        "max_failed_runs",
        "max_disk_usage_mb",
    ]:
        value = budget.get(key)
        if not isinstance(value, int) or value < 0:
            raise SchemaValidationError(f"queue.budget.{key} must be a non-negative integer")
    if not isinstance(budget.get("require_one_variable_at_a_time"), bool):
        raise SchemaValidationError("queue.budget.require_one_variable_at_a_time must be true or false")
    if budget["require_one_variable_at_a_time"] and budget["max_parameters_changed_per_child"] != 1:
        raise SchemaValidationError("one-variable-at-a-time queue items require max_parameters_changed_per_child=1")
    return budget


def _human_override_is_valid(db_path: str | Path, permissions: dict[str, Any], permission_name: str) -> bool:
    override = permissions.get("human_override")
    if not isinstance(override, dict):
        return False
    if override.get("permission") not in {permission_name, "all"}:
        return False
    task_id = override.get("implementation_task_id")
    approved_by = override.get("approved_by")
    if not task_id or not approved_by:
        return False
    registry.init_db(db_path)
    connection = registry.connect(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM implementation_tasks WHERE implementation_task_id = ?",
            (task_id,),
        ).fetchone()
    finally:
        connection.close()
    return bool(row and row["human_approved"])


def _normalize_permissions(db_path: str | Path, item: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    supplied = item.get("permissions")
    if not isinstance(supplied, dict):
        raise SchemaValidationError("queue.permissions must be a mapping")
    unknown = sorted(set(supplied) - (PERMISSION_KEYS | {"human_override"}))
    if unknown:
        raise SchemaValidationError(f"queue.permissions has unknown field(s): {unknown}")
    missing = sorted(PERMISSION_KEYS - set(supplied))
    if missing:
        raise SchemaValidationError(f"queue.permissions is missing required field(s): {missing}")
    permissions = {key: supplied[key] for key in PERMISSION_DEFAULTS}
    if "human_override" in supplied:
        permissions["human_override"] = supplied["human_override"]
    blockers: list[str] = []
    for key in PERMISSION_DEFAULTS:
        if not isinstance(permissions.get(key), bool):
            raise SchemaValidationError(f"queue.permissions.{key} must be true or false")
    for key, default in FORBIDDEN_PERMISSION_DEFAULTS.items():
        if permissions.get(key) is True and not _human_override_is_valid(db_path, permissions, key):
            blockers.append(f"{key} is forbidden for autonomous queue execution without a human-approved implementation task")
    if permissions.get("allow_lifecycle_apply") is True:
        blockers.append("allow_lifecycle_apply is never used by queue execution")
    return permissions, blockers


def _load_sweep_config(item: dict[str, Any]) -> dict[str, Any] | None:
    raw = item.get("sweep_config")
    if raw in (None, ""):
        return None
    if isinstance(raw, str):
        return load_yaml(_safe_repo_relative_path(raw, "sweep_config"))
    if isinstance(raw, dict):
        config = dict(raw)
        _validate_inline_config_paths(config)
        return config
    raise SchemaValidationError("queue.sweep_config must be a mapping or YAML path")


def _validate_inline_config_paths(value: Any, prefix: str = "sweep_config") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}"
            if isinstance(child, str) and (key.endswith("_path") or key.endswith("_file") or key.endswith("_dir") or key.endswith("_config")):
                _safe_repo_relative_path(child, child_prefix)
            else:
                _validate_inline_config_paths(child, child_prefix)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_inline_config_paths(child, f"{prefix}[{index}]")


def _validate_required_outputs(required_outputs: Any) -> list[str]:
    outputs = required_outputs or []
    if not isinstance(outputs, list):
        raise SchemaValidationError("queue.required_outputs must be a list")
    unknown = [item for item in outputs if item not in ARTIFACT_TYPES]
    if unknown:
        raise SchemaValidationError(f"queue.required_outputs has unknown artifact types: {unknown}")
    return [str(item) for item in outputs]


def _validate_required_fields(item, required_keys, task_type=None):
    for key in required_keys:
        if item.get(key) in (None, ""):
            if task_type:
                raise SchemaValidationError(f"queue.{key} is required for {task_type}")
            else:
                raise SchemaValidationError(f"queue.{key} is required")


def _validate_permission_requirement(permissions, key, must_be, task_type):
    value = "true" if must_be else "false"
    if must_be:
        if permissions.get(key) is not True:
            raise SchemaValidationError(f"{task_type} requires {key}={value}")
    else:
        if permissions.get(key) is True:
            raise SchemaValidationError(f"{task_type} requires {key}={value}")


def _validate_allowed_agent_roles(item: dict[str, Any]) -> list[str]:
    roles = item.get("allowed_agent_roles") or []
    if not isinstance(roles, list):
        raise SchemaValidationError("queue.allowed_agent_roles must be a list")
    for role in roles:
        agents.load_contract(str(role))
    return [str(role) for role in roles]


def _build_validated_item(
    item: dict[str, Any],
    *,
    budget: dict[str, Any],
    permissions: dict[str, Any],
    sweep_config: dict[str, Any] | None,
) -> dict[str, Any]:
    validated = {
        **item,
        "budget": budget,
        "permissions": permissions,
        "required_outputs": _validate_required_outputs(item.get("required_outputs")),
        "allowed_agent_roles": _validate_allowed_agent_roles(item),
        "sweep_config": sweep_config,
        "status": item.get("status") or "queued",
        "notes": item.get("notes"),
    }
    if validated["status"] not in QUEUE_ITEM_STATUSES:
        raise SchemaValidationError(f"queue.status must be one of {sorted(QUEUE_ITEM_STATUSES)}")
    return validated


def _persist_validated_item(
    db_path: str | Path,
    validated: dict[str, Any],
    *,
    source_path: str,
) -> None:
    registry.upsert_queue_item(
        db_path,
        {
            "queue_id": validated["queue_id"],
            "priority": validated["priority"],
            "hypothesis_id": validated.get("hypothesis_id") or "",
            "strategy_id": validated.get("strategy_id") or "",
            "task_type": validated["task_type"],
            "parent_experiment_id": validated.get("parent_experiment_id"),
            "status": validated["status"],
            "requested_by": validated["requested_by"],
            "allowed_agent_roles": validated["allowed_agent_roles"],
            "budget": validated["budget"],
            "permissions": validated["permissions"],
            "required_outputs": validated["required_outputs"],
            "sweep_config": validated.get("sweep_config"),
            "created_at": validated["created_at"],
            "notes": validated.get("notes"),
            "source_path": source_path,
        },
    )


def validate_queue(db_path: str | Path, queue_path: str | Path, *, persist: bool = True) -> dict[str, Any]:
    queue_doc = load_queue(queue_path)
    validated_items: list[dict[str, Any]] = []
    warnings: list[str] = []
    for item in queue_doc["items"]:
        task_type = item.get("task_type")
        if task_type not in TASK_TYPES:
            raise SchemaValidationError(f"queue.task_type must be one of {sorted(TASK_TYPES)}")
        _validate_queue_identifier(str(item.get("queue_id", "")), "queue_id")
        _validate_inline_config_paths(item, "item")
        if not isinstance(item.get("priority"), int):
            raise SchemaValidationError("queue.priority must be an integer")

        if task_type in INTAKE_TASK_TYPES:
            intake_keys = ["queue_id", "priority", "requested_by", "created_at"]
            if task_type in ("strategy_spec_generation", "implementation_materialization", "research_review_packet"):
                intake_keys.append("strategy_id")
            if task_type == "strategy_spec_generation":
                intake_keys.append("hypothesis_id")
            if task_type == "implementation_materialization":
                intake_keys.append("generated_spec_path")
                if item.get("strategy_version") in (None, ""):
                    raise SchemaValidationError("queue.strategy_version is required for implementation_materialization")
            if task_type == "research_review_packet":
                if item.get("strategy_version") in (None, ""):
                    raise SchemaValidationError("queue.strategy_version is required for research_review_packet")
            _validate_required_fields(item, intake_keys)
            if task_type == "hypothesis_generation":
                if item.get("research_theme") in (None, ""):
                    raise SchemaValidationError("queue.research_theme is required for hypothesis_generation")
                if item.get("symbol") in (None, ""):
                    raise SchemaValidationError("queue.symbol is required for hypothesis_generation")
                if item.get("timeframe") in (None, ""):
                    raise SchemaValidationError("queue.timeframe is required for hypothesis_generation")
                if item.get("market_regime") in (None, ""):
                    raise SchemaValidationError("queue.market_regime is required for hypothesis_generation")
                if item.get("strategy_family") in (None, ""):
                    raise SchemaValidationError("queue.strategy_family is required for hypothesis_generation")
                item.setdefault("max_hypotheses", 3)

            budget = _normalize_budget(item)
            permissions, blockers = _normalize_permissions(db_path, item)
            if blockers:
                raise SchemaValidationError("; ".join(blockers))
            validated = _build_validated_item(item, budget=budget, permissions=permissions, sweep_config=None)
            if persist:
                _persist_validated_item(db_path, validated, source_path=queue_doc["source_path"])
            validated_items.append(validated)
            continue

        if task_type in ("generated_baseline_experiment", "generated_baseline_review"):
            gb_keys = ["queue_id", "priority", "strategy_id", "strategy_version", "requested_by", "created_at"]
            if task_type == "generated_baseline_experiment":
                gb_keys.extend(["hypothesis_id", "dataset_id"])
                gb_keys.append("implementation_request_id")
            if task_type == "generated_baseline_review":
                gb_keys.append("parent_experiment_id")
            _validate_required_fields(item, gb_keys, task_type)
            if task_type == "generated_baseline_experiment":
                if item.get("strategy_version") in (None, ""):
                    raise SchemaValidationError("queue.strategy_version is required for generated_baseline_experiment")

            budget = _normalize_budget(item)
            permissions, blockers = _normalize_permissions(db_path, item)
            if blockers:
                raise SchemaValidationError("; ".join(blockers))
            if task_type == "generated_baseline_experiment":
                _validate_permission_requirement(permissions, "allow_final_holdout", False, task_type)
                _validate_permission_requirement(permissions, "allow_lifecycle_apply", False, task_type)
                _validate_permission_requirement(permissions, "allow_runner_execution", True, task_type)

            validated = _build_validated_item(item, budget=budget, permissions=permissions, sweep_config=None)
            if persist:
                _persist_validated_item(db_path, validated, source_path=queue_doc["source_path"])
            validated_items.append(validated)
            continue

        if task_type in ("generated_robustness_sweep", "generated_robustness_review"):
            gr_keys = ["queue_id", "priority", "strategy_id", "strategy_version", "requested_by", "created_at"]
            if task_type == "generated_robustness_sweep":
                gr_keys.extend(["implementation_request_id", "baseline_experiment_id"])
                gr_keys.append("hypothesis_id")
            if task_type == "generated_robustness_review":
                gr_keys.append("parent_experiment_id")
            _validate_required_fields(item, gr_keys, task_type)

            budget = _normalize_budget(item)
            permissions, blockers = _normalize_permissions(db_path, item)
            if blockers:
                raise SchemaValidationError("; ".join(blockers))

            sweep_config: dict[str, Any] | None = None
            if task_type == "generated_robustness_sweep":
                _validate_permission_requirement(permissions, "allow_final_holdout", False, task_type)
                _validate_permission_requirement(permissions, "allow_lifecycle_apply", False, task_type)
                _validate_permission_requirement(permissions, "allow_runner_execution", True, task_type)

                if budget["max_child_experiments"] <= 0:
                    raise SchemaValidationError("generated_robustness_sweep requires budget.max_child_experiments > 0")
                if budget["max_child_experiments"] > 12:
                    raise SchemaValidationError("generated_robustness_sweep requires budget.max_child_experiments <= 12")

                raw_sweep = item.get("sweep_config")
                if not isinstance(raw_sweep, dict):
                    raise SchemaValidationError("generated_robustness_sweep requires sweep_config (inline mapping)")

                param_blockers = generated_robustness.validate_sweep_parameters(
                    raw_sweep.get("parameters", {})
                )
                if param_blockers:
                    raise SchemaValidationError("; ".join(param_blockers))

                mutation_blockers = generated_robustness.has_blocked_mutations(raw_sweep)
                if mutation_blockers:
                    raise SchemaValidationError("; ".join(mutation_blockers))

                raw_sweep.setdefault("sweep_type", "parameter_robustness")
                raw_sweep.setdefault("parent_experiment_id", item.get("baseline_experiment_id"))
                raw_sweep.setdefault("strategy_id", item["strategy_id"])
                raw_sweep.setdefault("spec_path", str(_generated_strategy_spec_path(item["strategy_id"])))
                raw_sweep.setdefault("mode", "one_variable_at_a_time")
                raw_sweep_budget = dict(raw_sweep.get("budget") or {})
                raw_sweep_budget.setdefault("max_child_experiments", budget["max_child_experiments"])
                raw_sweep_budget.setdefault("max_parameters_changed_per_child", 1)
                raw_sweep_budget.setdefault("require_one_variable_at_a_time", True)
                raw_sweep["budget"] = raw_sweep_budget
                sweeps.validate_sweep_config(raw_sweep)
                if raw_sweep.get("parent_experiment_id"):
                    sweeps.plan_sweep(db_path, raw_sweep)
                sweep_config = raw_sweep

            validated = _build_validated_item(item, budget=budget, permissions=permissions, sweep_config=sweep_config)
            if persist:
                _persist_validated_item(db_path, validated, source_path=queue_doc["source_path"])
            validated_items.append(validated)
            continue

        if task_type == "generated_candidate_decision_packet":
            gc_keys = ["queue_id", "priority", "strategy_id", "strategy_version", "requested_by", "created_at"]
            _validate_required_fields(item, gc_keys, task_type)

            budget = _normalize_budget(item)
            permissions, blockers = _normalize_permissions(db_path, item)
            if blockers:
                raise SchemaValidationError("; ".join(blockers))

            _validate_permission_requirement(permissions, "allow_lifecycle_apply", False, task_type)
            _validate_permission_requirement(permissions, "allow_final_holdout", False, task_type)
            _validate_permission_requirement(permissions, "allow_runner_execution", False, task_type)

            validated = _build_validated_item(item, budget=budget, permissions=permissions, sweep_config=None)
            if persist:
                _persist_validated_item(db_path, validated, source_path=queue_doc["source_path"])
            validated_items.append(validated)
            continue

        if task_type in ("generated_final_holdout_experiment", "generated_final_holdout_review"):
            gfh_keys = ["queue_id", "priority", "strategy_id", "strategy_version", "requested_by", "created_at"]
            if task_type == "generated_final_holdout_experiment":
                gfh_keys.extend(["hypothesis_id", "dataset_id", "implementation_request_id", "approval_id"])
            if task_type == "generated_final_holdout_review":
                gfh_keys.append("parent_experiment_id")
            _validate_required_fields(item, gfh_keys, task_type)

            budget = _normalize_budget(item)
            permissions, blockers = _normalize_permissions(db_path, item)
            if blockers:
                raise SchemaValidationError("; ".join(blockers))

            if task_type == "generated_final_holdout_experiment":
                _validate_permission_requirement(permissions, "allow_final_holdout", True, task_type)
                _validate_permission_requirement(permissions, "allow_lifecycle_apply", False, task_type)
                if budget["max_experiments"] < 1:
                    raise SchemaValidationError("generated_final_holdout_experiment requires budget.max_experiments >= 1")

            validated = _build_validated_item(item, budget=budget, permissions=permissions, sweep_config=None)
            if persist:
                _persist_validated_item(db_path, validated, source_path=queue_doc["source_path"])
            validated_items.append(validated)
            continue

        _validate_required_fields(item, ["queue_id", "priority", "hypothesis_id", "strategy_id", "requested_by", "created_at"])

        if not isinstance(item["priority"], int):
            raise SchemaValidationError("queue.priority must be an integer")

        hypothesis_path = resolve_hypothesis_file(str(item["hypothesis_id"]))
        if not hypothesis_path:
            raise SchemaValidationError(f"referenced hypothesis does not exist: {item['hypothesis_id']}")
        validate_hypothesis(load_yaml(hypothesis_path))
        spec = _validated_strategy(str(item["strategy_id"]))
        if spec["hypothesis_id"] != item["hypothesis_id"]:
            raise SchemaValidationError("queue.hypothesis_id does not match strategy spec")

        parent_id = item.get("parent_experiment_id")
        if parent_id and not registry.get_experiment(db_path, parent_id):
            raise SchemaValidationError(f"parent experiment does not exist: {parent_id}")

        budget = _normalize_budget(item)
        permissions, blockers = _normalize_permissions(db_path, item)
        if blockers:
            raise SchemaValidationError("; ".join(blockers))

        sweep_config = _load_sweep_config(item)
        if task_type in {"parameter_robustness", "cost_stress"}:
            if not sweep_config:
                raise SchemaValidationError(f"{task_type} requires sweep_config")
            sweep_config.setdefault("sweep_type", task_type)
            sweep_config.setdefault("parent_experiment_id", parent_id)
            sweep_config.setdefault("strategy_id", item["strategy_id"])
            sweep_budget = dict(sweep_config.get("budget") or {})
            sweep_budget.setdefault("max_child_experiments", budget["max_child_experiments"])
            sweep_budget.setdefault("max_parameters_changed_per_child", budget["max_parameters_changed_per_child"])
            sweep_budget.setdefault("require_one_variable_at_a_time", budget["require_one_variable_at_a_time"])
            sweep_config["budget"] = sweep_budget
            sweeps.validate_sweep_config(sweep_config)
            if sweep_config.get("parent_experiment_id"):
                sweeps.plan_sweep(db_path, sweep_config)
        _enforce_precreation_budget(db_path, item, budget, sweep_config)

        validated = _build_validated_item(item, budget=budget, permissions=permissions, sweep_config=sweep_config)
        if persist:
            _persist_validated_item(db_path, validated, source_path=queue_doc["source_path"])
        if task_type == "cost_stress":
            warnings.append(f"{validated['queue_id']}: cost stress is scaffolded unless runner-safe cost materialization exists")
        validated_items.append(validated)
    return {
        "status": "valid",
        "queue_id": queue_doc["queue_id"],
        "source_path": queue_doc["source_path"],
        "items": sorted(validated_items, key=lambda item: (item["priority"], item["queue_id"])),
        "warnings": warnings,
    }


def _enforce_precreation_budget(
    db_path: str | Path,
    item: dict[str, Any],
    budget: dict[str, Any],
    sweep_config: dict[str, Any] | None,
) -> None:
    task_type = item["task_type"]
    if task_type == "baseline_experiment" and budget["max_experiments"] < 1:
        raise SchemaValidationError("baseline_experiment requires budget.max_experiments >= 1")
    if task_type == "generated_baseline_experiment" and budget["max_experiments"] < 1:
        raise SchemaValidationError("generated_baseline_experiment requires budget.max_experiments >= 1")
    if task_type in {"parameter_robustness", "cost_stress", "generated_robustness_sweep"}:
        if budget["max_sweeps"] < 1:
            raise SchemaValidationError(f"{task_type} requires budget.max_sweeps >= 1")
        if not sweep_config:
            raise SchemaValidationError(f"{task_type} requires sweep_config")
        if sweep_config.get("parent_experiment_id"):
            plan = sweeps.plan_sweep(db_path, sweep_config)
            child_count = len(plan["children"])
            if child_count > budget["max_child_experiments"]:
                raise SchemaValidationError(
                    f"{task_type} plans {child_count} children but budget.max_child_experiments is {budget['max_child_experiments']}"
                )


def plan_queue_run(db_path: str | Path, queue_path: str | Path) -> dict[str, Any]:
    validation_result = validate_queue(db_path, queue_path, persist=False)
    planned_items: list[dict[str, Any]] = []
    blocked_actions = []
    totals = {"experiments": 0, "sweeps": 0, "max_runtime_minutes": 0}
    for item in validation_result["items"]:
        budget = item["budget"]
        totals["max_runtime_minutes"] += budget["max_runtime_minutes"]
        planned: dict[str, Any] = {
            "queue_id": item["queue_id"],
            "priority": item["priority"],
            "task_type": item["task_type"],
            "permissions_used": {key: value for key, value in item["permissions"].items() if value is True},
            "blocked_actions": _blocked_actions_for_item(item),
            "budget": budget,
            "experiments_would_create": [],
            "sweeps_would_prepare": [],
        }
        if item["task_type"] == "baseline_experiment":
            totals["experiments"] += 1
            planned["experiments_would_create"].append(f"EXP_<timestamp>_{_slug(item['queue_id'])}")
        elif item["task_type"] == "generated_baseline_experiment":
            totals["experiments"] += 1
            planned["experiments_would_create"].append(f"EXP_<timestamp>_{_slug(item['queue_id'])}")
        elif item["task_type"] in ("generated_baseline_review", "generated_robustness_review", "generated_candidate_decision_packet"):
            planned["experiments_would_create"] = []
        elif item["task_type"] in {"parameter_robustness", "cost_stress", "generated_robustness_sweep"}:
            plan = sweeps.plan_sweep(db_path, item["sweep_config"])
            totals["sweeps"] += 1
            totals["experiments"] += len(plan["children"])
            planned["sweeps_would_prepare"].append(
                {
                    "sweep_type": plan["sweep_type"],
                    "parent_experiment_id": plan["parent_experiment_id"],
                    "child_count": len(plan["children"]),
                    "warnings": plan["warnings"],
                }
            )
        elif item["task_type"] in AGENT_REVIEW_TASKS:
            planned["experiments_would_create"] = []
        elif item["task_type"] in INTAKE_TASK_TYPES:
            planned["experiments_would_create"] = []
            planned["intake_artifact_generation"] = True
        blocked_actions.extend(planned["blocked_actions"])
        planned_items.append(planned)
    return {
        "dry_run": True,
        "status": "planned",
        "queue_id": validation_result["queue_id"],
        "tasks_that_would_run": planned_items,
        "blocked_actions": blocked_actions,
        "budget_estimate": totals,
        "warnings": validation_result["warnings"],
    }


def _blocked_actions_for_item(item: dict[str, Any]) -> list[str]:
    blocked = [
        "edit_mql5",
        "change_metric_definitions",
        "weaken_validation_thresholds",
        "mutate_dataset",
        "apply_lifecycle_transition",
        "use_final_holdout",
        "delete_failed_experiments",
        "overwrite_artifacts",
    ]
    if not item["permissions"].get("allow_runner_execution"):
        blocked.append("runner_execution")
    return blocked


def run_queue(
    db_path: str | Path,
    queue_path: str | Path,
    *,
    mode: str = "overnight",
    dry_run: bool = False,
    runner_script: str | Path = REPO_ROOT / "automated" / "scripts" / "run_backtest.sh",
    output_root: str | Path = QUEUE_RUN_ROOT,
) -> dict[str, Any]:
    if dry_run:
        return plan_queue_run(db_path, queue_path)
    validation_result = validate_queue(db_path, queue_path)
    queue_run_id = _queue_run_id(validation_result["queue_id"])
    queue_run_dir = Path(output_root) / queue_run_id
    queue_run_dir.mkdir(parents=True, exist_ok=False)
    summary: dict[str, Any] = {
        "schema_version": "research_queue_run_v1",
        "queue_run_id": queue_run_id,
        "queue_id": validation_result["queue_id"],
        "source_path": validation_result["source_path"],
        "mode": mode,
        "started_at": registry.utc_now(),
        "completed_at": None,
        "status": "running",
        "items": [],
        "experiments_created": [],
        "sweeps_created": [],
        "artifacts_created": [],
        "warnings": list(validation_result["warnings"]),
        "failures": [],
        "blocked_actions": [],
        "lifecycle_transition_proposals": [],
        "item_status_history": [],
        "budget_usage": {
            "experiments_created": 0,
            "sweeps_created": 0,
            "failed_runs": 0,
        },
    }
    for item in validation_result["items"]:
        _append_status(summary, item["queue_id"], item["status"])
    registry.create_queue_run(
        db_path,
        {
            "queue_run_id": queue_run_id,
            "queue_id": validation_result["queue_id"],
            "status": "running",
            "mode": mode,
            "started_at": summary["started_at"],
            "completed_at": None,
            "source_path": validation_result["source_path"],
        },
    )
    for item in validation_result["items"]:
        if summary["budget_usage"]["failed_runs"] > item["budget"]["max_failed_runs"]:
            _append_status(summary, item["queue_id"], "skipped")
            _record_item(summary, item, "skipped", warnings=["failure budget already exceeded"])
            registry.update_queue_item(db_path, item["queue_id"], status="skipped")
            continue
        _append_status(summary, item["queue_id"], "running")
        registry.update_queue_item(db_path, item["queue_id"], status="running")
        try:
            item_result = _execute_item(
                db_path,
                item,
                queue_run_dir,
                runner_script=runner_script,
                research_output_root=queue_run_dir / "experiments",
            )
            _merge_item_result(summary, item_result)
            status = item_result["status"]
            _append_status(summary, item["queue_id"], status)
            registry.update_queue_item(db_path, item["queue_id"], status=status)
        except Exception as exc:
            summary["budget_usage"]["failed_runs"] += 1
            failure = {"queue_id": item["queue_id"], "error": str(exc)}
            summary["failures"].append(failure)
            _append_status(summary, item["queue_id"], "failed")
            _record_item(summary, item, "failed", failures=[str(exc)])
            registry.update_queue_item(db_path, item["queue_id"], status="failed")
        if summary["budget_usage"]["failed_runs"] > item["budget"]["max_failed_runs"]:
            summary["warnings"].append(f"failure budget exceeded after {item['queue_id']}; remaining items skipped")
    summary["completed_at"] = registry.utc_now()
    summary["status"] = "failed" if summary["failures"] else "completed_with_warnings" if summary["warnings"] else "completed"
    summary_path = queue_run_dir / "queue_run_summary.json"
    _write_json_new(summary_path, summary)
    registry.update_queue_run(
        db_path,
        queue_run_id,
        status=summary["status"],
        completed_at=summary["completed_at"],
        experiments_created=summary["experiments_created"],
        sweeps_created=summary["sweeps_created"],
        artifacts_created=summary["artifacts_created"],
        warnings=summary["warnings"],
        failures=summary["failures"],
        summary_path=str(summary_path),
    )
    return summary


def _record_item(
    summary: dict[str, Any],
    item: dict[str, Any],
    status: str,
    *,
    experiments: list[str] | None = None,
    sweeps_created: list[str] | None = None,
    artifacts: list[str] | None = None,
    warnings: list[str] | None = None,
    failures: list[str] | None = None,
    blocked_actions: list[str] | None = None,
    lifecycle_transition_proposals: list[str] | None = None,
) -> dict[str, Any]:
    item_summary = {
        "queue_id": item["queue_id"],
        "task_type": item["task_type"],
        "status": status,
        "experiments_created": experiments or [],
        "sweeps_created": sweeps_created or [],
        "artifacts_created": artifacts or [],
        "warnings": warnings or [],
        "failures": failures or [],
        "blocked_actions": blocked_actions or _blocked_actions_for_item(item),
        "lifecycle_transition_proposals": lifecycle_transition_proposals or [],
    }
    summary["items"].append(item_summary)
    return item_summary


def _append_status(summary: dict[str, Any], queue_id: str, status: str) -> None:
    summary["item_status_history"].append(
        {
            "queue_id": queue_id,
            "status": status,
            "at": registry.utc_now(),
        }
    )


def _merge_item_result(summary: dict[str, Any], item_result: dict[str, Any]) -> None:
    item = item_result["item"]
    _record_item(
        summary,
        item,
        item_result["status"],
        experiments=item_result.get("experiments_created"),
        sweeps_created=item_result.get("sweeps_created"),
        artifacts=item_result.get("artifacts_created"),
        warnings=item_result.get("warnings"),
        failures=item_result.get("failures"),
        blocked_actions=item_result.get("blocked_actions"),
        lifecycle_transition_proposals=item_result.get("lifecycle_transition_proposals"),
    )
    for key in ["experiments_created", "sweeps_created", "artifacts_created", "warnings", "failures", "blocked_actions"]:
        summary[key].extend(item_result.get(key, []))
    summary["lifecycle_transition_proposals"].extend(item_result.get("lifecycle_transition_proposals", []))
    summary["budget_usage"]["experiments_created"] += len(item_result.get("experiments_created", []))
    summary["budget_usage"]["sweeps_created"] += len(item_result.get("sweeps_created", []))
    summary["budget_usage"]["failed_runs"] += len(item_result.get("failures", []))


def _execute_item(
    db_path: str | Path,
    item: dict[str, Any],
    queue_run_dir: Path,
    *,
    runner_script: str | Path,
    research_output_root: Path,
) -> dict[str, Any]:
    task_type = item["task_type"]
    if task_type == "baseline_experiment":
        return _execute_baseline(db_path, item, queue_run_dir, runner_script=runner_script, research_output_root=research_output_root)
    if task_type in {"parameter_robustness", "cost_stress"}:
        return _execute_sweep_item(db_path, item, queue_run_dir, runner_script=runner_script, research_output_root=research_output_root)
    if task_type == "portfolio_review":
        return _execute_portfolio_review(db_path, item, queue_run_dir)
    if task_type == "red_team_review":
        return _execute_review_request(db_path, item, queue_run_dir, "red_team_reviewer")
    if task_type == "research_summary":
        return _execute_research_summary(db_path, item, queue_run_dir)
    if task_type == "implementation_request":
        return _execute_impl_request(db_path, item, queue_run_dir)
    if task_type == "implementation_compile_check":
        return _execute_impl_compile_check(db_path, item, queue_run_dir)
    if task_type == "implementation_review":
        return _execute_impl_review(db_path, item, queue_run_dir)
    if task_type == "hypothesis_generation":
        return _execute_hypothesis_generation(db_path, item, queue_run_dir)
    if task_type == "strategy_spec_generation":
        return _execute_strategy_spec_generation(db_path, item, queue_run_dir)
    if task_type == "implementation_materialization":
        return _execute_implementation_materialization(db_path, item, queue_run_dir)
    if task_type == "research_review_packet":
        return _execute_research_review_packet(db_path, item, queue_run_dir)
    if task_type == "generated_baseline_experiment":
        return _execute_generated_baseline(db_path, item, queue_run_dir, runner_script=runner_script, research_output_root=research_output_root)
    if task_type == "generated_baseline_review":
        return _execute_generated_baseline_review(db_path, item, queue_run_dir)
    if task_type == "generated_robustness_sweep":
        return _execute_generated_robustness_sweep(db_path, item, queue_run_dir, runner_script=runner_script, research_output_root=research_output_root)
    if task_type == "generated_robustness_review":
        return _execute_generated_robustness_review(db_path, item, queue_run_dir)
    if task_type == "generated_candidate_decision_packet":
        return _execute_generated_candidate_decision_packet(db_path, item, queue_run_dir)
    if task_type == "generated_final_holdout_experiment":
        return _execute_generated_final_holdout_experiment(db_path, item, queue_run_dir, runner_script=runner_script, research_output_root=research_output_root)
    if task_type == "generated_final_holdout_review":
        return _execute_generated_final_holdout_review(db_path, item, queue_run_dir)
    raise ValueError(f"unsupported task type: {task_type}")


def _base_item_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "item": item,
        "status": "completed",
        "experiments_created": [],
        "sweeps_created": [],
        "artifacts_created": [],
        "warnings": [],
        "failures": [],
        "blocked_actions": _blocked_actions_for_item(item),
        "lifecycle_transition_proposals": [],
    }


def _dataset_id_for_item(db_path: str | Path, item: dict[str, Any]) -> str | None:
    if item.get("dataset_id"):
        return str(item["dataset_id"])
    sweep_config = item.get("sweep_config") or {}
    if sweep_config.get("dataset_id"):
        return str(sweep_config["dataset_id"])
    parent_id = item.get("parent_experiment_id")
    if parent_id:
        parent = registry.get_experiment(db_path, parent_id)
        if parent:
            return parent.get("dataset_id")
    return None


def _execute_baseline(
    db_path: str | Path,
    item: dict[str, Any],
    queue_run_dir: Path,
    *,
    runner_script: str | Path,
    research_output_root: Path,
) -> dict[str, Any]:
    result = _base_item_result(item)
    if item["budget"]["max_experiments"] < 1:
        result["status"] = "skipped"
        result["warnings"].append("max_experiments is 0")
        return result
    dataset_id = _dataset_id_for_item(db_path, item)
    if not dataset_id:
        result["status"] = "failed"
        result["failures"].append("baseline_experiment requires dataset_id or parent_experiment_id with dataset")
        return result
    experiment_id = f"EXP_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{_slug(item['queue_id'])[:40]}_{uuid.uuid4().hex[:6].upper()}"
    context = runner.prepare_run(
        db_path=db_path,
        strategy_spec_path=_strategy_spec_path(item["strategy_id"]),
        dataset_id=dataset_id,
        experiment_id=experiment_id,
        output_root=research_output_root,
        run_reason="agent",
        created_by=f"research_queue:{item['queue_id']}",
        change_type="baseline",
        change_summary=f"Queue baseline experiment for {item['queue_id']}.",
        rationale=item.get("notes") or "Autonomous queue prepared an approved baseline experiment.",
        parent_experiment_id=item.get("parent_experiment_id"),
    )
    result["experiments_created"].append(experiment_id)
    if item["permissions"]["allow_runner_execution"]:
        run_result = runner.run_prepared_experiment(
            db_path=db_path,
            experiment_id=experiment_id,
            research_output_dir=context["output_dir"],
            runner_script=runner_script,
        )
        if run_result["returncode"] != 0:
            result["status"] = "failed"
            result["failures"].append(f"{experiment_id} runner returned {run_result['returncode']}")
    else:
        result["status"] = "completed_with_warnings"
        result["warnings"].append("runner execution was not permitted; experiment prepared only")
    _maybe_propose_lifecycle(db_path, item, experiment_id, queue_run_dir, result)
    return result


def _execute_generated_baseline(
    db_path: str | Path,
    item: dict[str, Any],
    queue_run_dir: Path,
    *,
    runner_script: str | Path,
    research_output_root: Path,
) -> dict[str, Any]:
    result = _base_item_result(item)
    if item["budget"]["max_experiments"] < 1:
        result["status"] = "skipped"
        result["warnings"].append("max_experiments is 0")
        return result

    strategy_id = item["strategy_id"]
    strategy_version = item.get("strategy_version", "v1")
    impl_request_id = item.get("implementation_request_id", "")

    if not impl_request_id:
        result["status"] = "failed"
        result["failures"].append("generated_baseline_experiment requires implementation_request_id")
        return result

    request = registry.get_implementation_request(db_path, impl_request_id)
    if not request:
        result["status"] = "failed"
        result["failures"].append(f"implementation request not found: {impl_request_id}")
        return result

    implementations = registry.list_implementations(db_path, impl_request_id)
    if not implementations:
        result["status"] = "failed"
        result["failures"].append("No implementation record found; compile-check must be run first")
        return result

    current_impl = implementations[-1]

    errors: list[str] = []

    compile_status = current_impl.get("compile_status")
    if not compile_status:
        errors.append("Compile status is missing; run compile-check first")
    elif compile_status == "failed":
        errors.append("Compile status is 'failed'; cannot use for baseline")
    elif compile_status == "mock_checked":
        errors.append("Compile was mock-checked; real compile 'passed' required for generated baseline")

    generated_mq5 = current_impl.get("generated_mq5_path", "")
    if generated_mq5:
        mq5_path = Path(generated_mq5)
        try:
            impl_mod.assert_sandbox_path(mq5_path)
        except SchemaValidationError as exc:
            errors.append(str(exc))
        if not mq5_path.is_file():
            errors.append(f"Generated .mq5 file not found: {generated_mq5}")
    else:
        errors.append("No generated .mq5 path recorded")

    impl_req_dir = impl_mod.IMPL_REQUESTS_DIR / impl_request_id
    review_path = impl_req_dir / "diff_review.yaml"
    if not review_path.is_file():
        errors.append("Diff review artifact not found; run diff-review first")

    input_match = current_impl.get("input_match_status")
    if not input_match:
        errors.append("Input match status is not set; run diff-review first")
    elif input_match == "mismatch":
        errors.append("Input match status is 'mismatch'; cannot use for baseline")

    if not current_impl.get("approved_for_baseline"):
        errors.append("Not approved for baseline; run approve-for-baseline first")

    approval_scope = current_impl.get("approval_scope", "baseline_only")
    if approval_scope != "baseline_only":
        errors.append(f"Approval scope is '{approval_scope}'; 'baseline_only' required for generated baseline")

    usage_count = registry.count_approval_usage_for_implementation(db_path, current_impl["implementation_id"])
    allow_reuse = bool(current_impl.get("allow_reuse"))
    if usage_count > 0 and not allow_reuse:
        errors.append(
            f"Approval already consumed ({usage_count} usage(s)). "
            "Reuse requires explicit allow_reuse flag on approval."
        )

    if errors:
        result["status"] = "failed"
        result["failures"].extend(errors)
        return result

    dataset_id = item.get("dataset_id", "")
    if not dataset_id:
        result["status"] = "failed"
        result["failures"].append("dataset_id is required")
        return result

    experiment_id = (
        f"EXP_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        f"_{_slug(item['queue_id'])[:40]}_{uuid.uuid4().hex[:6].upper()}"
    )
    spec_path = _generated_strategy_spec_path(strategy_id)

    try:
        context = runner.prepare_run(
            db_path=db_path,
            strategy_spec_path=spec_path,
            dataset_id=dataset_id,
            experiment_id=experiment_id,
            output_root=research_output_root,
            run_reason="agent",
            created_by=f"research_queue:{item['queue_id']}",
            change_type="baseline",
            change_summary=f"Queue generated baseline experiment for {item['queue_id']}.",
            rationale=item.get("notes") or "Autonomous queue prepared an approved generated baseline experiment.",
        )
    except Exception as exc:
        result["status"] = "failed"
        result["failures"].append(str(exc))
        return result

    usage_id = f"USAGE_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8].upper()}"
    usage_record = {
        "usage_id": usage_id,
        "implementation_id": current_impl["implementation_id"],
        "implementation_request_id": impl_request_id,
        "experiment_id": experiment_id,
        "queue_run_id": item.get("queue_id", ""),
        "used_at": registry.utc_now(),
        "runner_mode": "baseline",
        "status": "pending",
    }
    registry.create_approval_usage(db_path, usage_record)
    result["experiments_created"].append(experiment_id)
    result["artifacts_created"].append(usage_id)

    usage_status = "completed"
    if item["permissions"]["allow_runner_execution"]:
        try:
            run_result = runner.run_prepared_experiment(
                db_path=db_path,
                experiment_id=experiment_id,
                research_output_dir=context["output_dir"],
                runner_script=runner_script,
            )
            if run_result["returncode"] != 0:
                result["status"] = "failed"
                result["failures"].append(f"{experiment_id} runner returned {run_result['returncode']}")
                usage_status = "failed"
        except Exception as exc:
            result["status"] = "failed"
            result["failures"].append(str(exc))
            usage_status = "failed"
    else:
        result["status"] = "completed_with_warnings"
        result["warnings"].append("runner execution was not permitted; experiment prepared only")
        usage_status = "completed"

    registry.update_approval_usage(db_path, usage_id, status=usage_status)
    return result


def _execute_generated_baseline_review(
    db_path: str | Path,
    item: dict[str, Any],
    queue_run_dir: Path,
) -> dict[str, Any]:
    result = _base_item_result(item)
    experiment_id = item.get("parent_experiment_id")
    if not experiment_id:
        result["status"] = "failed"
        result["failures"].append("generated_baseline_review requires parent_experiment_id")
        return result

    experiment = registry.get_experiment(db_path, experiment_id)
    if not experiment:
        result["status"] = "failed"
        result["failures"].append(f"experiment not found: {experiment_id}")
        return result

    strategy_id = item["strategy_id"]
    strategy_version = item.get("strategy_version", "v1")

    request = registry.find_implementation_request(db_path, strategy_id, strategy_version)
    impl_request_id = request["implementation_request_id"] if request else None

    implementations = []
    current_impl = None
    if request:
        implementations = registry.list_implementations(db_path, request["implementation_request_id"])
        if implementations:
            current_impl = implementations[-1]

    implementation_id = current_impl["implementation_id"] if current_impl else None
    approval_status = "not_approved"
    if current_impl and current_impl.get("approved_for_baseline"):
        approval_status = "approved_for_baseline"

    approval_usage = None
    if current_impl:
        usages = registry.list_approval_usage_for_implementation(db_path, current_impl["implementation_id"])
        if usages:
            approval_usage = usages[-1]

    try:
        review = generated_baseline.build_generated_baseline_review_packet(
            db_path,
            experiment_id=experiment_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            implementation_request_id=impl_request_id,
            implementation_id=implementation_id,
            approval_status=approval_status,
            approval_usage=dict(approval_usage) if approval_usage else None,
            runner_mode="queue",
            output_dir=queue_run_dir / "generated_baseline_reviews",
        )
        result["artifacts_created"].append(review["packet_path"])
        rec = review["packet"]["recommendation"]
        result["warnings"].append(f"generated_baseline_review: recommendation={rec}")
        if review["packet"].get("warnings"):
            result["warnings"].extend(review["packet"]["warnings"])
        if review["packet"].get("red_team_results", {}).get("warnings"):
            result["warnings"].extend(
                f"red-team: {w}" for w in review["packet"]["red_team_results"]["warnings"]
            )
    except Exception as exc:
        result["status"] = "failed"
        result["failures"].append(str(exc))
        return result

    return result


def _execute_generated_robustness_sweep(
    db_path: str | Path,
    item: dict[str, Any],
    queue_run_dir: Path,
    *,
    runner_script: str | Path,
    research_output_root: Path,
) -> dict[str, Any]:
    result = _base_item_result(item)
    if item["budget"]["max_sweeps"] < 1:
        result["status"] = "skipped"
        result["warnings"].append("max_sweeps is 0")
        return result

    strategy_id = item["strategy_id"]
    strategy_version = item.get("strategy_version", "v1")
    impl_request_id = item.get("implementation_request_id", "")
    baseline_experiment_id = item.get("baseline_experiment_id", "")

    if not impl_request_id:
        result["status"] = "failed"
        result["failures"].append("generated_robustness_sweep requires implementation_request_id")
        return result
    if not baseline_experiment_id:
        result["status"] = "failed"
        result["failures"].append("generated_robustness_sweep requires baseline_experiment_id")
        return result

    eligibility = generated_robustness.require_generated_robustness_eligibility(
        db_path, strategy_id, strategy_version, allow_mock_compile=True,
    )
    if not eligibility["eligible"]:
        result["status"] = "failed"
        result["failures"].extend(eligibility["errors"])
        return result

    config = dict(item["sweep_config"])
    config.setdefault("sweep_id", (
        f"SWEEP_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        f"_{_slug(item['queue_id'])[:40]}_{uuid.uuid4().hex[:6].upper()}"
    ))
    config_path = queue_run_dir / "queue_inputs" / f"{_slug(item['queue_id'])}_sweep.yaml"
    _write_text_new(config_path, yaml.safe_dump(config, sort_keys=False))

    try:
        prepared = sweeps.prepare_sweep(
            db_path,
            config_path=config_path,
            output_root=research_output_root,
            created_by=f"research_queue:{item['queue_id']}",
        )
    except Exception as exc:
        result["status"] = "failed"
        result["failures"].append(str(exc))
        return result

    result["sweeps_created"].append(prepared["sweep_id"])
    result["experiments_created"].extend(child["child_experiment_id"] for child in prepared["children"])
    if prepared.get("warnings"):
        result["warnings"].extend(prepared["warnings"])

    run_result = sweeps.run_sweep(
        db_path,
        sweep_id=prepared["sweep_id"],
        limit=item["budget"]["max_child_experiments"],
        continue_on_error=True,
        runner_script=runner_script,
        output_root=research_output_root,
    )
    for child_result in run_result.get("results", []):
        if child_result.get("returncode") not in (None, 0) or child_result.get("error"):
            result["failures"].append(
                f"{child_result.get('child_experiment_id')}: {child_result.get('error') or child_result.get('returncode')}"
            )
    if run_result.get("status") == "failed":
        result["status"] = "failed"

    summary_path = (
        queue_run_dir / "sweep_summaries" / f"{prepared['sweep_id']}_summary.json"
    )
    try:
        sweep_summary = sweeps.summarize_sweep(
            db_path, sweep_id=prepared["sweep_id"], output_path=summary_path,
        )
        result["artifacts_created"].append(str(summary_path))
        if sweep_summary.get("status") in {"warn", "not_available", "not_implemented"} and result["status"] == "completed":
            result["status"] = "completed_with_warnings"
    except Exception as exc:
        result["warnings"].append(f"sweep summary failed: {exc}")

    return result


def _execute_generated_robustness_review(
    db_path: str | Path,
    item: dict[str, Any],
    queue_run_dir: Path,
) -> dict[str, Any]:
    result = _base_item_result(item)
    sweep_id = item.get("parent_experiment_id", "")
    if not sweep_id:
        result["status"] = "failed"
        result["failures"].append("generated_robustness_review requires parent_experiment_id (sweep_id)")
        return result

    strategy_id = item["strategy_id"]
    strategy_version = item.get("strategy_version", "v1")

    request = registry.find_implementation_request(db_path, strategy_id, strategy_version)
    impl_request_id = request["implementation_request_id"] if request else None

    implementations = []
    current_impl = None
    if request:
        implementations = registry.list_implementations(db_path, request["implementation_request_id"])
        if implementations:
            current_impl = implementations[-1]
    implementation_id = current_impl["implementation_id"] if current_impl else None

    try:
        review = generated_robustness.build_generated_robustness_review_packet(
            db_path,
            sweep_id=sweep_id,
            baseline_experiment_id=item.get("baseline_experiment_id", ""),
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            implementation_request_id=impl_request_id,
            implementation_id=implementation_id,
            output_dir=queue_run_dir / "generated_robustness_reviews",
        )
        result["artifacts_created"].append(review["packet_path"])
        rec = review["packet"]["recommendation"]
        result["warnings"].append(f"generated_robustness_review: recommendation={rec}")
        if review["packet"].get("robustness_warnings"):
            result["warnings"].extend(
                f"robustness: {w}" for w in review["packet"]["robustness_warnings"]
            )
    except Exception as exc:
        result["status"] = "failed"
        result["failures"].append(str(exc))
        return result

    return result


def _execute_generated_candidate_decision_packet(
    db_path: str | Path,
    item: dict[str, Any],
    queue_run_dir: Path,
) -> dict[str, Any]:
    result = _base_item_result(item)
    strategy_id = item["strategy_id"]
    strategy_version = item.get("strategy_version", "v1")
    impl_request_id = item.get("implementation_request_id")
    baseline_experiment_id = item.get("baseline_experiment_id")
    robustness_sweep_id = item.get("robustness_sweep_id")

    output_dir = queue_run_dir / "generated_candidate_decision_packets"
    try:
        outcome = generated_candidate.build_generated_candidate_decision_packet(
            db_path,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            implementation_request_id=impl_request_id,
            baseline_experiment_id=baseline_experiment_id,
            robustness_sweep_id=robustness_sweep_id,
            output_dir=output_dir,
        )
        result["artifacts_created"].append(outcome["packet_path"])
        packet = outcome["packet"]
        result["warnings"].append(
            f"generated_candidate_decision_packet: "
            f"proposed_next_action={packet['proposed_next_action']}, "
            f"lifecycle_proposal={packet['lifecycle_proposal']}"
        )
        if packet.get("unresolved_warnings"):
            result["warnings"].extend(packet["unresolved_warnings"])
    except Exception as exc:
        result["status"] = "failed"
        result["failures"].append(str(exc))
        return result

    return result


def _execute_generated_final_holdout_experiment(
    db_path: str | Path,
    item: dict[str, Any],
    queue_run_dir: Path,
    *,
    runner_script: str | Path,
    research_output_root: Path,
) -> dict[str, Any]:
    result = _base_item_result(item)
    if item["budget"]["max_experiments"] < 1:
        result["status"] = "skipped"
        result["warnings"].append("max_experiments is 0")
        return result

    strategy_id = item["strategy_id"]
    strategy_version = item.get("strategy_version", "v1")
    impl_request_id = item.get("implementation_request_id", "")
    dataset_id = item.get("dataset_id", "")
    approval_id = item.get("approval_id", "")

    if not impl_request_id:
        result["status"] = "failed"
        result["failures"].append("generated_final_holdout_experiment requires implementation_request_id")
        return result
    if not approval_id:
        result["status"] = "failed"
        result["failures"].append("generated_final_holdout_experiment requires approval_id")
        return result

    approval = registry.get_scope_approval(db_path, approval_id)
    approval_error = check_approval_usable(approval, approval_id=approval_id)
    if approval_error:
        result["status"] = "failed"
        result["failures"].append(approval_error)
        return result
    if approval.get("strategy_id") != strategy_id:
        result["status"] = "failed"
        result["failures"].append(f"approval strategy_id mismatch: {approval.get('strategy_id')} != {strategy_id}")
        return result
    if approval.get("approval_scope") != "final_holdout_only":
        result["status"] = "failed"
        result["failures"].append(f"approval_scope must be final_holdout_only, got {approval.get('approval_scope')}")
        return result

    eligibility = generated_final_holdout.require_generated_final_holdout_eligibility(
        db_path, strategy_id, strategy_version, allow_mock_compile=True,
    )
    if not eligibility["eligible"]:
        result["status"] = "failed"
        result["failures"].extend(eligibility["errors"])
        return result

    experiment_id = (
        f"FH_EXP_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        f"_{_slug(item['queue_id'])[:40]}_{uuid.uuid4().hex[:6].upper()}"
    )
    spec_path = _generated_strategy_spec_path(strategy_id)

    try:
        context = runner.prepare_run(
            db_path=db_path,
            strategy_spec_path=spec_path,
            dataset_id=dataset_id,
            experiment_id=experiment_id,
            output_root=research_output_root,
            run_reason="agent",
            created_by=f"research_queue:{item['queue_id']}",
            change_type="final_holdout",
            change_summary=f"Queue final holdout experiment for {item['queue_id']}.",
            rationale=item.get("notes") or "Queue final holdout experiment.",
        )
    except Exception as exc:
        result["status"] = "failed"
        result["failures"].append(str(exc))
        return result

    usage_id = f"USAGE_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8].upper()}"
    impls = registry.list_implementations(db_path, impl_request_id)
    current_impl = impls[-1] if impls else {}
    usage_record = {
        "usage_id": usage_id,
        "implementation_id": current_impl.get("implementation_id", ""),
        "implementation_request_id": impl_request_id,
        "experiment_id": experiment_id,
        "queue_run_id": item.get("queue_id", ""),
        "used_at": registry.utc_now(),
        "runner_mode": "final_holdout",
        "status": "pending",
        "scope_approval_id": approval_id,
    }
    registry.create_approval_usage(db_path, usage_record)
    registry.update_scope_approval_used(db_path, approval_id, experiment_id)
    result["experiments_created"].append(experiment_id)
    result["artifacts_created"].append(usage_id)

    usage_status = "completed"
    if item["permissions"]["allow_runner_execution"]:
        try:
            run_result = runner.run_prepared_experiment(
                db_path=db_path,
                experiment_id=experiment_id,
                research_output_dir=context["output_dir"],
                runner_script=runner_script,
            )
            if run_result["returncode"] != 0:
                result["status"] = "failed"
                result["failures"].append(f"{experiment_id} runner returned {run_result['returncode']}")
                usage_status = "failed"
        except Exception as exc:
            result["status"] = "failed"
            result["failures"].append(str(exc))
            usage_status = "failed"
    else:
        result["status"] = "completed_with_warnings"
        result["warnings"].append("runner execution was not permitted; experiment prepared only")
        usage_status = "completed"

    registry.update_approval_usage(db_path, usage_id, status=usage_status)
    return result


def _execute_generated_final_holdout_review(
    db_path: str | Path,
    item: dict[str, Any],
    queue_run_dir: Path,
) -> dict[str, Any]:
    result = _base_item_result(item)
    experiment_id = item.get("parent_experiment_id", "")
    if not experiment_id:
        result["status"] = "failed"
        result["failures"].append("generated_final_holdout_review requires parent_experiment_id")
        return result

    strategy_id = item["strategy_id"]
    strategy_version = item.get("strategy_version", "v1")

    try:
        review = generated_final_holdout.build_generated_final_holdout_review_packet(
            db_path,
            experiment_id=experiment_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            approval_id=item.get("approval_id"),
            output_dir=queue_run_dir / "generated_final_holdout_reviews",
        )
        result["artifacts_created"].append(review["packet_path"])
        review_status = review["packet"]["status"]
        result["warnings"].append(f"generated_final_holdout_review: status={review_status}")
        if review["packet"].get("warnings"):
            result["warnings"].extend(f"final_holdout: {w}" for w in review["packet"]["warnings"])
    except Exception as exc:
        result["status"] = "failed"
        result["failures"].append(str(exc))
        return result

    return result


def _execute_sweep_item(
    db_path: str | Path,
    item: dict[str, Any],
    queue_run_dir: Path,
    *,
    runner_script: str | Path,
    research_output_root: Path,
) -> dict[str, Any]:
    result = _base_item_result(item)
    if item["budget"]["max_sweeps"] < 1:
        result["status"] = "skipped"
        result["warnings"].append("max_sweeps is 0")
        return result
    config = dict(item["sweep_config"])
    config.setdefault("sweep_id", f"SWEEP_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{_slug(item['queue_id'])[:40]}_{uuid.uuid4().hex[:6].upper()}")
    config_path = queue_run_dir / "queue_inputs" / f"{_slug(item['queue_id'])}_sweep.yaml"
    _write_text_new(config_path, yaml.safe_dump(config, sort_keys=False))
    prepared = sweeps.prepare_sweep(
        db_path,
        config_path=config_path,
        output_root=research_output_root,
        created_by=f"research_queue:{item['queue_id']}",
    )
    result["sweeps_created"].append(prepared["sweep_id"])
    result["experiments_created"].extend(child["child_experiment_id"] for child in prepared["children"])
    if prepared.get("warnings"):
        result["warnings"].extend(prepared["warnings"])
    if item["permissions"]["allow_runner_execution"] and item["task_type"] == "parameter_robustness":
        run_result = sweeps.run_sweep(
            db_path,
            sweep_id=prepared["sweep_id"],
            limit=item["budget"]["max_child_experiments"],
            continue_on_error=True,
            runner_script=runner_script,
            output_root=research_output_root,
        )
        for child_result in run_result.get("results", []):
            if child_result.get("returncode") not in (None, 0) or child_result.get("error"):
                result["failures"].append(
                    f"{child_result.get('child_experiment_id')}: {child_result.get('error') or child_result.get('returncode')}"
                )
        if run_result.get("status") == "failed":
            result["status"] = "failed"
    else:
        result["warnings"].append("sweep prepared without runner execution")
    summary_path = queue_run_dir / "sweep_summaries" / f"{prepared['sweep_id']}_summary.json"
    sweep_summary = sweeps.summarize_sweep(db_path, sweep_id=prepared["sweep_id"], output_path=summary_path)
    result["artifacts_created"].append(str(summary_path))
    if sweep_summary.get("status") in {"warn", "not_available", "not_implemented"} and result["status"] == "completed":
        result["status"] = "completed_with_warnings"
    _maybe_propose_lifecycle(db_path, item, prepared["parent_experiment_id"], queue_run_dir, result)
    return result


def _execute_portfolio_review(db_path: str | Path, item: dict[str, Any], queue_run_dir: Path) -> dict[str, Any]:
    result = _base_item_result(item)
    parent_id = item.get("parent_experiment_id")
    if not parent_id:
        result["status"] = "failed"
        result["failures"].append("portfolio_review requires parent_experiment_id")
        return result
    output = queue_run_dir / "portfolio" / f"{parent_id}_portfolio_report.json"
    portfolio_config = (item.get("sweep_config") or {}).get("portfolio_config")
    if portfolio_config:
        report = write_configured_portfolio_report(db_path, _repo_path(portfolio_config), output, candidate_experiment_id=parent_id)
    else:
        report = write_portfolio_report(db_path, parent_id, output)
    registry.attach_artifact(db_path, parent_id, "portfolio_report", output)
    result["artifacts_created"].append(str(output))
    if report.get("status") == "fail":
        result["status"] = "failed"
        result["failures"].append("portfolio report status is fail")
    elif report.get("status") != "pass":
        result["status"] = "completed_with_warnings"
        result["warnings"].append(f"portfolio report status is {report.get('status')}")
    review = _create_review_request(db_path, item, queue_run_dir, "portfolio_reviewer", experiment_id=parent_id)
    result["artifacts_created"].append(review)
    _maybe_propose_lifecycle(db_path, item, parent_id, queue_run_dir, result)
    return result


def _execute_review_request(db_path: str | Path, item: dict[str, Any], queue_run_dir: Path, role: str) -> dict[str, Any]:
    result = _base_item_result(item)
    experiment_id = item.get("parent_experiment_id")
    path = _create_review_request(db_path, item, queue_run_dir, role, experiment_id=experiment_id)
    result["artifacts_created"].append(path)
    result["status"] = "completed_with_warnings"
    result["warnings"].append("no LLM agent backend connected; review_request is pending_agent_review")
    return result


def _execute_research_summary(db_path: str | Path, item: dict[str, Any], queue_run_dir: Path) -> dict[str, Any]:
    result = _base_item_result(item)
    strategy_id = item["strategy_id"]
    experiments = [
        experiment for experiment in registry.list_experiments(db_path)
        if experiment["strategy_id"] == strategy_id
    ]
    summary = {
        "agent_role": "research_librarian",
        "hypothesis_id": item["hypothesis_id"],
        "strategy_id": strategy_id,
        "tested_summary": f"Registry currently has {len(experiments)} experiment rows for {strategy_id}.",
        "failed_variants": [experiment["experiment_id"] for experiment in experiments if experiment["status"] == "failed"],
        "passed_variants": [experiment["experiment_id"] for experiment in experiments if experiment["gate_status"] == "pass"],
        "exhausted_hypotheses": [],
        "duplicate_ideas": [],
        "suggested_next_actions": ["Review failed and warning variants before creating new structural changes."],
        "status": "generated_from_registry",
    }
    path = queue_run_dir / "agent_requests" / f"{_slug(item['queue_id'])}_research_librarian_summary.yaml"
    _write_text_new(path, yaml.safe_dump(summary, sort_keys=False))
    if item.get("parent_experiment_id"):
        registry.attach_artifact(db_path, item["parent_experiment_id"], "research_librarian_summary", path)
        registry.attach_agent_artifact(
            db_path,
            experiment_id=item["parent_experiment_id"],
            agent_role="research_librarian",
            artifact_type="research_librarian_summary",
            path=path,
        )
    result["artifacts_created"].append(str(path))
    return result


def _create_review_request(
    db_path: str | Path,
    item: dict[str, Any],
    queue_run_dir: Path,
    role: str,
    *,
    experiment_id: str | None,
) -> str:
    request = {
        "artifact_type": "review_request",
        "status": "pending_agent_review",
        "agent_role": role,
        "queue_id": item["queue_id"],
        "task_type": item["task_type"],
        "experiment_id": experiment_id,
        "hypothesis_id": item["hypothesis_id"],
        "strategy_id": item["strategy_id"],
        "required_outputs": item["required_outputs"],
        "created_at": registry.utc_now(),
        "notes": "No LLM backend is connected; this is a structured request, not a completed review.",
    }
    path = queue_run_dir / "agent_requests" / f"{_slug(item['queue_id'])}_{role}_review_request.yaml"
    _write_text_new(path, yaml.safe_dump(request, sort_keys=False))
    if experiment_id:
        registry.attach_artifact(db_path, experiment_id, "review_request", path)
        registry.attach_agent_artifact(
            db_path,
            experiment_id=experiment_id,
            agent_role=role,
            artifact_type="review_request",
            path=path,
        )
    return str(path)


def _maybe_propose_lifecycle(
    db_path: str | Path,
    item: dict[str, Any],
    experiment_id: str,
    queue_run_dir: Path,
    result: dict[str, Any],
) -> None:
    proposal = item.get("lifecycle_proposal")
    if not proposal:
        return
    if not item["permissions"].get("allow_lifecycle_propose"):
        result["warnings"].append("lifecycle proposal skipped because allow_lifecycle_propose is false")
        return
    if item["permissions"].get("allow_lifecycle_apply"):
        result["blocked_actions"].append("apply_lifecycle_transition")
    if not isinstance(proposal, dict) or not proposal.get("to_state"):
        result["warnings"].append("lifecycle_proposal requires to_state")
        return
    transition = lifecycle.propose_transition(
        db_path,
        strategy=_strategy_spec_path(item["strategy_id"]),
        to_state=proposal["to_state"],
        experiment_id=experiment_id,
        reason=proposal.get("reason") or f"Queue {item['queue_id']} proposes lifecycle transition.",
        requested_by=f"research_queue:{item['queue_id']}",
        strictness=proposal.get("strictness", "normal"),
        override=False,
        notes="Queue execution may propose but never apply lifecycle transitions.",
        snapshot_dir=queue_run_dir / "lifecycle_snapshots",
    )
    result["lifecycle_transition_proposals"].append(transition["transition"]["transition_id"])


def _execute_impl_request(
    db_path: str | Path,
    item: dict[str, Any],
    queue_run_dir: Path,
) -> dict[str, Any]:
    result = _base_item_result(item)
    strategy_id = item.get("strategy_id", "")
    strategy_version = item.get("strategy_version", "v1")
    generated_files = item.get("generated_files") or []
    if not generated_files:
        result["status"] = "failed"
        result["failures"].append("implementation_request requires generated_files")
        return result
    try:
        outcome = impl_mod.create_implementation_request(
            db_path,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            sandbox_dir=impl_mod.sandbox_path(strategy_id, strategy_version),
            generated_files=generated_files,
            created_by=f"research_queue:{item['queue_id']}",
            hypothesis_id=item.get("hypothesis_id"),
            strategy_spec_path=item.get("strategy_spec_path"),
            expected_inputs=item.get("expected_inputs"),
            parameters=item.get("parameters"),
        )
        result["artifacts_created"].append(outcome.get("artifact_path", ""))
        result["experiments_created"].append(outcome["implementation_request_id"])
    except Exception as exc:
        result["status"] = "failed"
        result["failures"].append(str(exc))
    return result


def _execute_impl_compile_check(
    db_path: str | Path,
    item: dict[str, Any],
    queue_run_dir: Path,
) -> dict[str, Any]:
    result = _base_item_result(item)
    req_id = item.get("implementation_request_id") or item.get("parent_experiment_id")
    if not req_id:
        result["status"] = "failed"
        result["failures"].append("implementation_compile_check requires implementation_request_id")
        return result
    try:
        outcome = impl_mod.compile_check(db_path, req_id, mock=True)
        result["experiments_created"].append(req_id)
        if outcome.get("compile_status") in ("mock_checked", "passed"):
            result["artifacts_created"].append(outcome.get("generated_mq5_path", ""))
        else:
            result["status"] = "failed"
            result["failures"].extend(outcome.get("errors", []))
    except Exception as exc:
        result["status"] = "failed"
        result["failures"].append(str(exc))
    return result


def _execute_impl_review(
    db_path: str | Path,
    item: dict[str, Any],
    queue_run_dir: Path,
) -> dict[str, Any]:
    result = _base_item_result(item)
    req_id = item.get("implementation_request_id") or item.get("parent_experiment_id")
    if not req_id:
        result["status"] = "failed"
        result["failures"].append("implementation_review requires implementation_request_id")
        return result
    try:
        outcome = impl_mod.run_diff_review(db_path, req_id)
        result["artifacts_created"].extend([outcome.get("artifact_path", "")])
        if outcome.get("hard_blockers"):
            result["status"] = "completed_with_warnings"
            result["warnings"].extend(outcome["hard_blockers"])
        result["status"] = "completed_with_warnings"
        result["warnings"].append("diff review completed; human review required for baseline approval")
    except Exception as exc:
        result["status"] = "failed"
        result["failures"].append(str(exc))
    return result


def _execute_hypothesis_generation(
    db_path: str | Path,
    item: dict[str, Any],
    queue_run_dir: Path,
) -> dict[str, Any]:
    result = _base_item_result(item)
    intake_dir = queue_run_dir / "intake"
    intake_dir.mkdir(parents=True, exist_ok=True)
    try:
        hypotheses = intake.generate_hypotheses(
            research_theme=item.get("research_theme", ""),
            symbol=item.get("symbol", ""),
            timeframe=item.get("timeframe", ""),
            market_regime=item.get("market_regime", ""),
            strategy_family=item.get("strategy_family", ""),
            max_hypotheses=item.get("max_hypotheses", 3),
            constraints=item.get("constraints") or {},
            created_by=f"research_queue:{item['queue_id']}",
            hypothesis_set_dir=intake_dir,
        )
        for h in hypotheses:
            result["artifacts_created"].append(h.get("path", ""))
            if h.get("hypothesis_set_path"):
                result["artifacts_created"].append(h["hypothesis_set_path"])
        hyp_ids = [h["hypothesis_id"] for h in hypotheses]
        result["experiments_created"] = hyp_ids
        result["status"] = "completed"
    except Exception as exc:
        result["status"] = "failed"
        result["failures"].append(str(exc))
    return result


def _execute_strategy_spec_generation(
    db_path: str | Path,
    item: dict[str, Any],
    queue_run_dir: Path,
) -> dict[str, Any]:
    result = _base_item_result(item)
    intake_dir = queue_run_dir / "intake"
    intake_dir.mkdir(parents=True, exist_ok=True)
    try:
        hypothesis_id = item.get("hypothesis_id")
        if not hypothesis_id:
            hyp_set_path = intake_dir / "hypothesis_set.yaml"
            if not hyp_set_path.is_file():
                result["status"] = "failed"
                result["failures"].append("hypothesis_id required and no hypothesis_set.yaml found in intake dir")
                return result
            import yaml as _yl
            hyp_set = _yl.safe_load(hyp_set_path.read_text(encoding="utf-8"))
            hyp_list = hyp_set.get("hypotheses", [])
            selected_idx = item.get("selected_index", 0)
            if selected_idx >= len(hyp_list):
                result["status"] = "failed"
                result["failures"].append(f"selected_index {selected_idx} out of range (have {len(hyp_list)} hypotheses)")
                return result
            hypothesis_id = hyp_list[selected_idx]["hypothesis_id"]

        outcome = intake.generate_strategy_spec(
            hypothesis_id=hypothesis_id,
            strategy_id=item["strategy_id"],
            strategy_version=item.get("strategy_version", "v1"),
            created_by=f"research_queue:{item['queue_id']}",
            output_dir=GENERATED_SPECS_DIR,
        )
        result["artifacts_created"].append(outcome["spec_path"])
        result["experiments_created"].append(item["strategy_id"])
        result["status"] = "completed"
    except Exception as exc:
        result["status"] = "failed"
        result["failures"].append(str(exc))
    return result


def _execute_implementation_materialization(
    db_path: str | Path,
    item: dict[str, Any],
    queue_run_dir: Path,
) -> dict[str, Any]:
    result = _base_item_result(item)
    try:
        spec_path = item.get("generated_spec_path")
        if not spec_path:
            spec_path = GENERATED_SPECS_DIR / f"{item['strategy_id']}.yaml"
        outcome = intake.materialize_implementation(
            db_path,
            strategy_spec_path=spec_path,
            strategy_id=item["strategy_id"],
            strategy_version=item.get("strategy_version", "v1"),
            created_by=f"research_queue:{item['queue_id']}",
            mock_compile=True,
        )
        if outcome.get("status") == "failed":
            result["status"] = "failed"
            result["failures"].extend(outcome.get("errors", [outcome.get("status", "unknown error")]))
            return result
        result["artifacts_created"].append(outcome.get("implementation_request_id", ""))
        result["artifacts_created"].extend(outcome.get("sandbox_files", []))
        if outcome.get("diff_review_path"):
            result["artifacts_created"].append(outcome["diff_review_path"])
        if outcome.get("hard_blockers"):
            result["warnings"].extend(outcome["hard_blockers"])
        result["experiments_created"].append(outcome["implementation_request_id"])
        result["status"] = "completed"
        if outcome.get("hard_blockers") or outcome.get("dangerous_patterns"):
            result["status"] = "completed_with_warnings"
            result["warnings"].append("implementation materialized with warnings; review packet recommended")
    except Exception as exc:
        result["status"] = "failed"
        result["failures"].append(str(exc))
    return result


def _execute_research_review_packet(
    db_path: str | Path,
    item: dict[str, Any],
    queue_run_dir: Path,
) -> dict[str, Any]:
    result = _base_item_result(item)
    intake_dir = queue_run_dir / "intake"
    intake_dir.mkdir(parents=True, exist_ok=True)
    try:
        outcome = intake.build_review_packet(
            db_path,
            strategy_id=item["strategy_id"],
            strategy_version=item.get("strategy_version", "v1"),
            output_dir=intake_dir,
        )
        result["artifacts_created"].append(outcome["packet_path"])
        result["experiments_created"].append(f"review:{item['strategy_id']}")
        packet = outcome["packet"]
        if packet.get("dangerous_pattern_warnings"):
            result["warnings"].extend(packet["dangerous_pattern_warnings"])
        result["warnings"].append(f"review packet: approval_status={packet['approval_status']}, recommended={packet['recommended_next_action']}")
        result["status"] = "completed_with_warnings" if packet.get("dangerous_pattern_warnings") else "completed"
    except Exception as exc:
        result["status"] = "failed"
        result["failures"].append(str(exc))
    return result


def generate_morning_report(
    db_path: str | Path,
    *,
    run_id: str,
    output_root: str | Path = QUEUE_RUN_ROOT,
) -> dict[str, Any]:
    queue_run = registry.get_queue_run(db_path, run_id)
    if not queue_run:
        raise ValueError(f"queue run not found: {run_id}")
    summary_path = queue_run.get("summary_path")
    if not summary_path:
        raise ValueError(f"queue run summary_path is missing: {run_id}")
    summary_file = resolve_stored_path(summary_path, REPO_ROOT)
    if not summary_file.is_file():
        summary_file = Path(summary_path)
    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    report = _build_morning_report(db_path, summary)
    run_dir = Path(output_root) / run_id
    json_path = run_dir / "morning_report.json"
    md_path = run_dir / "morning_report.md"
    if json_path.exists():
        json_path = run_dir / f"morning_report_{uuid.uuid4().hex[:6].upper()}.json"
    if md_path.exists():
        md_path = run_dir / f"morning_report_{uuid.uuid4().hex[:6].upper()}.md"
    _write_json_new(json_path, report)
    _write_text_new(md_path, _morning_markdown(report))
    registry.update_queue_run(
        db_path,
        run_id,
        artifacts_created=_json_loads(queue_run.get("artifacts_created_json"), []) + [str(json_path), str(md_path)],
    )
    return {"run_id": run_id, "json_path": str(json_path), "markdown_path": str(md_path), "report": report}


def _build_morning_report(db_path: str | Path, summary: dict[str, Any]) -> dict[str, Any]:
    experiments = []
    failed_candidates = []
    best_candidates = []
    for experiment_id in summary.get("experiments_created", []):
        experiment = registry.get_experiment(db_path, experiment_id)
        metrics = registry.get_experiment_metrics(db_path, experiment_id) or {}
        item = {"experiment_id": experiment_id, "experiment": experiment, "metrics": metrics}
        experiments.append(item)
        status = experiment.get("status") if experiment else None
        net_return = metrics.get("net_return")
        profit_factor = metrics.get("profit_factor")
        if status == "failed" or (net_return is not None and net_return <= 0) or (profit_factor is not None and profit_factor <= 1):
            failed_candidates.append(item)
        elif net_return is not None or profit_factor is not None:
            best_candidates.append(item)
    best_candidates.sort(key=lambda item: ((item["metrics"].get("net_return") or -999), (item["metrics"].get("profit_factor") or -999)), reverse=True)

    sweep_reports = []
    for sweep_id in summary.get("sweeps_created", []):
        sweep = registry.get_sweep(db_path, sweep_id)
        children = registry.list_sweep_children(db_path, sweep_id)
        sweep_reports.append({"sweep": sweep, "children": children})

    red_team_objections = []
    for artifact in summary.get("artifacts_created", []):
        path = Path(artifact)
        if path.is_file() and path.name.endswith("red_team_reviewer_review_request.yaml"):
            red_team_objections.append({"status": "pending_agent_review", "path": str(path)})

    queue_items = summary.get("items", [])
    failed_items = [item for item in queue_items if item.get("status") == "failed"]
    skipped_items = [item for item in queue_items if item.get("status") == "skipped"]
    blocked_items = [item for item in queue_items if item.get("blocked_actions")]
    return {
        "schema_version": "morning_report_v1",
        "queue_run_id": summary["queue_run_id"],
        "summary": {
            "status": summary["status"],
            "started_at": summary["started_at"],
            "completed_at": summary["completed_at"],
            "warnings": summary.get("warnings", []),
            "failures": summary.get("failures", []),
        },
        "queue_items_processed": queue_items,
        "failed_items": failed_items,
        "skipped_items": skipped_items,
        "blocked_items": blocked_items,
        "item_status_history": summary.get("item_status_history", []),
        "experiments_created": experiments,
        "sweeps_created": sweep_reports,
        "best_candidates": best_candidates[:5],
        "failed_candidates": failed_candidates,
        "robustness_summaries": [item for item in sweep_reports if item["sweep"] and item["sweep"]["sweep_type"] == "parameter_robustness"],
        "portfolio_summaries": [artifact for artifact in summary.get("artifacts_created", []) if str(artifact).endswith("portfolio_report.json")],
        "red_team_objections": red_team_objections,
        "lifecycle_transition_proposals": summary.get("lifecycle_transition_proposals", []),
        "budget_usage": summary.get("budget_usage", {}),
        "blocked_actions": summary.get("blocked_actions", []),
        "recommended_manual_reviews": _recommended_reviews(summary, failed_candidates, red_team_objections),
        "archive_candidates": [item["experiment_id"] for item in failed_candidates],
        "next_suggested_research_actions": _next_actions(summary, failed_candidates),
    }


def _recommended_reviews(summary: dict[str, Any], failed_candidates: list[dict[str, Any]], red_team_objections: list[dict[str, Any]]) -> list[str]:
    reviews = []
    if failed_candidates:
        reviews.append("Review failed and rejected variants before accepting any winner.")
    if red_team_objections:
        reviews.append("Complete pending red-team review requests; no red-team decision has been faked.")
    if summary.get("lifecycle_transition_proposals"):
        reviews.append("Manually evaluate lifecycle transition proposals before applying any state change.")
    return reviews


def _next_actions(summary: dict[str, Any], failed_candidates: list[dict[str, Any]]) -> list[str]:
    if summary.get("failures"):
        return ["Inspect queue failures and rerun only uncontaminated tasks."]
    if failed_candidates:
        return ["Compare failed variants against parent baselines to identify blocked market conditions."]
    return ["Review robustness and portfolio artifacts before scheduling another queue run."]


def _morning_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Morning Report: {report['queue_run_id']}",
        "",
        "## Summary",
        f"- Status: {report['summary']['status']}",
        f"- Warnings: {len(report['summary']['warnings'])}",
        f"- Failures: {len(report['summary']['failures'])}",
        "",
        "## Queue Items Processed",
    ]
    for item in report["queue_items_processed"]:
        lines.append(f"- `{item['queue_id']}` {item['task_type']}: {item['status']}")
    lines.extend(["", "## Experiments Created"])
    for item in report["experiments_created"]:
        experiment = item["experiment"] or {}
        lines.append(f"- `{item['experiment_id']}`: {experiment.get('status', 'missing')} / gate {experiment.get('gate_status', 'missing')}")
    lines.extend(["", "## Sweeps Created"])
    for item in report["sweeps_created"]:
        sweep = item["sweep"] or {}
        lines.append(f"- `{sweep.get('sweep_id', 'missing')}`: {sweep.get('sweep_type', 'unknown')} with {len(item['children'])} children")
    lines.extend(["", "## Best Candidates"])
    if report["best_candidates"]:
        for item in report["best_candidates"]:
            metrics = item["metrics"]
            lines.append(f"- `{item['experiment_id']}` net_return={metrics.get('net_return')} PF={metrics.get('profit_factor')}")
    else:
        lines.append("- None available.")
    lines.extend(["", "## Failed Candidates"])
    if report["failed_candidates"]:
        for item in report["failed_candidates"]:
            metrics = item["metrics"]
            lines.append(f"- `{item['experiment_id']}` status={(item['experiment'] or {}).get('status')} net_return={metrics.get('net_return')} PF={metrics.get('profit_factor')}")
    else:
        lines.append("- None recorded.")
    sections = [
        ("Failed Items", [f"`{item['queue_id']}` {item['task_type']}: {item['failures']}" for item in report["failed_items"]]),
        ("Skipped Items", [f"`{item['queue_id']}` {item['task_type']}: {item['warnings']}" for item in report["skipped_items"]]),
        ("Blocked Items", [f"`{item['queue_id']}`: {item['blocked_actions']}" for item in report["blocked_items"]]),
        ("Robustness Summaries", report["robustness_summaries"]),
        ("Portfolio Summaries", report["portfolio_summaries"]),
        ("Red-Team Objections", report["red_team_objections"]),
        ("Lifecycle Transition Proposals", report["lifecycle_transition_proposals"]),
        ("Budget Usage", [json.dumps(report["budget_usage"], sort_keys=True)]),
        ("Blocked Actions", report["blocked_actions"]),
        ("Recommended Manual Reviews", report["recommended_manual_reviews"]),
        ("Archive Candidates", report["archive_candidates"]),
        ("Next Suggested Research Actions", report["next_suggested_research_actions"]),
    ]
    for title, values in sections:
        lines.extend(["", f"## {title}"])
        if values:
            for value in values:
                lines.append(f"- {value}")
        else:
            lines.append("- None.")
    return "\n".join(lines) + "\n"
