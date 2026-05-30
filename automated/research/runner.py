from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import implementation as impl_mod, registry
from .contracts import REQUIRED_RESULT_ARTIFACT_TYPES
from .datasets import dataset_bundle_hash
from .hashing import (
    file_sha256,
    git_code_version,
    hash_cost_config,
    hash_execution_config,
    hash_parameter_set,
    hash_strategy_spec,
)
from .portfolio import write_portfolio_report
from .schemas import REPO_ROOT, load_yaml, resolve_hypothesis_file, validate_hypothesis, validate_strategy_spec
from .validation import write_validation_report


RESEARCH_RUNS_DIR = REPO_ROOT / "automated" / "research_runs"

RUNNER_ARTIFACTS = {
    "trade_log": "trades.csv",
    "equity_curve": "equity.csv",
    "bars": "bars.csv",
    "metrics_json": "run_summary.json",
    "raw_backtest_output": "terminal_run.log",
    "tester_agent_log": "tester_agent.log",
    "compile_log": "compile.log",
    "mt5_report": "mt5_report.htm",
}

REQUIRED_ARTIFACT_TYPES = {"trade_log", "equity_curve", "metrics_json", "raw_backtest_output"}
assert REQUIRED_ARTIFACT_TYPES == REQUIRED_RESULT_ARTIFACT_TYPES


def repo_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else REPO_ROOT / path


def experiment_output_dir(experiment_id: str, output_root: str | Path = RESEARCH_RUNS_DIR) -> Path:
    return Path(output_root) / experiment_id


def runner_report_dir(runner_run_id: str) -> Path:
    return REPO_ROOT / "automated" / "reports" / runner_run_id


def ensure_output_tree(output_dir: Path) -> None:
    for child in ["raw", "parsed", "reports"]:
        (output_dir / child).mkdir(parents=True, exist_ok=True)


def write_runner_config(base_config: str | Path, output_path: str | Path, runner_run_id: str) -> Path:
    base_path = repo_path(base_config)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = base_path.read_text(encoding="utf-8").splitlines()
    replaced = False
    rewritten: list[str] = []
    for line in lines:
        if line.strip().startswith("RUN_ID="):
            rewritten.append(f'RUN_ID="{runner_run_id}"')
            replaced = True
        else:
            rewritten.append(line)
    if not replaced:
        rewritten.insert(0, f'RUN_ID="{runner_run_id}"')
    output.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    return output


def _hypothesis_present(hypothesis_id: str) -> bool:
    path = resolve_hypothesis_file(hypothesis_id)
    if not path:
        return False
    validate_hypothesis(load_yaml(path))
    return True


def _complexity_score(spec: dict[str, Any]) -> int:
    parameters = spec.get("parameters", {})
    filters = spec.get("filters", [])
    exit_rules = spec.get("exit", {}).get("rules", [])
    regime_conditions = spec.get("regime_filters", [])
    excluded_assets = spec.get("assets_excluded_after_initial_test", [])
    return (
        len(parameters if isinstance(parameters, dict) else {})
        + 2 * len(filters if isinstance(filters, list) else [])
        + 2 * len(exit_rules if isinstance(exit_rules, list) else [])
        + 3 * len(regime_conditions if isinstance(regime_conditions, list) else [])
        + len(excluded_assets if isinstance(excluded_assets, list) else [])
    )


