from __future__ import annotations

import itertools
import json
import re
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

import yaml

from . import registry, runner
from .contracts import SWEEP_TYPES, ensure_member, resolve_stored_path
from .hashing import parse_key_value_file
from .schemas import REPO_ROOT, GENERATED_SPECS_DIR, SchemaValidationError, load_yaml, validate_strategy_spec


SWEEP_ROOT = REPO_ROOT / "automated" / "sweeps"
SWEEP_CONFIG_DIR = REPO_ROOT / "automated" / "specs" / "sweeps"


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()


def _sweep_id(strategy_id: str, sweep_type: str) -> str:
    return f"SWEEP_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{_slug(strategy_id)}_{_slug(sweep_type)}"


def _strategy_spec_path(strategy_id: str, config: dict[str, Any] | None = None) -> Path:
    if config and config.get("spec_path"):
        path = Path(config["spec_path"])
        if path.is_file():
            return path
    path = REPO_ROOT / "automated" / "specs" / "strategies" / f"{strategy_id}.yaml"
    if path.is_file():
        return path
    path = GENERATED_SPECS_DIR / f"{strategy_id}.yaml"
    if path.is_file():
        return path
    raise ValueError(f"strategy spec not found for strategy_id: {strategy_id}")


def validate_sweep_config(data: dict[str, Any]) -> dict[str, Any]:
    sweep_type = data.get("sweep_type")
    if not isinstance(sweep_type, str):
        raise SchemaValidationError("sweep.sweep_type is required")
    try:
        ensure_member(sweep_type, SWEEP_TYPES, "sweep.sweep_type")
    except ValueError as exc:
        raise SchemaValidationError(str(exc)) from exc
    if not data.get("parent_experiment_id"):
        raise SchemaValidationError("sweep.parent_experiment_id is required")
    budget = data.setdefault("budget", {})
    if not isinstance(budget, dict):
        raise SchemaValidationError("sweep.budget must be a mapping")
    max_children = budget.setdefault("max_child_experiments", 25)
    if not isinstance(max_children, int) or max_children < 0:
        raise SchemaValidationError("sweep.budget.max_child_experiments must be a non-negative integer")
    if sweep_type == "parameter_robustness":
        mode = data.setdefault("mode", "one_variable_at_a_time")
        if mode not in {"one_variable_at_a_time", "grid"}:
            raise SchemaValidationError("sweep.mode must be one_variable_at_a_time or grid")
        if not isinstance(data.get("parameters"), dict) or not data["parameters"]:
            raise SchemaValidationError("sweep.parameters must be a non-empty mapping")
        changed = budget.setdefault("max_parameters_changed_per_child", 1)
        if mode == "one_variable_at_a_time" and changed != 1 and data.get("budget", {}).get("require_one_variable_at_a_time", True):
            raise SchemaValidationError("one_variable_at_a_time sweeps require max_parameters_changed_per_child=1")
    elif sweep_type == "cost_stress":
        if not isinstance(data.get("cost_multipliers"), list) or not data["cost_multipliers"]:
            raise SchemaValidationError("cost_stress.cost_multipliers must be a non-empty list")
    return data


def load_sweep_config(path: str | Path) -> dict[str, Any]:
    data = load_yaml(path)
    return validate_sweep_config(data)


