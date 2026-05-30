from __future__ import annotations

import argparse
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from . import agents, backtest_readiness, compiler, datasets, generated_baseline, generated_final_holdout, generated_robustness, intake, lifecycle, queue, registry, sweeps
from .hashing import (
    git_code_version,
    hash_cost_config,
    hash_execution_config,
    hash_parameter_set,
    hash_strategy_spec,
    verify_bound_packet_digest,
)
from . import implementation as impl_mod
from .schemas import (
    REPO_ROOT,
    SANDBOX_ROOT,
    load_yaml,
    resolve_hypothesis_file,
    validate_hypothesis,
    validate_strategy_spec,
)
from .contracts import check_approval_usable
from .validation import write_validation_report
from .portfolio import (
    default_portfolio_report_path,
    load_portfolio_config,
    validate_portfolio_config,
    write_configured_portfolio_report,
    write_portfolio_report,
)
from . import runner as experiment_runner


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()


def _repo_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else REPO_ROOT / path


def _experiment_id(strategy_id: str) -> str:
    return f"EXP_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{_slug(strategy_id)[:48]}"


def cmd_register_dataset(args: argparse.Namespace) -> int:
    dataset = datasets.register_dataset(
        args.db,
        bars_path=args.bars,
        symbol=args.symbol,
        timeframe=args.timeframe,
        source_name=args.source_name,
        broker=args.broker,
        server=args.server,
        timezone_name=args.timezone,
        missing_data_policy=args.missing_data_policy,
        cleaning_rules=args.cleaning_rules,
    )
    print(json.dumps(dataset, indent=2, sort_keys=True))
    return 0


def cmd_validate_strategy_spec(args: argparse.Namespace) -> int:
    spec = load_yaml(args.strategy_spec)
    validate_strategy_spec(spec)
    implementation_files = spec["implementation"]["files"]
    parameter_hash = hash_parameter_set(_repo_path(implementation_files["parameters"]))
    result = {
        "strategy_id": spec["strategy_id"],
        "strategy_version": spec["strategy_version"],
        "hypothesis_id": spec["hypothesis_id"],
        "spec_hash": hash_strategy_spec(spec),
        "parameter_set_hash": parameter_hash,
        "status": "valid",
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _load_hypothesis_for_spec(spec: dict[str, Any]) -> tuple[bool, str | None]:
    path = resolve_hypothesis_file(spec["hypothesis_id"])
    if not path:
        return False, None
    hypothesis = load_yaml(path)
    validate_hypothesis(hypothesis)
    return True, str(path)


def _complexity_score(spec: dict[str, Any]) -> int:
    parameters = spec.get("parameters", {})
    filters = spec.get("filters", [])
    exit_rules = spec.get("exit", {}).get("rules", [])
    regime_conditions = spec.get("regime_filters", [])
    excluded_assets = spec.get("assets_excluded_after_initial_test", [])
    return (
        len(parameters)
        + 2 * len(filters)
        + 2 * len(exit_rules if isinstance(exit_rules, list) else [])
        + 3 * len(regime_conditions if isinstance(regime_conditions, list) else [])
        + len(excluded_assets if isinstance(excluded_assets, list) else [])
    )


def cmd_create_experiment(args: argparse.Namespace) -> int:
    spec = load_yaml(args.strategy_spec)
    validate_strategy_spec(spec)
    hypothesis_present, _hypothesis_path = _load_hypothesis_for_spec(spec)
    dataset = registry.get_dataset(args.db, args.dataset_id)
    if not dataset:
        raise SystemExit(f"dataset not found: {args.dataset_id}")

    implementation_files = spec["implementation"]["files"]
    config_path = _repo_path(implementation_files["config"])
    parameter_path = _repo_path(implementation_files["parameters"])
    experiment_id = args.experiment_id or _experiment_id(spec["strategy_id"])
    payload = {
        "experiment_id": experiment_id,
        "hypothesis_id": spec["hypothesis_id"],
        "strategy_id": spec["strategy_id"],
        "strategy_version": spec["strategy_version"],
        "run_reason": args.run_reason,
        "created_by": args.created_by,
        "created_at": registry.utc_now(),
        "spec_hash": hash_strategy_spec(spec),
        "parameter_set_hash": hash_parameter_set(parameter_path),
        "dataset_id": dataset["dataset_id"],
        "dataset_bundle_id": None,
        "dataset_hash": dataset["file_hash"],
        "dataset_bundle_hash": None,
        "code_version": git_code_version(REPO_ROOT),
        "execution_config_hash": hash_execution_config(config_path),
        "cost_config_hash": hash_cost_config(spec["costs"]),
        "engine": spec["implementation"]["engine"],
        "implementation_files": implementation_files,
        "implementation_mode": spec["implementation"]["generation_mode"],
        "execution_timing": spec["execution_timing"],
        "timeframe": spec["timeframe"],
        "universe": spec["universe"],
        "parent_experiment_id": args.parent_experiment_id,
        "rerun_of_experiment_id": args.rerun_of_experiment_id,
        "is_artifact_regeneration": args.run_reason == "artifact_regeneration",
        "change_type": args.change_type,
        "change_summary": args.change_summary,
        "rationale": args.rationale,
        "parameter_diff": json.loads(args.parameter_diff) if args.parameter_diff else None,
        "structural_diff": json.loads(args.structural_diff) if args.structural_diff else None,
        "research_budget_snapshot": spec["research_budget"],
        "complexity_score": _complexity_score(spec),
        "min_trades_required": spec["validation"]["min_trades_required"],
        "cost_assumptions_documented": spec["costs"].get("assumptions_documented") is True,
        "dataset_metadata_present": True,
        "hypothesis_present": hypothesis_present,
        "validation_report_path": None,
        "gate_status": "incomplete",
        "started_at": None,
        "completed_at": None,
        "status": "planned",
    }
    registry.create_experiment(args.db, payload)
    print(json.dumps({"experiment_id": experiment_id, "status": "created"}, indent=2, sort_keys=True))
    return 0


def cmd_attach_artifact(args: argparse.Namespace) -> int:
    registry.attach_artifact(
        args.db,
        args.experiment_id,
        args.artifact_type,
        args.path,
        artifact_regenerated=args.artifact_regenerated,
    )
    print(json.dumps({"experiment_id": args.experiment_id, "artifact_type": args.artifact_type, "path": args.path}, indent=2))
    return 0


def cmd_attach_result_artifacts(args: argparse.Namespace) -> int:
    report_dir = Path(args.report_dir)
    artifact_map = {
        "trade_log": report_dir / "trades.csv",
        "equity_curve": report_dir / "equity.csv",
        "bars": report_dir / "bars.csv",
        "metrics_json": report_dir / "run_summary.json",
        "raw_backtest_output": report_dir / "terminal_run.log",
        "tester_agent_log": report_dir / "tester_agent.log",
        "compile_log": report_dir / "compile.log",
    }
    for artifact_type, path in artifact_map.items():
        if path.is_file():
            registry.attach_artifact(args.db, args.experiment_id, artifact_type, path)
    metrics_path = artifact_map["metrics_json"]
    headline_metrics: dict[str, Any] = {}
    if metrics_path.is_file():
        with metrics_path.open("r", encoding="utf-8") as handle:
            summary = json.load(handle)
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
        args.db,
        args.experiment_id,
        status="completed",
        completed_at=registry.utc_now(),
        headline_metrics_json=json.dumps(headline_metrics, sort_keys=True),
    )
    print(json.dumps({"experiment_id": args.experiment_id, "attached_from": str(report_dir)}, indent=2, sort_keys=True))
    return 0


def cmd_generate_validation_report(args: argparse.Namespace) -> int:
    output = args.output
    if not output:
        output = REPO_ROOT / "automated" / "reports" / args.experiment_id / "validation_report.json"
    report = write_validation_report(args.db, args.experiment_id, output)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def cmd_validation_generate(args: argparse.Namespace) -> int:
    output = args.output
    if not output:
        output = REPO_ROOT / "automated" / "research_runs" / args.experiment_id / "reports" / "validation_report.json"
    report = write_validation_report(args.db, args.experiment_id, output)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def cmd_validation_show(args: argparse.Namespace) -> int:
    experiment = registry.get_experiment(args.db, args.experiment_id)
    if not experiment:
        raise SystemExit(f"experiment not found: {args.experiment_id}")
    path = experiment.get("validation_report_path")
    if not path or not Path(path).is_file():
        default_path = REPO_ROOT / "automated" / "research_runs" / args.experiment_id / "reports" / "validation_report.json"
        if default_path.is_file():
            path = str(default_path)
        else:
            raise SystemExit(f"validation report not found for {args.experiment_id}")
    print(Path(path).read_text(encoding="utf-8"), end="")
    return 0