def prepare_run(
    *,
    db_path: str | Path,
    strategy_spec_path: str | Path,
    dataset_id: str | None,
    dataset_bundle_id: str | None = None,
    experiment_id: str,
    runner_run_id: str | None = None,
    output_root: str | Path = RESEARCH_RUNS_DIR,
    run_reason: str = "manual",
    created_by: str = "human",
    change_type: str = "baseline",
    change_summary: str = "Prepared experiment run.",
    rationale: str = "Research OS prepared run.",
    parameter_diff: dict[str, Any] | None = None,
    structural_diff: dict[str, Any] | None = None,
    parent_experiment_id: str | None = None,
    rerun_of_experiment_id: str | None = None,
    config_override_path: str | Path | None = None,
    parameter_override_path: str | Path | None = None,
    materialization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = load_yaml(strategy_spec_path)
    validate_strategy_spec(spec)
    hypothesis_present = _hypothesis_present(spec["hypothesis_id"])
    if not hypothesis_present:
        raise ValueError(f"hypothesis does not resolve: {spec['hypothesis_id']}")

    approval = impl_mod.require_generated_baseline_approval(
        db_path, spec["strategy_id"], spec["strategy_version"],
        allow_mock_compile=True,
    )
    if not approval["approved"]:
        raise ValueError(
            f"Generated implementation not approved for baseline: "
            f"{'; '.join(approval['errors'])}"
        )

    dataset = None
    dataset_hash = None
    if dataset_id:
        dataset = registry.get_dataset(db_path, dataset_id)
        if not dataset:
            raise ValueError(f"dataset not found: {dataset_id}")
        dataset_hash = dataset["file_hash"]
    elif dataset_bundle_id:
        raise NotImplementedError("dataset bundles are reserved for a later phase")
    else:
        raise ValueError("dataset_id or dataset_bundle_id is required")

    runner_run_id = runner_run_id or experiment_id
    output_dir = experiment_output_dir(experiment_id, output_root)
    ensure_output_tree(output_dir)

    implementation_files = spec["implementation"]["files"]
    config_path = Path(config_override_path) if config_override_path else repo_path(implementation_files["config"])
    parameter_path = Path(parameter_override_path) if parameter_override_path else repo_path(implementation_files["parameters"])
    generated_runner_config = write_runner_config(
        config_path,
        output_dir / "raw" / "runner.conf",
        runner_run_id,
    )
    runner_output_dir = runner_report_dir(runner_run_id)

    run_context = {
        "experiment_id": experiment_id,
        "strategy_id": spec["strategy_id"],
        "hypothesis_id": spec["hypothesis_id"],
        "dataset_id": dataset_id,
        "dataset_bundle_id": dataset_bundle_id,
        "spec_hash": hash_strategy_spec(spec),
        "parameter_set_hash": hash_parameter_set(parameter_path),
        "code_version": git_code_version(REPO_ROOT),
        "run_id": runner_run_id,
        "runner_run_id": runner_run_id,
        "runner_config_path": str(generated_runner_config),
        "parameter_file_path": str(parameter_path),
        "runner_output_dir": str(runner_output_dir),
        "output_dir": str(output_dir),
        "materialization": materialization or {},
        "started_at": None,
        "status": "prepared",
    }
    (output_dir / "run_context.json").write_text(json.dumps(run_context, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    payload = {
        "experiment_id": experiment_id,
        "hypothesis_id": spec["hypothesis_id"],
        "strategy_id": spec["strategy_id"],
        "strategy_version": spec["strategy_version"],
        "run_reason": run_reason,
        "created_by": created_by,
        "created_at": registry.utc_now(),
        "spec_hash": run_context["spec_hash"],
        "parameter_set_hash": run_context["parameter_set_hash"],
        "dataset_id": dataset_id,
        "dataset_bundle_id": dataset_bundle_id,
        "dataset_hash": dataset_hash,
        "dataset_bundle_hash": dataset_bundle_hash([]) if dataset_bundle_id else None,
        "code_version": run_context["code_version"],
        "execution_config_hash": hash_execution_config(generated_runner_config),
        "cost_config_hash": hash_cost_config(spec["costs"]),
        "engine": spec["implementation"]["engine"],
        "implementation_files": {
            **implementation_files,
            "generated_runner_config": str(generated_runner_config),
            "materialized_parameters": str(parameter_path) if parameter_override_path else None,
            "materialized_base_config": str(config_path) if config_override_path else None,
        },
        "implementation_mode": spec["implementation"]["generation_mode"],
        "execution_timing": spec["execution_timing"],
        "timeframe": spec["timeframe"],
        "universe": spec["universe"],
        "parent_experiment_id": parent_experiment_id,
        "rerun_of_experiment_id": rerun_of_experiment_id,
        "is_artifact_regeneration": run_reason == "artifact_regeneration",
        "change_type": change_type,
        "change_summary": change_summary,
        "rationale": rationale,
        "parameter_diff": parameter_diff,
        "structural_diff": structural_diff,
        "research_budget_snapshot": spec["research_budget"],
        "complexity_score": _complexity_score(spec),
        "min_trades_required": spec["validation"]["min_trades_required"],
        "cost_assumptions_documented": spec["costs"].get("assumptions_documented") is True,
        "dataset_metadata_present": dataset is not None,
        "hypothesis_present": hypothesis_present,
        "validation_report_path": None,
        "gate_status": "incomplete",
        "started_at": None,
        "completed_at": None,
        "status": "prepared",
    }
    registry.create_experiment(db_path, payload)
    registry.attach_artifact(db_path, experiment_id, "run_context", output_dir / "run_context.json")
    registry.attach_artifact(db_path, experiment_id, "runner_config", generated_runner_config)
    return run_context


def read_run_context(output_dir: str | Path) -> dict[str, Any]:
    path = Path(output_dir) / "run_context.json"
    if not path.is_file():
        raise FileNotFoundError(f"run_context.json not found in {output_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_artifact(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def attach_runner_outputs(
    *,
    db_path: str | Path,
    experiment_id: str,
    runner_output_dir: str | Path,
    research_output_dir: str | Path | None = None,
    artifact_regenerated: bool = False,
    generate_validation: bool = True,
) -> dict[str, Any]:
    experiment = registry.get_experiment(db_path, experiment_id)
    if not experiment:
        raise ValueError(f"experiment not found: {experiment_id}")
    output_dir = Path(research_output_dir) if research_output_dir else experiment_output_dir(experiment_id)
    ensure_output_tree(output_dir)
    runner_dir = Path(runner_output_dir)
    raw_dir = output_dir / "raw"

    artifacts: list[dict[str, Any]] = []
    warnings: list[str] = []
    present_types: set[str] = set()
    for artifact_type, filename in RUNNER_ARTIFACTS.items():
        source = runner_dir / filename
        if not source.is_file():
            if artifact_type in REQUIRED_ARTIFACT_TYPES:
                warnings.append(f"missing required artifact: {filename}")
            continue
        destination = _copy_artifact(source, raw_dir / filename)
        artifact_record = {
            "artifact_type": artifact_type,
            "path": str(destination),
            "hash": file_sha256(destination),
            "size_bytes": destination.stat().st_size,
            "created_at": registry.utc_now(),
        }
        artifacts.append(artifact_record)
        present_types.add(artifact_type)
        registry.attach_artifact(
            db_path,
            experiment_id,
            artifact_type,
            destination,
            artifact_regenerated=artifact_regenerated,
        )

    required_present = REQUIRED_ARTIFACT_TYPES.issubset(present_types)
    runner_run_id = experiment_id
    context_path = output_dir / "run_context.json"
    if context_path.is_file():
        context = json.loads(context_path.read_text(encoding="utf-8"))
        runner_run_id = context.get("runner_run_id") or context.get("run_id") or experiment_id

    validation_report_path = output_dir / "reports" / "validation_report.json"
    portfolio_report_path = output_dir / "reports" / "portfolio_report.json"
    validation_report = None
    if required_present and generate_validation:
        validation_report = write_validation_report(db_path, experiment_id, validation_report_path)
        write_portfolio_report(db_path, experiment_id, portfolio_report_path)
        if validation_report["hard_failures"]:
            status = "failed"
        elif validation_report["warnings"]:
            status = "completed_with_warnings"
        else:
            status = "completed"
    elif required_present:
        status = "completed_with_warnings"
        warnings.append("validation generation was disabled")
    else:
        status = "failed"

    manifest = {
        "experiment_id": experiment_id,
        "runner_run_id": runner_run_id,
        "output_dir": str(output_dir),
        "runner_output_dir": str(runner_dir),
        "artifacts": artifacts,
        "required_artifacts_present": required_present,
        "warnings": warnings,
    }
    manifest_path = output_dir / "artifact_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    registry.attach_artifact(db_path, experiment_id, "artifact_manifest", manifest_path)

    headline_metrics: dict[str, Any] = {}
    metrics = raw_dir / "run_summary.json"
    if metrics.is_file():
        summary = json.loads(metrics.read_text(encoding="utf-8"))
        headline_metrics = {
            "net_return": summary.get("net_profit") / summary.get("start_balance") if summary.get("start_balance") else None,
            "sharpe": None,
            "max_drawdown": summary.get("max_equity_drawdown_pct"),
            "trade_count": summary.get("trades"),
            "win_rate": summary.get("win_rate_pct"),
            "avg_trade": summary.get("expectancy"),
            "profit_factor": summary.get("profit_factor"),
        }
    registry.update_experiment(
        db_path,
        experiment_id,
        status=status,
        completed_at=registry.utc_now(),
        headline_metrics_json=json.dumps(headline_metrics, sort_keys=True),
        validation_report_path=str(validation_report_path) if validation_report_path.is_file() else None,
        gate_status=validation_report["gate_status"] if validation_report else "fail",
    )
    return manifest


def run_prepared_experiment(
    *,
    db_path: str | Path,
    experiment_id: str,
    research_output_dir: str | Path,
    runner_script: str | Path = REPO_ROOT / "automated" / "scripts" / "run_backtest.sh",
) -> dict[str, Any]:
    output_dir = Path(research_output_dir)
    context = read_run_context(output_dir)
    if context["experiment_id"] != experiment_id:
        raise ValueError("run_context experiment_id does not match requested experiment_id")
    stdout_path = output_dir / "runner_stdout.log"
    stderr_path = output_dir / "runner_stderr.log"
    registry.update_experiment(db_path, experiment_id, status="running", started_at=registry.utc_now())
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        completed = subprocess.run(
            [str(runner_script), context["runner_config_path"]],
            cwd=str(REPO_ROOT),
            stdout=stdout,
            stderr=stderr,
            check=False,
        )
    registry.attach_artifact(db_path, experiment_id, "runner_stdout", stdout_path)
    registry.attach_artifact(db_path, experiment_id, "runner_stderr", stderr_path)
    if completed.returncode != 0:
        registry.update_experiment(db_path, experiment_id, status="failed", completed_at=registry.utc_now())
    if Path(context["runner_output_dir"]).is_dir():
        manifest = attach_runner_outputs(
            db_path=db_path,
            experiment_id=experiment_id,
            runner_output_dir=context["runner_output_dir"],
            research_output_dir=output_dir,
        )
    else:
        manifest = {
            "experiment_id": experiment_id,
            "runner_run_id": context["runner_run_id"],
            "output_dir": str(output_dir),
            "runner_output_dir": context["runner_output_dir"],
            "artifacts": [],
            "required_artifacts_present": False,
            "warnings": ["runner output directory was not created"],
        }
        (output_dir / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"returncode": completed.returncode, "manifest": manifest}


def _parse_ea_set_file_from_config(config_path: Path) -> str | None:
    for line in config_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("EA_SET_FILE="):
            raw = stripped.split("=", 1)[1].strip().strip('"').strip("'")
            return raw if raw else None
    return None


def experiment_debug_runner_paths(output_dir: str | Path) -> dict[str, Any]:
    output_dir = Path(output_dir)
    context = read_run_context(output_dir)
    runner_config_path = Path(context["runner_config_path"])
    parameter_file_path = Path(context["parameter_file_path"])
    ea_set_file_raw = _parse_ea_set_file_from_config(runner_config_path)
    return {
        "experiment_id": context["experiment_id"],
        "runner_config_path": str(runner_config_path.resolve()),
        "runner_config_exists": runner_config_path.is_file(),
        "parameter_file_path": str(parameter_file_path.resolve()),
        "parameter_file_exists": parameter_file_path.is_file(),
        "ea_set_file_raw": ea_set_file_raw,
        "ea_set_file_resolved": str((runner_config_path.parent / ea_set_file_raw).resolve()) if ea_set_file_raw and not ea_set_file_raw.startswith("/") else (ea_set_file_raw if ea_set_file_raw else None),
        "runner_cwd": str(REPO_ROOT),
        "runner_output_dir": context["runner_output_dir"],
        "run_context": context,
    }