def plan_sweep(db_path: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    config = validate_sweep_config(dict(config))
    parent = registry.get_experiment(db_path, config["parent_experiment_id"])
    if not parent:
        raise ValueError(f"parent experiment not found: {config['parent_experiment_id']}")
    strategy_id = config.get("strategy_id") or parent["strategy_id"]
    if strategy_id != parent["strategy_id"]:
        raise ValueError("sweep.strategy_id does not match parent experiment strategy_id")
    spec_path = _strategy_spec_path(strategy_id, config)
    spec = load_yaml(spec_path)
    validate_strategy_spec(spec)
    if config["sweep_type"] == "parameter_robustness":
        children, warnings = _plan_parameter_children(config, spec)
    elif config["sweep_type"] == "cost_stress":
        children, warnings = _plan_cost_children(config)
    else:
        children, warnings = [], [f"{config['sweep_type']} is a scaffold and not executable in phase 7"]
    return {
        "sweep_id": config.get("sweep_id") or _sweep_id(strategy_id, config["sweep_type"]),
        "parent_experiment_id": parent["experiment_id"],
        "strategy_id": strategy_id,
        "hypothesis_id": parent["hypothesis_id"],
        "sweep_type": config["sweep_type"],
        "budget": config["budget"],
        "config": config,
        "children": children,
        "warnings": warnings,
    }


def _parameter_key_and_values(name: str, item: Any) -> tuple[str, list[Any], bool]:
    if isinstance(item, dict):
        values = item.get("values")
        key = item.get("set_key") or item.get("key") or name
        allow_add = item.get("allow_add") is True
    else:
        values = item
        key = name
        allow_add = False
    if not isinstance(values, list) or not values:
        raise SchemaValidationError(f"sweep.parameters.{name}.values must be a non-empty list")
    return str(key), values, allow_add


def _plan_parameter_children(config: dict[str, Any], spec: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    mode = config.get("mode", "one_variable_at_a_time")
    budget = config["budget"]
    max_children = budget["max_child_experiments"]
    baseline = spec.get("parameters", {})
    warnings: list[str] = []
    parameter_items = [(name, *_parameter_key_and_values(name, item)) for name, item in config["parameters"].items()]
    children: list[dict[str, Any]] = []
    if mode == "one_variable_at_a_time":
        for name, key, values, allow_add in parameter_items:
            if key not in baseline and not allow_add:
                raise SchemaValidationError(f"parameter key is missing from strategy spec baseline: {key}")
            baseline_value = baseline.get(key)
            for value in values:
                if value == baseline_value and not config.get("include_baseline_children", False):
                    continue
                children.append(
                    {
                        "child_index": len(children),
                        "child_role": f"{name}={value}",
                        "executable": True,
                        "parameter_diff": {key: {"from": baseline_value, "to": value, "allow_add": allow_add}},
                    }
                )
    else:
        warnings.append("grid mode increases multiple-testing risk; use only with explicit budget control")
        names = [item[0] for item in parameter_items]
        keys = [item[1] for item in parameter_items]
        values_product = itertools.product(*[item[2] for item in parameter_items])
        for combination in values_product:
            diff: dict[str, Any] = {}
            for key, value in zip(keys, combination):
                if key not in baseline:
                    raise SchemaValidationError(f"parameter key is missing from strategy spec baseline: {key}")
                if value != baseline[key]:
                    diff[key] = {"from": baseline[key], "to": value, "allow_add": False}
            if not diff and not config.get("include_baseline_children", False):
                continue
            children.append(
                {
                    "child_index": len(children),
                    "child_role": ",".join(f"{name}={value}" for name, value in zip(names, combination)),
                    "executable": True,
                    "parameter_diff": diff,
                }
            )
    if len(children) > max_children:
        raise SchemaValidationError(f"sweep plans {len(children)} children but budget max_child_experiments is {max_children}")
    max_changed = budget.get("max_parameters_changed_per_child", 1)
    if any(len(child.get("parameter_diff", {})) > max_changed for child in children):
        raise SchemaValidationError("child plan exceeds max_parameters_changed_per_child")
    return children, warnings


def _plan_cost_children(config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    children = [
        {
            "child_index": index,
            "child_role": f"cost_multiplier={value}",
            "executable": False,
            "status_reason": "not_executable_with_current_runner",
            "cost_diff": {"cost_multiplier": {"from": 1.0, "to": value}},
        }
        for index, value in enumerate(config["cost_multipliers"])
    ]
    if len(children) > config["budget"]["max_child_experiments"]:
        raise SchemaValidationError("cost_stress exceeds max_child_experiments")
    return children, ["cost assumptions are documented in YAML; current MT5 runner does not safely materialize costs"]


def _replace_key_value_file(source: Path, destination: Path, replacements: dict[str, Any], *, allow_add: set[str] | None = None) -> None:
    allow_add = allow_add or set()
    destination.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    lines = []
    for raw_line in source.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";") or "=" not in raw_line:
            lines.append(raw_line)
            continue
        key, old_value = raw_line.split("=", 1)
        clean_key = key.strip()
        if clean_key in replacements:
            seen.add(clean_key)
            quote = '"' if old_value.strip().startswith('"') and old_value.strip().endswith('"') else ""
            value = str(replacements[clean_key])
            lines.append(f"{key}={quote}{value}{quote}")
        else:
            lines.append(raw_line)
    missing = set(replacements) - seen
    disallowed = missing - allow_add
    if disallowed:
        raise SchemaValidationError(f"cannot materialize missing key(s): {sorted(disallowed)}")
    for key in sorted(missing & allow_add):
        lines.append(f"{key}={replacements[key]}")
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _materialize_child_files(spec: dict[str, Any], child: dict[str, Any], child_raw_dir: Path, child_experiment_id: str) -> dict[str, Any]:
    implementation_files = spec["implementation"]["files"]
    base_conf = runner.repo_path(implementation_files["config"])
    base_set = runner.repo_path(implementation_files["parameters"])
    child_set = child_raw_dir / "parameters.set"
    child_base_conf = child_raw_dir / "materialized_base.conf"
    replacements = {key: item["to"] for key, item in child.get("parameter_diff", {}).items()}
    allow_add = {key for key, item in child.get("parameter_diff", {}).items() if item.get("allow_add")}
    _replace_key_value_file(base_set, child_set, replacements, allow_add=allow_add)
    _replace_key_value_file(
        base_conf,
        child_base_conf,
        {"RUN_ID": child_experiment_id, "EA_SET_FILE": str(child_set.resolve())},
    )
    return {
        "status": "materialized",
        "base_config": str(base_conf),
        "base_parameters": str(base_set),
        "materialized_config": str(child_base_conf),
        "materialized_parameters": str(child_set),
        "changed_keys": sorted(replacements),
    }


def _create_scaffold_child_experiment(
    db_path: str | Path,
    *,
    parent: dict[str, Any],
    spec: dict[str, Any],
    child_experiment_id: str,
    child: dict[str, Any],
    created_by: str,
) -> None:
    registry.create_experiment(
        db_path,
        {
            "experiment_id": child_experiment_id,
            "hypothesis_id": parent["hypothesis_id"],
            "strategy_id": parent["strategy_id"],
            "strategy_version": parent["strategy_version"],
            "run_reason": "validation_only",
            "created_by": created_by,
            "created_at": registry.utc_now(),
            "spec_hash": parent["spec_hash"],
            "parameter_set_hash": parent["parameter_set_hash"],
            "dataset_id": parent.get("dataset_id"),
            "dataset_bundle_id": parent.get("dataset_bundle_id"),
            "dataset_hash": parent.get("dataset_hash"),
            "dataset_bundle_hash": parent.get("dataset_bundle_hash"),
            "code_version": parent["code_version"],
            "execution_config_hash": parent["execution_config_hash"],
            "cost_config_hash": parent["cost_config_hash"],
            "engine": parent["engine"],
            "implementation_files": _json_loads(parent.get("implementation_files_json")) or {},
            "implementation_mode": parent["implementation_mode"],
            "execution_timing": _json_loads(parent.get("execution_timing_json")) or {},
            "timeframe": parent["timeframe"],
            "universe": _json_loads(parent.get("universe_json")) or [],
            "parent_experiment_id": parent["experiment_id"],
            "rerun_of_experiment_id": None,
            "is_artifact_regeneration": False,
            "change_type": "validation",
            "change_summary": f"Scaffold sweep child: {child['child_role']}",
            "rationale": "Phase 7 scaffold child; not executable with current runner.",
            "parameter_diff": child.get("parameter_diff"),
            "structural_diff": None,
            "research_budget_snapshot": spec["research_budget"],
            "complexity_score": 0,
            "min_trades_required": spec["validation"]["min_trades_required"],
            "cost_assumptions_documented": bool(parent.get("cost_assumptions_documented")),
            "dataset_metadata_present": bool(parent.get("dataset_metadata_present")),
            "hypothesis_present": bool(parent.get("hypothesis_present")),
            "validation_report_path": None,
            "gate_status": "incomplete",
            "started_at": None,
            "completed_at": None,
            "status": "planned",
        },
    )


def prepare_sweep(
    db_path: str | Path,
    *,
    config_path: str | Path,
    output_root: str | Path = runner.RESEARCH_RUNS_DIR,
    created_by: str = "human",
) -> dict[str, Any]:
    config = load_sweep_config(config_path)
    plan = plan_sweep(db_path, config)
    parent = registry.get_experiment(db_path, plan["parent_experiment_id"])
    if not parent:
        raise ValueError(f"parent experiment not found: {plan['parent_experiment_id']}")
    spec_path = _strategy_spec_path(plan["strategy_id"], plan.get("config"))
    spec = load_yaml(spec_path)
    sweep_dir = SWEEP_ROOT / plan["sweep_id"]
    sweep_dir.mkdir(parents=True, exist_ok=True)
    registry.create_sweep(
        db_path,
        {
            "sweep_id": plan["sweep_id"],
            "parent_experiment_id": plan["parent_experiment_id"],
            "strategy_id": plan["strategy_id"],
            "hypothesis_id": plan["hypothesis_id"],
            "sweep_type": plan["sweep_type"],
            "status": "prepared",
            "created_by": created_by,
            "created_at": registry.utc_now(),
            "completed_at": None,
            "budget": plan["budget"],
            "config": plan["config"],
            "summary_path": None,
            "notes": "; ".join(plan["warnings"]),
        },
    )
    child_records: list[dict[str, Any]] = []
    for child in plan["children"]:
        child_experiment_id = f"{plan['parent_experiment_id']}_{plan['sweep_id']}_{child['child_index']:03d}"
        child_output_dir = runner.experiment_output_dir(child_experiment_id, output_root)
        runner.ensure_output_tree(child_output_dir)
        materialization: dict[str, Any]
        if child.get("executable"):
            materialization = _materialize_child_files(spec, child, child_output_dir / "raw", child_experiment_id)
            context = runner.prepare_run(
                db_path=db_path,
                strategy_spec_path=spec_path,
                dataset_id=parent.get("dataset_id"),
                dataset_bundle_id=parent.get("dataset_bundle_id"),
                experiment_id=child_experiment_id,
                output_root=output_root,
                run_reason="agent",
                created_by=created_by,
                change_type="parameter_diff",
                change_summary=f"Sweep child for {plan['sweep_id']}: {child['child_role']}",
                rationale="Prepared by Phase 7 sweep orchestration.",
                parameter_diff=child.get("parameter_diff"),
                parent_experiment_id=plan["parent_experiment_id"],
                config_override_path=materialization["materialized_config"],
                parameter_override_path=materialization["materialized_parameters"],
                materialization=materialization,
            )
            status = "prepared"
        else:
            _create_scaffold_child_experiment(
                db_path,
                parent=parent,
                spec=spec,
                child_experiment_id=child_experiment_id,
                child=child,
                created_by=created_by,
            )
            context = {
                "experiment_id": child_experiment_id,
                "status": "planned_requires_materialization",
                "output_dir": str(child_output_dir),
                "materialization": {"status": child.get("status_reason", "not_executable_with_current_runner")},
            }
            status = "planned"
        registry.add_sweep_child(
            db_path,
            {
                "sweep_id": plan["sweep_id"],
                "child_experiment_id": child_experiment_id,
                "child_index": child["child_index"],
                "child_role": child["child_role"],
                "parameter_diff": child.get("parameter_diff"),
                "cost_diff": child.get("cost_diff"),
                "execution_diff": child.get("execution_diff"),
                "window_diff": child.get("window_diff"),
                "status": status,
            },
        )
        child_records.append({**child, "child_experiment_id": child_experiment_id, "run_context": context})
    result = {**plan, "children": child_records, "status": "prepared", "sweep_dir": str(sweep_dir)}
    plan_path = sweep_dir / "sweep_plan.json"
    plan_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    registry.attach_artifact(db_path, plan["parent_experiment_id"], "sweep_plan", plan_path)
    return result


def show_sweep(db_path: str | Path, sweep_id: str) -> dict[str, Any]:
    sweep = registry.get_sweep(db_path, sweep_id)
    if not sweep:
        raise ValueError(f"sweep not found: {sweep_id}")
    return {**sweep, "children": registry.list_sweep_children(db_path, sweep_id)}


def run_sweep(
    db_path: str | Path,
    *,
    sweep_id: str,
    dry_run: bool = False,
    limit: int | None = None,
    continue_on_error: bool = False,
    runner_script: str | Path = REPO_ROOT / "automated" / "scripts" / "run_backtest.sh",
    output_root: str | Path = runner.RESEARCH_RUNS_DIR,
) -> dict[str, Any]:
    sweep = registry.get_sweep(db_path, sweep_id)
    if not sweep:
        raise ValueError(f"sweep not found: {sweep_id}")
    children = registry.list_sweep_children(db_path, sweep_id)
    executable = [child for child in children if child["status"] == "prepared"]
    selected = executable[:limit] if limit is not None else executable
    if dry_run:
        return {"sweep_id": sweep_id, "dry_run": True, "children_to_run": [child["child_experiment_id"] for child in selected]}
    registry.update_sweep(db_path, sweep_id, status="running")
    results = []
    failed = False
    for child in selected:
        experiment_id = child["child_experiment_id"]
        experiment_dir = runner.experiment_output_dir(experiment_id, output_root)
        try:
            result = runner.run_prepared_experiment(
                db_path=db_path,
                experiment_id=experiment_id,
                research_output_dir=experiment_dir,
                runner_script=runner_script,
            )
            child_status = "completed" if result["returncode"] == 0 else "failed"
            failed = failed or result["returncode"] != 0
            registry.update_sweep_child(db_path, sweep_id, experiment_id, status=child_status)
            results.append({"child_experiment_id": experiment_id, **result})
        except Exception as exc:
            failed = True
            registry.update_sweep_child(db_path, sweep_id, experiment_id, status="failed")
            results.append({"child_experiment_id": experiment_id, "error": str(exc)})
            if not continue_on_error:
                break
    registry.update_sweep(db_path, sweep_id, status="failed" if failed else "completed", completed_at=registry.utc_now())
    return {"sweep_id": sweep_id, "dry_run": False, "results": results, "status": "failed" if failed else "completed"}


def _json_loads(value: str | None) -> Any:
    return json.loads(value) if value else None


def _metric_value(db_path: str | Path, experiment_id: str, key: str) -> float | None:
    metrics = registry.get_experiment_metrics(db_path, experiment_id)
    if metrics and metrics.get(key) is not None:
        return float(metrics[key])
    experiment = registry.get_experiment(db_path, experiment_id)
    if experiment and experiment.get("headline_metrics_json"):
        data = json.loads(experiment["headline_metrics_json"])
        value = data.get(key)
        return float(value) if value is not None else None
    return None


def _plateau_score(children: list[dict[str, Any]], child_metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    by_parameter: dict[str, list[tuple[float, bool]]] = {}
    for child in children:
        diff = _json_loads(child.get("parameter_diff_json")) or {}
        if len(diff) != 1:
            continue
        key, item = next(iter(diff.items()))
        try:
            value = float(item["to"])
        except (TypeError, ValueError):
            continue
        metrics = child_metrics.get(child["child_experiment_id"], {})
        passes = (metrics.get("net_return") or 0) > 0 and (metrics.get("profit_factor") or 0) > 1
        by_parameter.setdefault(key, []).append((value, passes))
    result: dict[str, Any] = {}
    for key, values in by_parameter.items():
        ordered = sorted(values)
        passing_with_neighbor = 0
        isolated_passes = 0
        for index, (_value, passes) in enumerate(ordered):
            if not passes:
                continue
            neighbor_passes = (index > 0 and ordered[index - 1][1]) or (index < len(ordered) - 1 and ordered[index + 1][1])
            if neighbor_passes:
                passing_with_neighbor += 1
            else:
                isolated_passes += 1
        total_passes = passing_with_neighbor + isolated_passes
        result[key] = {
            "value": passing_with_neighbor / total_passes if total_passes else 0,
            "passing_points": total_passes,
            "isolated_passes": isolated_passes,
            "status": "warn" if isolated_passes and not passing_with_neighbor else "pass" if total_passes else "not_available",
        }
    return result


def summarize_sweep(db_path: str | Path, *, sweep_id: str, output_path: str | Path | None = None) -> dict[str, Any]:
    sweep = registry.get_sweep(db_path, sweep_id)
    if not sweep:
        raise ValueError(f"sweep not found: {sweep_id}")
    children = registry.list_sweep_children(db_path, sweep_id)
    parent_id = sweep["parent_experiment_id"]
    child_metrics: dict[str, dict[str, Any]] = {}
    completed = 0
    for child in children:
        experiment_id = child["child_experiment_id"]
        net_return = _metric_value(db_path, experiment_id, "net_return")
        profit_factor = _metric_value(db_path, experiment_id, "profit_factor")
        sharpe = _metric_value(db_path, experiment_id, "sharpe")
        max_drawdown = _metric_value(db_path, experiment_id, "max_drawdown")
        if net_return is not None or profit_factor is not None:
            completed += 1
        child_metrics[experiment_id] = {
            "net_return": net_return,
            "profit_factor": profit_factor,
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
        }
    if sweep["sweep_type"] == "parameter_robustness":
        available = [metrics for metrics in child_metrics.values() if metrics["net_return"] is not None]
        parent_net = _metric_value(db_path, parent_id, "net_return")
        parent_pf = _metric_value(db_path, parent_id, "profit_factor")
        net_returns = [item["net_return"] for item in available if item["net_return"] is not None]
        pfs = [item["profit_factor"] for item in available if item["profit_factor"] is not None]
        sharpes = [item["sharpe"] for item in available if item["sharpe"] is not None]
        dds = [item["max_drawdown"] for item in available if item["max_drawdown"] is not None]
        plateau = _plateau_score(children, child_metrics)
        status = "not_available" if not available else "warn"
        if available and net_returns and sum(1 for item in net_returns if item > 0) / len(net_returns) >= 0.5:
            status = "pass"
        report = {
            "schema_version": "sweep_summary_v1",
            "sweep_id": sweep_id,
            "sweep_type": sweep["sweep_type"],
            "parent_experiment_id": parent_id,
            "status": status,
            "parent_metrics": {"net_return": parent_net, "profit_factor": parent_pf},
            "children": [{**child, "metrics": child_metrics[child["child_experiment_id"]]} for child in children],
            "robustness": {
                "number_of_children_completed": completed,
                "percent_profitable": sum(1 for item in net_returns if item > 0) / len(net_returns) if net_returns else None,
                "percent_pf_above_1": sum(1 for item in pfs if item > 1) / len(pfs) if pfs else None,
                "median_sharpe": median(sharpes) if sharpes else None,
                "median_net_return": median(net_returns) if net_returns else None,
                "max_drawdown_median": median(dds) if dds else None,
                "degradation_from_parent": median(net_returns) - parent_net if net_returns and parent_net is not None else None,
                "plateau_score": plateau,
            },
        }
    elif sweep["sweep_type"] == "cost_stress":
        report = {
            "schema_version": "sweep_summary_v1",
            "sweep_id": sweep_id,
            "sweep_type": sweep["sweep_type"],
            "parent_experiment_id": parent_id,
            "status": "not_available",
            "reason": "not_executable_with_current_runner",
            "children": [{**child, "metrics": child_metrics[child["child_experiment_id"]]} for child in children],
        }
    else:
        report = {
            "schema_version": "sweep_summary_v1",
            "sweep_id": sweep_id,
            "sweep_type": sweep["sweep_type"],
            "parent_experiment_id": parent_id,
            "status": "not_implemented",
            "reason": f"{sweep['sweep_type']} is scaffolded in phase 7",
        }
    path = Path(output_path) if output_path else SWEEP_ROOT / sweep_id / "sweep_summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    registry.update_sweep(db_path, sweep_id, summary_path=str(path), status="completed_with_warnings" if report["status"] in {"warn", "not_available", "not_implemented"} else "completed", completed_at=registry.utc_now())
    registry.attach_artifact(db_path, parent_id, "sweep_summary", path)
    return report