def cmd_generate_portfolio_report(args: argparse.Namespace) -> int:
    output = args.output
    if not output:
        output = REPO_ROOT / "automated" / "reports" / args.experiment_id / "portfolio_report.json"
    report = write_portfolio_report(args.db, args.experiment_id, output)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def cmd_portfolio_validate_config(args: argparse.Namespace) -> int:
    config = load_portfolio_config(args.portfolio)
    print(json.dumps({"portfolio_id": config["portfolio_id"], "status": "valid"}, indent=2, sort_keys=True))
    return 0


def cmd_portfolio_generate(args: argparse.Namespace) -> int:
    report = write_configured_portfolio_report(args.db, args.portfolio, args.output)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def cmd_portfolio_show(args: argparse.Namespace) -> int:
    config = validate_portfolio_config(load_yaml(args.portfolio))
    path = Path(args.output) if args.output else default_portfolio_report_path(config)
    if not path.is_file():
        write_configured_portfolio_report(args.db, args.portfolio, path)
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def cmd_portfolio_evaluate_candidate(args: argparse.Namespace) -> int:
    report = write_configured_portfolio_report(
        args.db,
        args.portfolio,
        args.output,
        candidate_experiment_id=args.candidate_experiment_id,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def cmd_list_experiments(args: argparse.Namespace) -> int:
    print(json.dumps(registry.list_experiments(args.db), indent=2, sort_keys=True))
    return 0


def cmd_experiment_metrics(args: argparse.Namespace) -> int:
    metrics = registry.get_experiment_metrics(args.db, args.experiment_id)
    if not metrics:
        raise SystemExit(f"metrics not found for {args.experiment_id}")
    print(json.dumps(metrics, indent=2, sort_keys=True))
    return 0


def cmd_experiment_prepare_run(args: argparse.Namespace) -> int:
    spec = load_yaml(args.strategy)
    validate_strategy_spec(spec)
    experiment_id = args.experiment_id or _experiment_id(spec["strategy_id"])
    context = experiment_runner.prepare_run(
        db_path=args.db,
        strategy_spec_path=args.strategy,
        dataset_id=args.dataset_id,
        dataset_bundle_id=args.dataset_bundle_id,
        experiment_id=experiment_id,
        runner_run_id=args.runner_run_id,
        output_root=args.output_root,
        run_reason=args.run_reason,
        created_by=args.created_by,
        change_type=args.change_type,
        change_summary=args.change_summary or f"Prepared {args.change_type} run for {spec['strategy_id']}.",
        rationale=args.rationale or "Prepared through Research OS experiment context.",
        parameter_diff=json.loads(args.parameter_diff) if args.parameter_diff else None,
        structural_diff=json.loads(args.structural_diff) if args.structural_diff else None,
        parent_experiment_id=args.parent_experiment_id,
        rerun_of_experiment_id=args.rerun_of_experiment_id,
    )
    print(json.dumps({"experiment_id": context["experiment_id"], "output_dir": context["output_dir"], "run_context": context}, indent=2, sort_keys=True))
    return 0


def cmd_experiment_attach_artifacts(args: argparse.Namespace) -> int:
    manifest = experiment_runner.attach_runner_outputs(
        db_path=args.db,
        experiment_id=args.experiment_id,
        runner_output_dir=args.output_dir,
        research_output_dir=args.research_output_dir,
        artifact_regenerated=args.artifact_regenerated,
        generate_validation=not args.no_validation,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def cmd_experiment_run(args: argparse.Namespace) -> int:
    spec = load_yaml(args.strategy)
    validate_strategy_spec(spec)
    experiment_id = args.experiment_id or _experiment_id(spec["strategy_id"])
    context = experiment_runner.prepare_run(
        db_path=args.db,
        strategy_spec_path=args.strategy,
        dataset_id=args.dataset_id,
        dataset_bundle_id=args.dataset_bundle_id,
        experiment_id=experiment_id,
        runner_run_id=args.runner_run_id,
        output_root=args.output_root,
        run_reason=args.run_reason,
        created_by=args.created_by,
        change_type=args.change_type,
        change_summary=args.change_summary or f"Run {args.change_type} experiment for {spec['strategy_id']}.",
        rationale=args.rationale or "Launched through Research OS experiment context.",
        parameter_diff=json.loads(args.parameter_diff) if args.parameter_diff else None,
        structural_diff=json.loads(args.structural_diff) if args.structural_diff else None,
        parent_experiment_id=args.parent_experiment_id,
        rerun_of_experiment_id=args.rerun_of_experiment_id,
    )
    result = experiment_runner.run_prepared_experiment(
        db_path=args.db,
        experiment_id=experiment_id,
        research_output_dir=context["output_dir"],
        runner_script=args.runner_script,
    )
    print(json.dumps({"experiment_id": experiment_id, "output_dir": context["output_dir"], **result}, indent=2, sort_keys=True))
    return int(result["returncode"])


def cmd_lifecycle_show(args: argparse.Namespace) -> int:
    result = lifecycle.show_lifecycle(args.strategy)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_lifecycle_evaluate(args: argparse.Namespace) -> int:
    result = lifecycle.evaluate_transition(
        args.db,
        strategy=args.strategy,
        to_state=args.to_state,
        experiment_id=args.experiment_id,
        strictness=args.strictness,
        override=args.override,
        reason=args.reason,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result["status"] == "blocked" else 0


def cmd_lifecycle_propose(args: argparse.Namespace) -> int:
    result = lifecycle.propose_transition(
        args.db,
        strategy=args.strategy,
        to_state=args.to_state,
        experiment_id=args.experiment_id,
        reason=args.reason,
        requested_by=args.requested_by,
        approved_by=args.approved_by,
        strictness=args.strictness,
        override=args.override,
        notes=args.notes,
        snapshot_dir=args.snapshot_dir,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_lifecycle_apply(args: argparse.Namespace) -> int:
    result = lifecycle.apply_transition(args.db, transition_id=args.transition_id, strictness=args.strictness)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result["status"] == "blocked" else 0


def cmd_lifecycle_history(args: argparse.Namespace) -> int:
    _path, spec = lifecycle.load_strategy(args.strategy)
    result = registry.list_lifecycle_transitions(args.db, spec["strategy_id"])
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_agent_validate_output(args: argparse.Namespace) -> int:
    result = agents.validate_output_file(args.file)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_agent_attach_output(args: argparse.Namespace) -> int:
    result = agents.attach_output(args.db, experiment_id=args.experiment_id, path=args.file)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_agent_permissions(args: argparse.Namespace) -> int:
    result = agents.permissions_for_role(args.role)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_agent_list_contracts(args: argparse.Namespace) -> int:
    print(json.dumps(agents.list_contracts(), indent=2, sort_keys=True))
    return 0


def cmd_sweep_validate_config(args: argparse.Namespace) -> int:
    config = sweeps.load_sweep_config(args.config)
    print(json.dumps({"status": "valid", "sweep_type": config["sweep_type"], "parent_experiment_id": config["parent_experiment_id"]}, indent=2, sort_keys=True))
    return 0


def cmd_sweep_prepare(args: argparse.Namespace) -> int:
    result = sweeps.prepare_sweep(args.db, config_path=args.config, output_root=args.output_root, created_by=args.created_by)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_sweep_list(args: argparse.Namespace) -> int:
    print(json.dumps(registry.list_sweeps(args.db), indent=2, sort_keys=True))
    return 0


def cmd_sweep_show(args: argparse.Namespace) -> int:
    print(json.dumps(sweeps.show_sweep(args.db, args.sweep_id), indent=2, sort_keys=True))
    return 0


def cmd_sweep_run(args: argparse.Namespace) -> int:
    result = sweeps.run_sweep(
        args.db,
        sweep_id=args.sweep_id,
        dry_run=args.dry_run,
        limit=args.limit,
        continue_on_error=args.continue_on_error,
        runner_script=args.runner_script,
        output_root=args.output_root,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("dry_run") or result.get("status") != "failed" else 1


def cmd_sweep_summarize(args: argparse.Namespace) -> int:
    result = sweeps.summarize_sweep(args.db, sweep_id=args.sweep_id, output_path=args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_sweep_attach_child_artifacts(args: argparse.Namespace) -> int:
    child_ids = {child["child_experiment_id"] for child in registry.list_sweep_children(args.db, args.sweep_id)}
    if args.child_experiment_id not in child_ids:
        raise SystemExit(f"child experiment is not linked to sweep {args.sweep_id}: {args.child_experiment_id}")
    manifest = experiment_runner.attach_runner_outputs(
        db_path=args.db,
        experiment_id=args.child_experiment_id,
        runner_output_dir=args.output_dir,
        research_output_dir=args.research_output_dir,
        artifact_regenerated=args.artifact_regenerated,
        generate_validation=not args.no_validation,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def cmd_queue_validate(args: argparse.Namespace) -> int:
    result = queue.validate_queue(args.db, args.queue)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_queue_run(args: argparse.Namespace) -> int:
    result = queue.run_queue(
        args.db,
        args.queue,
        mode=args.mode,
        dry_run=args.dry_run,
        runner_script=args.runner_script,
        output_root=args.output_root,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("dry_run") or result.get("status") != "failed" else 1


def cmd_queue_report(args: argparse.Namespace) -> int:
    result = queue.generate_morning_report(args.db, run_id=args.run_id, output_root=args.output_root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_impl_create_request(args: argparse.Namespace) -> int:
    strategy_id = args.strategy_id
    strategy_version = args.strategy_version
    sandbox_dir = SANDBOX_ROOT / strategy_id / strategy_version
    expected_inputs = None
    if args.expected_inputs:
        expected_inputs = json.loads(args.expected_inputs)
    parameters = None
    if args.parameters:
        parameters = json.loads(args.parameters)
    result = impl_mod.create_implementation_request(
        args.db,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        sandbox_dir=sandbox_dir,
        generated_files=args.generated_files.split(",") if args.generated_files else [],
        created_by=args.created_by,
        hypothesis_id=args.hypothesis_id,
        strategy_spec_path=args.strategy_spec,
        expected_inputs=expected_inputs,
        parameters=parameters,
        entry_logic=args.entry_logic,
        exit_logic=args.exit_logic,
        risk_logic=args.risk_logic,
        test_plan=args.test_plan,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_impl_validate_request(args: argparse.Namespace) -> int:
    result = impl_mod.validate_request(args.db, args.implementation_request_id)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


def cmd_impl_inspect(args: argparse.Namespace) -> int:
    result = impl_mod.inspect(args.db, args.implementation_request_id)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_impl_compile_check(args: argparse.Namespace) -> int:
    if args.mock and args.real_compile_config:
        print(json.dumps({"error": "--mock and --real-compile-config are mutually exclusive"}, indent=2))
        return 1
    real_config = None
    compile_config_path = None
    if args.real_compile_config:
        try:
            compile_config_path = str(Path(args.real_compile_config).resolve())
            real_config = compiler.load_real_compile_config(compile_config_path)
        except (FileNotFoundError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}, indent=2))
            return 1
    result = impl_mod.compile_check(
        args.db, args.implementation_request_id,
        mock=args.mock, real_compile_config=real_config,
        compile_config_path=compile_config_path,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("compile_status") in ("mock_checked", "passed") else 1


def cmd_impl_backtest_readiness(args: argparse.Namespace) -> int:
    if args.real_backtest_readiness_config:
        try:
            readiness_config_path = str(Path(args.real_backtest_readiness_config).resolve())
            bt_config = backtest_readiness.load_real_backtest_readiness_config(readiness_config_path)
        except (FileNotFoundError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}, indent=2))
            return 1
    else:
        print(json.dumps({"error": "--real-backtest-readiness-config is required"}, indent=2))
        return 1
    result = backtest_readiness.run_real_backtest_readiness(
        args.db,
        args.implementation_request_id,
        bt_config,
        runner_conf_override=args.runner_conf_path,
        set_file_override=args.set_file_path,
        readiness_config_path=readiness_config_path,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "passed" else 1


def cmd_impl_diff_review(args: argparse.Namespace) -> int:
    result = impl_mod.run_diff_review(args.db, args.implementation_request_id)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_impl_readiness_review(args: argparse.Namespace) -> int:
    from . import readiness_review as rr_mod
    compile_path = Path(args.compile_evidence)
    if not compile_path.is_file():
        print(json.dumps({"error": f"Compile evidence not found: {compile_path}"}, indent=2))
        return 1
    bt_path = Path(args.backtest_readiness_evidence)
    if not bt_path.is_file():
        print(json.dumps({"error": f"Backtest readiness evidence not found: {bt_path}"}, indent=2))
        return 1
    out_path = Path(args.out) if args.out else None
    try:
        packet = rr_mod.build_readiness_review_packet(
            strategy_id=args.strategy_id,
            version=args.version,
            impl_request_id=args.implementation_request_id,
            compile_evidence_path=compile_path,
            backtest_readiness_evidence_path=bt_path,
            output_path=out_path,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1
    if out_path:
        print(json.dumps({"packet_path": str(out_path.resolve()), "status": "written", "artifact_type": packet["artifact_type"]}, indent=2))
    else:
        print(json.dumps(packet, indent=2, sort_keys=True))
    return 0


def cmd_impl_real_toolchain_rehearsal(args: argparse.Namespace) -> int:
    from . import toolchain_rehearsal as tc_mod
    if not args.real_compile_config:
        print(json.dumps({"error": "--real-compile-config is required"}, indent=2))
        return 1
    if not args.real_backtest_readiness_config:
        print(json.dumps({"error": "--real-backtest-readiness-config is required"}, indent=2))
        return 1
    if not args.out_dir:
        print(json.dumps({"error": "--out-dir is required"}, indent=2))
        return 1
    compile_config_path = str(Path(args.real_compile_config).resolve())
    bt_config_path = str(Path(args.real_backtest_readiness_config).resolve())
    out_dir = str(Path(args.out_dir).resolve())
    try:
        summary = tc_mod.run_toolchain_rehearsal(
            args.db,
            args.implementation_request_id,
            compile_config_path=compile_config_path,
            backtest_readiness_config_path=bt_config_path,
            out_dir=out_dir,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary.get("status") == "passed" else 1


def cmd_impl_approve_for_baseline(args: argparse.Namespace) -> int:
    result = impl_mod.approve_for_baseline(
        args.db,
        args.implementation_request_id,
        approved_by=args.approved_by,
        baseline_experiment_id=args.experiment_id,
        require_real_compile=not args.allow_mock_compile,
        approval_scope=args.approval_scope,
        allow_reuse=args.allow_reuse,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["approved"] else 1


def cmd_generated_baseline_run(args: argparse.Namespace) -> int:
    strategy_id = args.strategy_id
    strategy_version = args.strategy_version
    impl_request_id = args.implementation_request_id
    dataset_id = args.dataset_id
    allow_mock = args.allow_mock_compile
    allow_reuse = args.allow_reuse

    impl_req = registry.get_implementation_request(args.db, impl_request_id)
    if not impl_req:
        raise SystemExit(f"implementation request not found: {impl_request_id}")

    impls = registry.list_implementations(args.db, impl_request_id)
    if not impls:
        raise SystemExit("no implementation record found; run compile-check first")

    current_impl = impls[-1]

    guard = impl_mod.require_generated_baseline_approval(
        args.db,
        strategy_id,
        strategy_version,
        allow_mock_compile=allow_mock,
        check_scope=True,
        check_consumed=not allow_reuse,
    )
    if not guard["approved"]:
        raise SystemExit(
            f"Generated implementation not approved for baseline: "
            f"{'; '.join(guard['errors'])}"
        )

    experiment_id = args.experiment_id or (
        f"EXP_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        f"_{_slug(strategy_id)[:40]}_{uuid.uuid4().hex[:6].upper()}"
    )
    output_root = args.output_root or str(experiment_runner.RESEARCH_RUNS_DIR)
    spec_path = args.spec_path or queue._generated_strategy_spec_path(strategy_id)

    context = experiment_runner.prepare_run(
        db_path=args.db,
        strategy_spec_path=spec_path,
        dataset_id=dataset_id,
        experiment_id=experiment_id,
        output_root=output_root,
        run_reason="manual",
        created_by=args.created_by,
        change_type="baseline",
        change_summary=f"Generated baseline experiment for {strategy_id}.",
        rationale=f"CLI generated-baseline run for {strategy_id}.",
    )

    usage_id = f"USAGE_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8].upper()}"
    usage_record = {
        "usage_id": usage_id,
        "implementation_id": current_impl["implementation_id"],
        "implementation_request_id": impl_request_id,
        "experiment_id": experiment_id,
        "queue_run_id": None,
        "used_at": registry.utc_now(),
        "runner_mode": "cli",
        "status": "pending",
    }
    registry.create_approval_usage(args.db, usage_record)

    usage_status = "completed"
    run_result = None
    if args.run:
        run_result = experiment_runner.run_prepared_experiment(
            db_path=args.db,
            experiment_id=experiment_id,
            research_output_dir=context["output_dir"],
            runner_script=args.runner_script,
        )
        if run_result["returncode"] != 0:
            usage_status = "failed"
    elif args.prepare_only:
        pass
    else:
        usage_status = "completed"

    if usage_status == "failed":
        result_status = "failed"
    elif args.run and run_result and run_result["returncode"] == 0:
        result_status = "completed"
    else:
        result_status = "prepared"

    registry.update_approval_usage(args.db, usage_id, status=usage_status)

    output = {
        "experiment_id": experiment_id,
        "implementation_request_id": impl_request_id,
        "implementation_id": current_impl["implementation_id"],
        "usage_id": usage_id,
        "approval_scope": current_impl.get("approval_scope", "baseline_only"),
        "status": result_status,
        "usage_status": usage_status,
    }
    if context:
        output["output_dir"] = context["output_dir"]
    if run_result:
        output["runner_returncode"] = run_result["returncode"]
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if result_status != "failed" else 1


def cmd_generated_baseline_review(args: argparse.Namespace) -> int:
    experiment_id = args.experiment_id
    strategy_id = args.strategy_id
    strategy_version = args.strategy_version

    request = registry.find_implementation_request(args.db, strategy_id, strategy_version)
    impl_request_id = request["implementation_request_id"] if request else None

    implementations = []
    current_impl = None
    if request:
        implementations = registry.list_implementations(args.db, request["implementation_request_id"])
        if implementations:
            current_impl = implementations[-1]

    implementation_id = current_impl["implementation_id"] if current_impl else None
    approval_status = "not_approved"
    if current_impl and current_impl.get("approved_for_baseline"):
        approval_status = "approved_for_baseline"

    approval_usage = None
    if current_impl:
        usages = registry.list_approval_usage_for_implementation(args.db, current_impl["implementation_id"])
        if usages:
            approval_usage = usages[-1]

    result = generated_baseline.build_generated_baseline_review_packet(
        args.db,
        experiment_id=experiment_id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        implementation_request_id=impl_request_id,
        implementation_id=implementation_id,
        approval_status=approval_status,
        approval_usage=dict(approval_usage) if approval_usage else None,
        runner_mode="cli",
        output_dir=args.output,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_generated_robustness_run_sweep(args: argparse.Namespace) -> int:
    strategy_id = args.strategy_id
    strategy_version = args.strategy_version
    impl_request_id = args.implementation_request_id
    baseline_experiment_id = args.baseline_experiment_id
    child_cap = args.child_cap

    eligibility = generated_robustness.require_generated_robustness_eligibility(
        args.db, strategy_id, strategy_version, allow_mock_compile=args.allow_mock_compile,
    )
    if not eligibility["eligible"]:
        raise SystemExit(
            f"Generated strategy not eligible for robustness sweep: "
            f"{'; '.join(eligibility['errors'])}"
        )

    import yaml
    params_raw = json.loads(args.params)
    if not isinstance(params_raw, dict) or not params_raw:
        raise SystemExit("--params must be a non-empty JSON object mapping parameter names to value lists")

    param_warnings = generated_robustness.validate_sweep_parameters(params_raw)
    if param_warnings:
        raise SystemExit("; ".join(param_warnings))

    config = {
        "sweep_type": "parameter_robustness",
        "parent_experiment_id": baseline_experiment_id,
        "strategy_id": strategy_id,
        "spec_path": str(queue._generated_strategy_spec_path(strategy_id)),
        "mode": "one_variable_at_a_time",
        "parameters": params_raw,
        "budget": {
            "max_child_experiments": child_cap,
            "max_parameters_changed_per_child": 1,
            "require_one_variable_at_a_time": True,
        },
    }
    sweeps.validate_sweep_config(config)

    output_root = args.output_root or str(queue.QUEUE_RUN_ROOT)
    sweep_id = (
        f"SWEEP_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        f"_{_slug(strategy_id)[:40]}_{uuid.uuid4().hex[:6].upper()}"
    )

    config["sweep_id"] = sweep_id
    config_path = Path(output_root) / "cli_inputs" / f"{sweep_id}_config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    prepared = sweeps.prepare_sweep(
        args.db,
        config_path=config_path,
        output_root=output_root,
        created_by=args.created_by,
    )

    output: dict[str, Any] = {
        "sweep_id": prepared["sweep_id"],
        "status": "prepared",
        "children": [
            {
                "child_experiment_id": c["child_experiment_id"],
                "child_role": c["child_role"],
            }
            for c in prepared["children"]
        ],
    }

    if args.run:
        run_result = sweeps.run_sweep(
            args.db,
            sweep_id=prepared["sweep_id"],
            limit=child_cap,
            continue_on_error=True,
            runner_script=args.runner_script,
            output_root=output_root,
        )
        output["status"] = run_result.get("status", "completed")
        output["runner_results"] = [
            {
                "child_experiment_id": r["child_experiment_id"],
                "returncode": r.get("returncode"),
            }
            for r in run_result.get("results", [])
        ]
        if run_result.get("status") == "failed":
            out_code = 1
        else:
            out_code = 0
        try:
            summary = sweeps.summarize_sweep(
                args.db, sweep_id=prepared["sweep_id"],
                output_path=Path(output_root) / "sweep_summaries" / f"{sweep_id}_summary.json",
            )
            output["summary_path"] = summary.get("summary_path", "")
        except Exception as exc:
            output["summary_warning"] = str(exc)
    else:
        out_code = 0

    print(json.dumps(output, indent=2, sort_keys=True))
    return out_code


def cmd_generated_robustness_review(args: argparse.Namespace) -> int:
    result = generated_robustness.build_generated_robustness_review_packet(
        args.db,
        sweep_id=args.sweep_id,
        baseline_experiment_id=args.baseline_experiment_id,
        strategy_id=args.strategy_id,
        strategy_version=args.strategy_version,
        output_dir=Path(args.output) if args.output else None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_generated_candidate_decision_packet(args: argparse.Namespace) -> int:
    from . import generated_candidate
    result = generated_candidate.build_generated_candidate_decision_packet(
        args.db,
        strategy_id=args.strategy_id,
        strategy_version=args.strategy_version,
        implementation_request_id=args.implementation_request_id,
        baseline_experiment_id=args.baseline_experiment_id,
        robustness_sweep_id=args.robustness_sweep_id,
        output_dir=Path(args.output) if args.output else None,
    )
    packet = result["packet"]
    output = {
        "packet_path": result["packet_path"],
        "proposed_next_action": packet["proposed_next_action"],
        "lifecycle_proposal": packet["lifecycle_proposal"],
        "candidate_status": packet["candidate_status"],
        "decision_rules_applied": packet["decision_rules_applied"],
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def cmd_experiment_show_runner_paths(args: argparse.Namespace) -> int:
    debug_info = experiment_runner.experiment_debug_runner_paths(args.output_dir)
    print(json.dumps(debug_info, indent=2, sort_keys=True))
    return 0


def cmd_intake_generate_hypotheses(args: argparse.Namespace) -> int:
    constraints = {}
    if args.constraints:
        constraints = json.loads(args.constraints)
    result = intake.generate_hypotheses(
        research_theme=args.theme,
        symbol=args.symbol,
        timeframe=args.timeframe,
        market_regime=args.market_regime,
        strategy_family=args.strategy_family,
        max_hypotheses=args.max_hypotheses,
        constraints=constraints,
        created_by=args.created_by,
        hypothesis_set_dir=Path(args.output) if args.output else None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_intake_generate_spec(args: argparse.Namespace) -> int:
    result = intake.generate_strategy_spec(
        hypothesis_id=args.hypothesis_id,
        strategy_id=args.strategy_id,
        strategy_version=args.strategy_version,
        created_by=args.created_by,
        output_dir=Path(args.output) if args.output else None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_intake_materialize(args: argparse.Namespace) -> int:
    result = intake.materialize_implementation(
        args.db,
        strategy_spec_path=args.spec_path,
        strategy_id=args.strategy_id,
        strategy_version=args.strategy_version,
        created_by=args.created_by,
        mock_compile=args.mock,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "materialized" else 1


def cmd_intake_review_packet(args: argparse.Namespace) -> int:
    result = intake.build_review_packet(
        args.db,
        strategy_id=args.strategy_id,
        strategy_version=args.strategy_version,
        output_dir=Path(args.output) if args.output else None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_generated_final_holdout_approve(args: argparse.Namespace) -> int:
    result = generated_final_holdout.approve_for_final_holdout(
        args.db,
        args.implementation_request_id,
        decision_packet_path=args.decision_packet_path,
        approved_by=args.approved_by,
        allow_reuse=args.allow_reuse,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["approved"] else 1


def cmd_generated_final_holdout_run(args: argparse.Namespace) -> int:
    strategy_id = args.strategy_id
    strategy_version = args.strategy_version
    impl_request_id = args.implementation_request_id
    dataset_id = args.dataset_id
    decision_packet_path = args.decision_packet_path
    allow_reuse = args.allow_reuse
    allow_mock = args.allow_mock_compile

    impl_req = registry.get_implementation_request(args.db, impl_request_id)
    if not impl_req:
        raise SystemExit(f"implementation request not found: {impl_request_id}")

    impls = registry.list_implementations(args.db, impl_request_id)
    if not impls:
        raise SystemExit("no implementation record found; run compile-check first")

    current_impl = impls[-1]

    eligibility = generated_final_holdout.require_generated_final_holdout_eligibility(
        args.db,
        strategy_id,
        strategy_version,
        decision_packet_path=decision_packet_path,
        allow_mock_compile=allow_mock,
    )
    if not eligibility["eligible"]:
        raise SystemExit(
            f"Generated strategy not eligible for final holdout: "
            f"{'; '.join(eligibility['errors'])}"
        )

    approval = registry.find_scope_approval(
        args.db, strategy_id, strategy_version, generated_final_holdout.FINAL_HOLDOUT_SCOPE,
    )
    approval_error = check_approval_usable(approval)
    if approval_error:
        raise SystemExit(approval_error)

    if decision_packet_path:
        digest_error = verify_bound_packet_digest(approval, decision_packet_path)
        if digest_error:
            raise SystemExit(digest_error)

    protected_errors = generated_final_holdout.check_protected_config(
        decision_packet_path,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        dataset_id=dataset_id,
    )
    if protected_errors:
        raise SystemExit("Protected config mismatch: " + "; ".join(protected_errors))

    experiment_id = args.experiment_id or (
        f"FH_EXP_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        f"_{_slug(strategy_id)[:40]}_{uuid.uuid4().hex[:6].upper()}"
    )
    output_root = args.output_root or str(experiment_runner.RESEARCH_RUNS_DIR)
    spec_path = args.spec_path or queue._generated_strategy_spec_path(strategy_id)

    context = experiment_runner.prepare_run(
        db_path=args.db,
        strategy_spec_path=spec_path,
        dataset_id=dataset_id,
        experiment_id=experiment_id,
        output_root=output_root,
        run_reason="manual",
        created_by=args.created_by,
        change_type="final_holdout",
        change_summary=f"Generated final holdout experiment for {strategy_id}.",
        rationale=f"CLI generated-final-holdout run for {strategy_id}.",
    )

    usage_id = f"USAGE_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8].upper()}"
    usage_record = {
        "usage_id": usage_id,
        "implementation_id": current_impl["implementation_id"],
        "implementation_request_id": impl_request_id,
        "experiment_id": experiment_id,
        "queue_run_id": None,
        "used_at": registry.utc_now(),
        "runner_mode": "final_holdout",
        "status": "pending",
        "scope_approval_id": approval["approval_id"],
    }
    registry.create_approval_usage(args.db, usage_record)

    registry.update_scope_approval_used(args.db, approval["approval_id"], experiment_id)

    usage_status = "completed"
    run_result = None
    if args.run:
        run_result = experiment_runner.run_prepared_experiment(
            db_path=args.db,
            experiment_id=experiment_id,
            research_output_dir=context["output_dir"],
            runner_script=args.runner_script,
        )
        if run_result["returncode"] != 0:
            usage_status = "failed"
    elif args.prepare_only:
        pass
    else:
        usage_status = "completed"

    if usage_status == "failed":
        result_status = "failed"
    elif args.run and run_result and run_result["returncode"] == 0:
        result_status = "completed"
    else:
        result_status = "prepared"

    registry.update_approval_usage(args.db, usage_id, status=usage_status)

    output = {
        "experiment_id": experiment_id,
        "implementation_request_id": impl_request_id,
        "implementation_id": current_impl["implementation_id"],
        "usage_id": usage_id,
        "approval_id": approval["approval_id"],
        "approval_scope": generated_final_holdout.FINAL_HOLDOUT_SCOPE,
        "status": result_status,
        "usage_status": usage_status,
    }
    if context:
        output["output_dir"] = context["output_dir"]
    if run_result:
        output["runner_returncode"] = run_result["returncode"]
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if result_status != "failed" else 1


def cmd_generated_final_holdout_review(args: argparse.Namespace) -> int:
    result = generated_final_holdout.build_generated_final_holdout_review_packet(
        args.db,
        experiment_id=args.experiment_id,
        strategy_id=args.strategy_id,
        strategy_version=args.strategy_version,
        decision_packet_path=args.decision_packet_path,
        approval_id=args.approval_id,
        output_dir=Path(args.output) if args.output else None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research", description="Research OS phase 1 CLI")
    parser.add_argument("--db", default=str(registry.DEFAULT_DB_PATH), help="SQLite registry path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    register_dataset = subparsers.add_parser("register-dataset")
    register_dataset.add_argument("--bars", required=True)
    register_dataset.add_argument("--symbol", required=True)
    register_dataset.add_argument("--timeframe", required=True)
    register_dataset.add_argument("--source-name", default="MT5")
    register_dataset.add_argument("--broker")
    register_dataset.add_argument("--server")
    register_dataset.add_argument("--timezone", default="broker_server")
    register_dataset.add_argument("--missing-data-policy", default="not_evaluated_phase1")
    register_dataset.add_argument("--cleaning-rules", default="raw_mt5_export_no_cleaning")
    register_dataset.set_defaults(func=cmd_register_dataset)

    validate_spec = subparsers.add_parser("validate-strategy-spec")
    validate_spec.add_argument("strategy_spec")
    validate_spec.set_defaults(func=cmd_validate_strategy_spec)

    create_experiment = subparsers.add_parser("create-experiment")
    create_experiment.add_argument("--strategy-spec", required=True)
    create_experiment.add_argument("--dataset-id", required=True)
    create_experiment.add_argument("--experiment-id")
    create_experiment.add_argument("--run-reason", default="manual", choices=["manual", "agent", "scheduled", "artifact_regeneration", "validation_only"])
    create_experiment.add_argument("--created-by", default="human")
    create_experiment.add_argument("--parent-experiment-id")
    create_experiment.add_argument("--rerun-of-experiment-id")
    create_experiment.add_argument("--change-type", default="baseline", choices=["baseline", "parameter_diff", "structural_diff", "validation", "rerun", "implementation_test"])
    create_experiment.add_argument("--change-summary", required=True)
    create_experiment.add_argument("--rationale", required=True)
    create_experiment.add_argument("--parameter-diff")
    create_experiment.add_argument("--structural-diff")
    create_experiment.set_defaults(func=cmd_create_experiment)

    attach_artifact = subparsers.add_parser("attach-artifact")
    attach_artifact.add_argument("experiment_id")
    attach_artifact.add_argument("--artifact-type", required=True)
    attach_artifact.add_argument("--path", required=True)
    attach_artifact.add_argument("--artifact-regenerated", action="store_true")
    attach_artifact.set_defaults(func=cmd_attach_artifact)

    attach_results = subparsers.add_parser("attach-result-artifacts")
    attach_results.add_argument("experiment_id")
    attach_results.add_argument("--report-dir", required=True)
    attach_results.set_defaults(func=cmd_attach_result_artifacts)

    validate = subparsers.add_parser("generate-validation-report")
    validate.add_argument("experiment_id")
    validate.add_argument("--output")
    validate.set_defaults(func=cmd_generate_validation_report)

    portfolio_report = subparsers.add_parser("generate-portfolio-report")
    portfolio_report.add_argument("experiment_id")
    portfolio_report.add_argument("--output")
    portfolio_report.set_defaults(func=cmd_generate_portfolio_report)

    list_experiments = subparsers.add_parser("list-experiments")
    list_experiments.set_defaults(func=cmd_list_experiments)

    validation_commands = subparsers.add_parser("validation")
    validation_subparsers = validation_commands.add_subparsers(dest="validation_command", required=True)

    validation_generate = validation_subparsers.add_parser("generate")
    validation_generate.add_argument("--experiment-id", required=True)
    validation_generate.add_argument("--output")
    validation_generate.set_defaults(func=cmd_validation_generate)

    validation_show = validation_subparsers.add_parser("show")
    validation_show.add_argument("--experiment-id", required=True)
    validation_show.set_defaults(func=cmd_validation_show)

    portfolio_commands = subparsers.add_parser("portfolio")
    portfolio_subparsers = portfolio_commands.add_subparsers(dest="portfolio_command", required=True)

    portfolio_validate = portfolio_subparsers.add_parser("validate-config")
    portfolio_validate.add_argument("--portfolio", required=True)
    portfolio_validate.set_defaults(func=cmd_portfolio_validate_config)

    portfolio_generate = portfolio_subparsers.add_parser("generate")
    portfolio_generate.add_argument("--portfolio", required=True)
    portfolio_generate.add_argument("--output")
    portfolio_generate.set_defaults(func=cmd_portfolio_generate)

    portfolio_show = portfolio_subparsers.add_parser("show")
    portfolio_show.add_argument("--portfolio", required=True)
    portfolio_show.add_argument("--output")
    portfolio_show.set_defaults(func=cmd_portfolio_show)

    portfolio_candidate = portfolio_subparsers.add_parser("evaluate-candidate")
    portfolio_candidate.add_argument("--portfolio", required=True)
    portfolio_candidate.add_argument("--candidate-experiment-id", required=True)
    portfolio_candidate.add_argument("--output")
    portfolio_candidate.set_defaults(func=cmd_portfolio_evaluate_candidate)

    experiment = subparsers.add_parser("experiment")
    experiment_subparsers = experiment.add_subparsers(dest="experiment_command", required=True)

    def add_prepare_args(command: argparse.ArgumentParser) -> None:
        command.add_argument("--strategy", required=True, help="Strategy YAML spec path")
        command.add_argument("--dataset-id")
        command.add_argument("--dataset-bundle-id")
        command.add_argument("--experiment-id")
        command.add_argument("--runner-run-id")
        command.add_argument("--output-root", default=str(experiment_runner.RESEARCH_RUNS_DIR))
        command.add_argument("--run-reason", default="manual", choices=["manual", "agent", "scheduled", "artifact_regeneration", "validation_only"])
        command.add_argument("--created-by", default="human")
        command.add_argument("--parent-experiment-id")
        command.add_argument("--rerun-of-experiment-id")
        command.add_argument("--change-type", default="baseline", choices=["baseline", "parameter_diff", "structural_diff", "validation", "rerun", "implementation_test"])
        command.add_argument("--change-summary")
        command.add_argument("--rationale")
        command.add_argument("--parameter-diff")
        command.add_argument("--structural-diff")

    prepare_run = experiment_subparsers.add_parser("prepare-run")
    add_prepare_args(prepare_run)
    prepare_run.set_defaults(func=cmd_experiment_prepare_run)

    attach_artifacts = experiment_subparsers.add_parser("attach-artifacts")
    attach_artifacts.add_argument("--experiment-id", required=True)
    attach_artifacts.add_argument("--output-dir", required=True, help="Existing runner output directory")
    attach_artifacts.add_argument("--research-output-dir")
    attach_artifacts.add_argument("--artifact-regenerated", action="store_true")
    attach_artifacts.add_argument("--no-validation", action="store_true", help="Attach artifacts without regenerating validation reports")
    attach_artifacts.set_defaults(func=cmd_experiment_attach_artifacts)

    run = experiment_subparsers.add_parser("run")
    add_prepare_args(run)
    run.add_argument("--runner-script", default=str(REPO_ROOT / "automated" / "scripts" / "run_backtest.sh"))
    run.set_defaults(func=cmd_experiment_run)

    experiment_metrics = experiment_subparsers.add_parser("metrics")
    experiment_metrics.add_argument("--experiment-id", required=True)
    experiment_metrics.set_defaults(func=cmd_experiment_metrics)

    show_runner_paths = experiment_subparsers.add_parser("show-runner-paths")
    show_runner_paths.add_argument("--output-dir", required=True, help="Experiment output directory containing run_context.json")
    show_runner_paths.set_defaults(func=cmd_experiment_show_runner_paths)

    lifecycle_commands = subparsers.add_parser("lifecycle")
    lifecycle_subparsers = lifecycle_commands.add_subparsers(dest="lifecycle_command", required=True)

    lifecycle_show = lifecycle_subparsers.add_parser("show")
    lifecycle_show.add_argument("--strategy", required=True)
    lifecycle_show.set_defaults(func=cmd_lifecycle_show)

    lifecycle_evaluate = lifecycle_subparsers.add_parser("evaluate")
    lifecycle_evaluate.add_argument("--strategy", required=True)
    lifecycle_evaluate.add_argument("--to-state", required=True)
    lifecycle_evaluate.add_argument("--experiment-id")
    lifecycle_evaluate.add_argument("--strictness", choices=sorted(lifecycle.STRICTNESS_MODES), default="normal")
    lifecycle_evaluate.add_argument("--override", action="store_true")
    lifecycle_evaluate.add_argument("--reason")
    lifecycle_evaluate.set_defaults(func=cmd_lifecycle_evaluate)

    lifecycle_propose = lifecycle_subparsers.add_parser("propose")
    lifecycle_propose.add_argument("--strategy", required=True)
    lifecycle_propose.add_argument("--to-state", required=True)
    lifecycle_propose.add_argument("--experiment-id")
    lifecycle_propose.add_argument("--reason", required=True)
    lifecycle_propose.add_argument("--requested-by", default="human")
    lifecycle_propose.add_argument("--approved-by")
    lifecycle_propose.add_argument("--strictness", choices=sorted(lifecycle.STRICTNESS_MODES), default="normal")
    lifecycle_propose.add_argument("--override", action="store_true")
    lifecycle_propose.add_argument("--notes")
    lifecycle_propose.add_argument("--snapshot-dir")
    lifecycle_propose.set_defaults(func=cmd_lifecycle_propose)

    lifecycle_apply = lifecycle_subparsers.add_parser("apply")
    lifecycle_apply.add_argument("--transition-id", required=True)
    lifecycle_apply.add_argument("--strictness", choices=sorted(lifecycle.STRICTNESS_MODES), default="normal")
    lifecycle_apply.set_defaults(func=cmd_lifecycle_apply)

    lifecycle_history = lifecycle_subparsers.add_parser("history")
    lifecycle_history.add_argument("--strategy", required=True)
    lifecycle_history.set_defaults(func=cmd_lifecycle_history)

    agent_commands = subparsers.add_parser("agent")
    agent_subparsers = agent_commands.add_subparsers(dest="agent_command", required=True)

    agent_validate = agent_subparsers.add_parser("validate-output")
    agent_validate.add_argument("--file", required=True)
    agent_validate.set_defaults(func=cmd_agent_validate_output)

    agent_attach = agent_subparsers.add_parser("attach-output")
    agent_attach.add_argument("--experiment-id", required=True)
    agent_attach.add_argument("--file", required=True)
    agent_attach.set_defaults(func=cmd_agent_attach_output)

    agent_permissions = agent_subparsers.add_parser("permissions")
    agent_permissions.add_argument("--role", required=True)
    agent_permissions.set_defaults(func=cmd_agent_permissions)

    agent_list = agent_subparsers.add_parser("list-contracts")
    agent_list.set_defaults(func=cmd_agent_list_contracts)

    sweep_commands = subparsers.add_parser("sweep")
    sweep_subparsers = sweep_commands.add_subparsers(dest="sweep_command", required=True)

    sweep_validate = sweep_subparsers.add_parser("validate-config")
    sweep_validate.add_argument("--config", required=True)
    sweep_validate.set_defaults(func=cmd_sweep_validate_config)

    sweep_prepare = sweep_subparsers.add_parser("prepare")
    sweep_prepare.add_argument("--config", required=True)
    sweep_prepare.add_argument("--output-root", default=str(experiment_runner.RESEARCH_RUNS_DIR))
    sweep_prepare.add_argument("--created-by", default="human")
    sweep_prepare.set_defaults(func=cmd_sweep_prepare)

    sweep_list = sweep_subparsers.add_parser("list")
    sweep_list.set_defaults(func=cmd_sweep_list)

    sweep_show = sweep_subparsers.add_parser("show")
    sweep_show.add_argument("--sweep-id", required=True)
    sweep_show.set_defaults(func=cmd_sweep_show)

    sweep_run = sweep_subparsers.add_parser("run")
    sweep_run.add_argument("--sweep-id", required=True)
    sweep_run.add_argument("--dry-run", action="store_true")
    sweep_run.add_argument("--limit", type=int)
    sweep_run.add_argument("--continue-on-error", action="store_true")
    sweep_run.add_argument("--runner-script", default=str(REPO_ROOT / "automated" / "scripts" / "run_backtest.sh"))
    sweep_run.add_argument("--output-root", default=str(experiment_runner.RESEARCH_RUNS_DIR))
    sweep_run.set_defaults(func=cmd_sweep_run)

    sweep_summary = sweep_subparsers.add_parser("summarize")
    sweep_summary.add_argument("--sweep-id", required=True)
    sweep_summary.add_argument("--output")
    sweep_summary.set_defaults(func=cmd_sweep_summarize)

    sweep_attach = sweep_subparsers.add_parser("attach-child-artifacts")
    sweep_attach.add_argument("--sweep-id", required=True)
    sweep_attach.add_argument("--child-experiment-id", required=True)
    sweep_attach.add_argument("--output-dir", required=True)
    sweep_attach.add_argument("--research-output-dir")
    sweep_attach.add_argument("--artifact-regenerated", action="store_true")
    sweep_attach.add_argument("--no-validation", action="store_true")
    sweep_attach.set_defaults(func=cmd_sweep_attach_child_artifacts)

    queue_commands = subparsers.add_parser("queue")
    queue_subparsers = queue_commands.add_subparsers(dest="queue_command", required=True)

    queue_validate = queue_subparsers.add_parser("validate")
    queue_validate.add_argument("--queue", required=True)
    queue_validate.set_defaults(func=cmd_queue_validate)

    queue_run = queue_subparsers.add_parser("run")
    queue_run.add_argument("--queue", required=True)
    queue_run.add_argument("--mode", default="overnight", choices=["overnight"])
    queue_run.add_argument("--dry-run", action="store_true")
    queue_run.add_argument("--runner-script", default=str(REPO_ROOT / "automated" / "scripts" / "run_backtest.sh"))
    queue_run.add_argument("--output-root", default=str(queue.QUEUE_RUN_ROOT))
    queue_run.set_defaults(func=cmd_queue_run)

    queue_report = queue_subparsers.add_parser("report")
    queue_report.add_argument("--run-id", required=True)
    queue_report.add_argument("--output-root", default=str(queue.QUEUE_RUN_ROOT))
    queue_report.set_defaults(func=cmd_queue_report)

    impl_commands = subparsers.add_parser("implementation")
    impl_subparsers = impl_commands.add_subparsers(dest="impl_command", required=True)

    impl_create = impl_subparsers.add_parser("create-request")
    impl_create.add_argument("--strategy-id", required=True)
    impl_create.add_argument("--strategy-version", required=True)
    impl_create.add_argument("--generated-files", required=True, help="Comma-separated list of file names")
    impl_create.add_argument("--hypothesis-id")
    impl_create.add_argument("--strategy-spec")
    impl_create.add_argument("--expected-inputs", help="JSON list of {name,type,required,default}")
    impl_create.add_argument("--parameters", help="JSON mapping of parameters")
    impl_create.add_argument("--entry-logic")
    impl_create.add_argument("--exit-logic")
    impl_create.add_argument("--risk-logic")
    impl_create.add_argument("--test-plan")
    impl_create.add_argument("--created-by", default="human")
    impl_create.set_defaults(func=cmd_impl_create_request)

    impl_validate = impl_subparsers.add_parser("validate-request")
    impl_validate.add_argument("implementation_request_id")
    impl_validate.set_defaults(func=cmd_impl_validate_request)

    impl_inspect = impl_subparsers.add_parser("inspect")
    impl_inspect.add_argument("implementation_request_id")
    impl_inspect.set_defaults(func=cmd_impl_inspect)

    impl_compile = impl_subparsers.add_parser("compile-check")
    impl_compile.add_argument("implementation_request_id")
    impl_compile.add_argument("--mock", action="store_true", help="Mock compile without MT5/Wine")
    impl_compile.add_argument("--real-compile-config", type=str, default=None, help="Path to real compile YAML config")
    impl_compile.set_defaults(func=cmd_impl_compile_check)

    impl_bt_readiness = impl_subparsers.add_parser("backtest-readiness")
    impl_bt_readiness.add_argument("implementation_request_id")
    impl_bt_readiness.add_argument("--real-backtest-readiness-config", required=True, help="Path to real backtest readiness YAML config")
    impl_bt_readiness.add_argument("--runner-conf-path", type=str, default=None, help="Override runner .conf path")
    impl_bt_readiness.add_argument("--set-file-path", type=str, default=None, help="Override .set file path")
    impl_bt_readiness.set_defaults(func=cmd_impl_backtest_readiness)

    impl_readiness_review = impl_subparsers.add_parser("readiness-review")
    impl_readiness_review.add_argument("implementation_request_id")
    impl_readiness_review.add_argument("--strategy-id", help="Strategy ID (optional, inferred from evidence)")
    impl_readiness_review.add_argument("--version", help="Strategy version (optional, inferred from evidence)")
    impl_readiness_review.add_argument("--compile-evidence", required=True, help="Path to compile evidence JSON file")
    impl_readiness_review.add_argument("--backtest-readiness-evidence", required=True, help="Path to backtest readiness evidence JSON file")
    impl_readiness_review.add_argument("--out", help="Output path for the review packet JSON (rejected if under automated/strategies/)")
    impl_readiness_review.set_defaults(func=cmd_impl_readiness_review)

    impl_tc = impl_subparsers.add_parser("real-toolchain-rehearsal")
    impl_tc.add_argument("implementation_request_id")
    impl_tc.add_argument("--real-compile-config", required=True, help="Path to real compile YAML config")
    impl_tc.add_argument("--real-backtest-readiness-config", required=True, help="Path to real backtest readiness YAML config")
    impl_tc.add_argument("--out-dir", required=True, help="Explicit output directory for evidence files (rejected if under automated/strategies/)")
    impl_tc.set_defaults(func=cmd_impl_real_toolchain_rehearsal)

    impl_review = impl_subparsers.add_parser("diff-review")
    impl_review.add_argument("implementation_request_id")
    impl_review.set_defaults(func=cmd_impl_diff_review)

    impl_approve = impl_subparsers.add_parser("approve-for-baseline")
    impl_approve.add_argument("implementation_request_id")
    impl_approve.add_argument("--approved-by", required=True)
    impl_approve.add_argument("--experiment-id", help="Optional baseline experiment ID")
    impl_approve.add_argument("--allow-mock-compile", action="store_true", help="Allow mock_checked compile status for approval")
    impl_approve.add_argument("--approval-scope", default="baseline_only", choices=["baseline_only"], help="Scope of approval")
    impl_approve.add_argument("--allow-reuse", action="store_true", help="Allow reusing the same approval for multiple baseline experiments")
    impl_approve.set_defaults(func=cmd_impl_approve_for_baseline)

    intake_commands = subparsers.add_parser("intake")
    intake_subparsers = intake_commands.add_subparsers(dest="intake_command", required=True)

    intake_gen_hyp = intake_subparsers.add_parser("generate-hypotheses")
    intake_gen_hyp.add_argument("--theme", required=True, help="Research theme description")
    intake_gen_hyp.add_argument("--symbol", required=True, help="Trading symbol")
    intake_gen_hyp.add_argument("--timeframe", required=True, help="Timeframe (e.g. H4)")
    intake_gen_hyp.add_argument("--market-regime", required=True, choices=["trending", "ranging"])
    intake_gen_hyp.add_argument("--strategy-family", required=True, choices=["mean_reversion", "breakout_continuation", "failed_breakout_reversal"])
    intake_gen_hyp.add_argument("--max-hypotheses", type=int, default=3)
    intake_gen_hyp.add_argument("--constraints", help="JSON constraints")
    intake_gen_hyp.add_argument("--output", help="Output directory for hypothesis set artifact")
    intake_gen_hyp.add_argument("--created-by", default="cli")
    intake_gen_hyp.set_defaults(func=cmd_intake_generate_hypotheses)

    intake_gen_spec = intake_subparsers.add_parser("generate-spec")
    intake_gen_spec.add_argument("--hypothesis-id", required=True)
    intake_gen_spec.add_argument("--strategy-id", required=True)
    intake_gen_spec.add_argument("--strategy-version", default="v1")
    intake_gen_spec.add_argument("--output", help="Output directory (default: automated/generated_specs/)")
    intake_gen_spec.add_argument("--created-by", default="cli")
    intake_gen_spec.set_defaults(func=cmd_intake_generate_spec)

    intake_mat = intake_subparsers.add_parser("materialize")
    intake_mat.add_argument("--strategy-id", required=True)
    intake_mat.add_argument("--strategy-version", default="v1")
    intake_mat.add_argument("--spec-path", required=True, help="Path to generated strategy spec")
    intake_mat.add_argument("--mock", action="store_true", help="Mock compile without MT5")
    intake_mat.add_argument("--created-by", default="cli")
    intake_mat.set_defaults(func=cmd_intake_materialize)

    intake_rp = intake_subparsers.add_parser("review-packet")
    intake_rp.add_argument("--strategy-id", required=True)
    intake_rp.add_argument("--strategy-version", default="v1")
    intake_rp.add_argument("--output", help="Output directory for review packet")
    intake_rp.set_defaults(func=cmd_intake_review_packet)

    gb_commands = subparsers.add_parser("generated-baseline")
    gb_subparsers = gb_commands.add_subparsers(dest="gb_command", required=True)

    gb_run = gb_subparsers.add_parser("run")
    gb_run.add_argument("--implementation-request-id", required=True, help="Implementation request ID")
    gb_run.add_argument("--strategy-id", required=True)
    gb_run.add_argument("--strategy-version", default="v1")
    gb_run.add_argument("--dataset-id", required=True)
    gb_run.add_argument("--spec-path", help="Override path to generated strategy spec")
    gb_run.add_argument("--experiment-id", help="Optional explicit experiment ID")
    gb_run.add_argument("--output-root", default=str(experiment_runner.RESEARCH_RUNS_DIR))
    gb_run.add_argument("--created-by", default="cli")
    gb_run.add_argument("--allow-mock-compile", action="store_true", help="Allow mock_checked compile")
    gb_run.add_argument("--allow-reuse", action="store_true", help="Allow reusing same approval")
    gb_run.add_argument("--run", action="store_true", help="Execute the prepared experiment via runner")
    gb_run.add_argument("--prepare-only", action="store_true", help="Only prepare without running")
    gb_run.add_argument("--runner-script", default=str(REPO_ROOT / "automated" / "scripts" / "run_backtest.sh"))
    gb_run.set_defaults(func=cmd_generated_baseline_run)

    gb_review = gb_subparsers.add_parser("review")
    gb_review.add_argument("--experiment-id", required=True)
    gb_review.add_argument("--strategy-id", required=True)
    gb_review.add_argument("--strategy-version", default="v1")
    gb_review.add_argument("--output", help="Output directory for review packet")
    gb_review.set_defaults(func=cmd_generated_baseline_review)

    gr_commands = subparsers.add_parser("generated-robustness")
    gr_subparsers = gr_commands.add_subparsers(dest="gr_command", required=True)

    gr_run = gr_subparsers.add_parser("run-sweep")
    gr_run.add_argument("--strategy-id", required=True)
    gr_run.add_argument("--strategy-version", default="v1")
    gr_run.add_argument("--implementation-request-id", required=True)
    gr_run.add_argument("--baseline-experiment-id", required=True)
    gr_run.add_argument("--params", required=True, help="JSON: {param_key: [values...]}")
    gr_run.add_argument("--child-cap", type=int, default=10, help="Max child experiments (<=12)")
    gr_run.add_argument("--run", action="store_true", help="Execute the prepared sweep via runner")
    gr_run.add_argument("--allow-mock-compile", action="store_true", help="Allow mock_checked compile")
    gr_run.add_argument("--output-root", default=str(queue.QUEUE_RUN_ROOT))
    gr_run.add_argument("--created-by", default="cli")
    gr_run.add_argument("--runner-script", default=str(REPO_ROOT / "automated" / "scripts" / "run_backtest.sh"))
    gr_run.set_defaults(func=cmd_generated_robustness_run_sweep)

    gr_review = gr_subparsers.add_parser("review")
    gr_review.add_argument("--sweep-id", required=True)
    gr_review.add_argument("--baseline-experiment-id", required=True)
    gr_review.add_argument("--strategy-id", required=True)
    gr_review.add_argument("--strategy-version", default="v1")
    gr_review.add_argument("--output", help="Output directory for review packet")
    gr_review.set_defaults(func=cmd_generated_robustness_review)

    gc_commands = subparsers.add_parser("generated-candidate")
    gc_subparsers = gc_commands.add_subparsers(dest="gc_command", required=True)

    gc_decision = gc_subparsers.add_parser("decision-packet")
    gc_decision.add_argument("--strategy-id", required=True)
    gc_decision.add_argument("--strategy-version", default="v1")
    gc_decision.add_argument("--implementation-request-id")
    gc_decision.add_argument("--baseline-experiment-id")
    gc_decision.add_argument("--robustness-sweep-id")
    gc_decision.add_argument("--output")
    gc_decision.set_defaults(func=cmd_generated_candidate_decision_packet)

    gfh_commands = subparsers.add_parser("generated-final-holdout")
    gfh_subparsers = gfh_commands.add_subparsers(dest="gfh_command", required=True)

    gfh_approve = gfh_subparsers.add_parser("approve")
    gfh_approve.add_argument("--implementation-request-id", required=True)
    gfh_approve.add_argument("--decision-packet-path", required=True)
    gfh_approve.add_argument("--approved-by", required=True)
    gfh_approve.add_argument("--allow-reuse", action="store_true", help="Allow reusing same approval")
    gfh_approve.set_defaults(func=cmd_generated_final_holdout_approve)

    gfh_run = gfh_subparsers.add_parser("run")
    gfh_run.add_argument("--implementation-request-id", required=True)
    gfh_run.add_argument("--strategy-id", required=True)
    gfh_run.add_argument("--strategy-version", default="v1")
    gfh_run.add_argument("--dataset-id", required=True)
    gfh_run.add_argument("--decision-packet-path", required=True)
    gfh_run.add_argument("--spec-path", help="Override path to generated strategy spec")
    gfh_run.add_argument("--experiment-id", help="Optional explicit experiment ID")
    gfh_run.add_argument("--output-root", default=str(experiment_runner.RESEARCH_RUNS_DIR))
    gfh_run.add_argument("--created-by", default="cli")
    gfh_run.add_argument("--allow-mock-compile", action="store_true", help="Allow mock_checked compile")
    gfh_run.add_argument("--allow-reuse", action="store_true", help="Allow reusing same approval")
    gfh_run.add_argument("--run", action="store_true", help="Execute the prepared experiment via runner")
    gfh_run.add_argument("--prepare-only", action="store_true", help="Only prepare without running")
    gfh_run.add_argument("--runner-script", default=str(REPO_ROOT / "automated" / "scripts" / "run_backtest.sh"))
    gfh_run.set_defaults(func=cmd_generated_final_holdout_run)

    gfh_review = gfh_subparsers.add_parser("review")
    gfh_review.add_argument("--experiment-id", required=True)
    gfh_review.add_argument("--strategy-id", required=True)
    gfh_review.add_argument("--strategy-version", default="v1")
    gfh_review.add_argument("--decision-packet-path")
    gfh_review.add_argument("--approval-id")
    gfh_review.add_argument("--output")
    gfh_review.set_defaults(func=cmd_generated_final_holdout_review)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
