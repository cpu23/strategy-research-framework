from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from automated.research import agents, datasets, lifecycle, portfolio, queue, registry, runner, sweeps, validation
from automated.research.contracts import EXPERIMENT_STATUSES
from automated.research.hashing import hash_parameter_set, hash_strategy_spec, stable_hash
from automated.research.metrics import extract_metrics
from automated.research.schemas import (
    REPO_ROOT,
    SchemaValidationError,
    load_yaml,
    validate_hypothesis,
    validate_strategy_spec,
)


class ResearchPhase1Tests(unittest.TestCase):
    def test_hypothesis_example_validates(self) -> None:
        data = load_yaml(REPO_ROOT / "hypotheses" / "HYP_FAILED_BREAKOUT_REVERSAL_001.yaml")
        self.assertEqual(validate_hypothesis(data)["hypothesis_id"], "HYP_FAILED_BREAKOUT_REVERSAL_001")

    def test_strategy_spec_example_validates(self) -> None:
        data = load_yaml(REPO_ROOT / "automated" / "specs" / "strategies" / "failed_breakout_reversal_v1.yaml")
        self.assertEqual(validate_strategy_spec(data)["strategy_id"], "failed_breakout_reversal_v1")

    def test_strategy_spec_rejects_missing_cost_documentation(self) -> None:
        data = load_yaml(REPO_ROOT / "automated" / "specs" / "strategies" / "failed_breakout_reversal_v1.yaml")
        data["costs"]["assumptions_documented"] = False
        with self.assertRaises(SchemaValidationError):
            validate_strategy_spec(data)

    def test_hashing_is_stable_for_mapping_order(self) -> None:
        left = {"b": 2, "a": {"y": 4, "x": 3}}
        right = {"a": {"x": 3, "y": 4}, "b": 2}
        self.assertEqual(stable_hash(left), stable_hash(right))

    def test_parameter_set_hash_ignores_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "a.set"
            second = Path(temp_dir) / "b.set"
            first.write_text("A=1\nB=true\n", encoding="utf-8")
            second.write_text("B=true\nA=1\n", encoding="utf-8")
            self.assertEqual(hash_parameter_set(first), hash_parameter_set(second))

    def test_strategy_hash_changes_when_research_contract_changes(self) -> None:
        spec = load_yaml(REPO_ROOT / "automated" / "specs" / "strategies" / "failed_breakout_reversal_v1.yaml")
        original = hash_strategy_spec(spec)
        spec["validation"]["min_trades_required"] = 99
        self.assertNotEqual(original, hash_strategy_spec(spec))

    def test_dataset_registration_from_bars_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            bars = Path(temp_dir) / "bars.csv"
            bars.write_text(
                "time\topen\thigh\tlow\tclose\ttick_volume\n"
                "2026.01.01 00:00:00\t1\t2\t0.5\t1.5\t10\n"
                "2026.01.01 04:00:00\t1.5\t2.5\t1\t2\t11\n",
                encoding="utf-8",
            )
            dataset = datasets.register_dataset(
                db_path,
                bars_path=bars,
                symbol="XAUUSD",
                timeframe="H4",
                broker="OANDA",
                server="demo",
            )
            self.assertEqual(dataset["row_count"], 2)
            stored = registry.get_dataset(db_path, dataset["dataset_id"])
            self.assertIsNotNone(stored)
            self.assertEqual(stored["file_hash"], dataset["file_hash"])

    def test_experiment_creation_and_validation_failure_without_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            bars = Path(temp_dir) / "bars.csv"
            bars.write_text(
                "time\topen\thigh\tlow\tclose\ttick_volume\n"
                "2026.01.01 00:00:00\t1\t2\t0.5\t1.5\t10\n",
                encoding="utf-8",
            )
            dataset = datasets.register_dataset(db_path, bars_path=bars, symbol="XAUUSD", timeframe="H4")
            payload = _experiment_payload(dataset)
            registry.create_experiment(db_path, payload)
            report = validation.build_validation_report(db_path, payload["experiment_id"])
            self.assertEqual(report["gate_status"], "fail")
            self.assertEqual(report["sections"]["artifact_checks"]["trade_log"]["status"], "fail")

    def test_validation_v2_warns_when_phase3_future_gates_are_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            bars = temp / "bars.csv"
            bars.write_text(
                "time\topen\thigh\tlow\tclose\ttick_volume\n"
                "2026.01.01 00:00:00\t1\t2\t0.5\t1.5\t10\n",
                encoding="utf-8",
            )
            dataset = datasets.register_dataset(db_path, bars_path=bars, symbol="XAUUSD", timeframe="H4")
            payload = _experiment_payload(dataset)
            registry.create_experiment(db_path, payload)
            trades = temp / "trades.csv"
            trades.write_text(
                "strategy_id\tsymbol\ttimeframe\tentry_time\texit_time\tdirection\tentry_price\texit_price\tstop_price\ttarget_price\tvolume\tprofit\tr_multiple\texit_reason\n"
                "s\tXAUUSD\tPERIOD_H4\t2026.01.01 04:00:00\t2026.01.01 08:00:00\tlong\t1\t2\t0\t3\t1\t10\t1\tclosed\n",
                encoding="utf-8",
            )
            equity = temp / "equity.csv"
            equity.write_text("time\tbalance\tequity\n2026.01.01 00:00:00\t100\t100\n", encoding="utf-8")
            summary = temp / "run_summary.json"
            summary.write_text(
                json.dumps(
                    {
                        "trades": 1,
                        "net_profit": 10,
                        "start_balance": 100,
                        "max_equity_drawdown_pct": 0,
                        "win_rate_pct": 100,
                        "expectancy": 10,
                        "profit_factor": 0,
                    }
                ),
                encoding="utf-8",
            )
            raw = temp / "terminal_run.log"
            raw.write_text("ok\n", encoding="utf-8")
            for artifact_type, path in [
                ("trade_log", trades),
                ("equity_curve", equity),
                ("metrics_json", summary),
                ("raw_backtest_output", raw),
            ]:
                registry.attach_artifact(db_path, payload["experiment_id"], artifact_type, path)
            report = validation.build_validation_report(db_path, payload["experiment_id"])
            self.assertEqual(report["gate_status"], "warn")
            self.assertIn("Walk-forward validation not implemented in phase 3", report["warnings"])
            self.assertEqual(report["schema_version"], "validation_report_v2")

    def test_single_strategy_portfolio_report_warns_without_correlation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            bars = Path(temp_dir) / "bars.csv"
            bars.write_text(
                "time\topen\thigh\tlow\tclose\ttick_volume\n"
                "2026.01.01 00:00:00\t1\t2\t0.5\t1.5\t10\n",
                encoding="utf-8",
            )
            dataset = datasets.register_dataset(db_path, bars_path=bars, symbol="XAUUSD", timeframe="H4")
            payload = _experiment_payload(dataset)
            registry.create_experiment(db_path, payload)
            report = portfolio.build_portfolio_report(db_path, payload["experiment_id"])
            self.assertEqual(report["status"], "warn")
            self.assertEqual(
                report["average_abs_correlation"][payload["experiment_id"]]["availability"],
                "not_available",
            )


class ResearchPhase2RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.created_runner_dirs: list[Path] = []

    def tearDown(self) -> None:
        for path in self.created_runner_dirs:
            shutil.rmtree(path, ignore_errors=True)

    def test_prepare_run_creates_experiment_and_run_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            dataset = _register_temp_dataset(db_path, temp)
            experiment_id = "EXP_TEST_PREPARE_RUN"
            context = runner.prepare_run(
                db_path=db_path,
                strategy_spec_path=REPO_ROOT / "automated" / "specs" / "strategies" / "failed_breakout_reversal_v1.yaml",
                dataset_id=dataset["dataset_id"],
                experiment_id=experiment_id,
                output_root=temp / "research_runs",
                change_summary="prepare test",
                rationale="test",
            )
            self.created_runner_dirs.append(Path(context["runner_output_dir"]))
            self.assertEqual(context["status"], "prepared")
            self.assertTrue((Path(context["output_dir"]) / "run_context.json").is_file())
            stored = registry.get_experiment(db_path, experiment_id)
            self.assertIsNotNone(stored)
            self.assertEqual(stored["status"], "prepared")

    def test_prepare_run_fails_if_strategy_spec_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            dataset = _register_temp_dataset(db_path, temp)
            spec = load_yaml(REPO_ROOT / "automated" / "specs" / "strategies" / "failed_breakout_reversal_v1.yaml")
            spec["costs"]["assumptions_documented"] = False
            invalid_spec = temp / "invalid.yaml"
            import yaml

            invalid_spec.write_text(yaml.safe_dump(spec), encoding="utf-8")
            with self.assertRaises(SchemaValidationError):
                runner.prepare_run(
                    db_path=db_path,
                    strategy_spec_path=invalid_spec,
                    dataset_id=dataset["dataset_id"],
                    experiment_id="EXP_TEST_INVALID_SPEC",
                    output_root=temp / "research_runs",
                )

    def test_prepare_run_fails_if_dataset_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                runner.prepare_run(
                    db_path=Path(temp_dir) / "registry.sqlite",
                    strategy_spec_path=REPO_ROOT / "automated" / "specs" / "strategies" / "failed_breakout_reversal_v1.yaml",
                    dataset_id="DATA_MISSING",
                    experiment_id="EXP_TEST_MISSING_DATASET",
                    output_root=Path(temp_dir) / "research_runs",
                )

    def test_attach_artifacts_hashes_files_and_generates_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            dataset = _register_temp_dataset(db_path, temp)
            experiment_id = "EXP_TEST_ATTACH_ARTIFACTS"
            context = runner.prepare_run(
                db_path=db_path,
                strategy_spec_path=REPO_ROOT / "automated" / "specs" / "strategies" / "failed_breakout_reversal_v1.yaml",
                dataset_id=dataset["dataset_id"],
                experiment_id=experiment_id,
                output_root=temp / "research_runs",
            )
            self.created_runner_dirs.append(Path(context["runner_output_dir"]))
            runner_output = temp / "runner_output"
            _write_runner_artifacts(runner_output, trades=31)
            manifest = runner.attach_runner_outputs(
                db_path=db_path,
                experiment_id=experiment_id,
                runner_output_dir=runner_output,
                research_output_dir=context["output_dir"],
            )
            self.assertTrue(manifest["required_artifacts_present"])
            self.assertTrue((Path(context["output_dir"]) / "artifact_manifest.json").is_file())
            self.assertTrue((Path(context["output_dir"]) / "reports" / "validation_report.json").is_file())
            stored = registry.get_experiment(db_path, experiment_id)
            self.assertEqual(stored["status"], "completed_with_warnings")
            artifacts = registry.list_artifacts(db_path, experiment_id)
            self.assertTrue(any(item["artifact_type"] == "trade_log" and item["file_hash"] for item in artifacts))
            metrics = registry.get_experiment_metrics(db_path, experiment_id)
            self.assertIsNotNone(metrics)
            self.assertEqual(metrics["trade_count"], 31)

    def test_missing_required_artifact_marks_experiment_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            dataset = _register_temp_dataset(db_path, temp)
            experiment_id = "EXP_TEST_MISSING_ARTIFACT"
            context = runner.prepare_run(
                db_path=db_path,
                strategy_spec_path=REPO_ROOT / "automated" / "specs" / "strategies" / "failed_breakout_reversal_v1.yaml",
                dataset_id=dataset["dataset_id"],
                experiment_id=experiment_id,
                output_root=temp / "research_runs",
            )
            self.created_runner_dirs.append(Path(context["runner_output_dir"]))
            runner_output = temp / "runner_output"
            runner_output.mkdir()
            (runner_output / "terminal_run.log").write_text("no csvs\n", encoding="utf-8")
            manifest = runner.attach_runner_outputs(
                db_path=db_path,
                experiment_id=experiment_id,
                runner_output_dir=runner_output,
                research_output_dir=context["output_dir"],
            )
            self.assertFalse(manifest["required_artifacts_present"])
            self.assertTrue(manifest["warnings"])
            stored = registry.get_experiment(db_path, experiment_id)
            self.assertEqual(stored["status"], "failed")

    def test_full_run_command_is_mockable_without_mt5(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            dataset = _register_temp_dataset(db_path, temp)
            experiment_id = "EXP_TEST_FAKE_RUNNER"
            context = runner.prepare_run(
                db_path=db_path,
                strategy_spec_path=REPO_ROOT / "automated" / "specs" / "strategies" / "failed_breakout_reversal_v1.yaml",
                dataset_id=dataset["dataset_id"],
                experiment_id=experiment_id,
                output_root=temp / "research_runs",
            )
            self.created_runner_dirs.append(Path(context["runner_output_dir"]))
            fake_runner = temp / "fake_runner.py"
            fake_runner.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, re, sys\n"
                f"repo = pathlib.Path({str(REPO_ROOT)!r})\n"
                "text = pathlib.Path(sys.argv[1]).read_text()\n"
                "run_id = re.search(r'RUN_ID=\"?([^\"\\n]+)', text).group(1)\n"
                "out = repo / 'automated' / 'reports' / run_id\n"
                "out.mkdir(parents=True, exist_ok=True)\n"
                "header = 'strategy_id\\tsymbol\\ttimeframe\\tentry_time\\texit_time\\tdirection\\tentry_price\\texit_price\\tstop_price\\ttarget_price\\tvolume\\tprofit\\tr_multiple\\texit_reason\\n'\n"
                "rows = ''.join(['s\\tXAUUSD\\tPERIOD_H4\\t2026.01.01 04:00:00\\t2026.01.01 08:00:00\\tlong\\t1\\t2\\t0\\t3\\t1\\t10\\t1\\tclosed\\n' for _ in range(31)])\n"
                "(out / 'trades.csv').write_text(header + rows)\n"
                "(out / 'equity.csv').write_text('time\\tbalance\\tequity\\n2026.01.01 00:00:00\\t100\\t100\\n')\n"
                "(out / 'bars.csv').write_text('time\\topen\\thigh\\tlow\\tclose\\ttick_volume\\n2026.01.01 00:00:00\\t1\\t2\\t0.5\\t1.5\\t10\\n')\n"
                "(out / 'run_summary.json').write_text('{\"trades\":31,\"net_profit\":10,\"start_balance\":100,\"max_equity_drawdown_pct\":0,\"win_rate_pct\":100,\"expectancy\":0.32,\"profit_factor\":2}')\n"
                "(out / 'terminal_run.log').write_text('fake ok\\n')\n",
                encoding="utf-8",
            )
            fake_runner.chmod(fake_runner.stat().st_mode | 0o111)
            result = runner.run_prepared_experiment(
                db_path=db_path,
                experiment_id=experiment_id,
                research_output_dir=context["output_dir"],
                runner_script=fake_runner,
            )
            self.assertEqual(result["returncode"], 0)
            stored = registry.get_experiment(db_path, experiment_id)
            self.assertEqual(stored["status"], "completed_with_warnings")


class ResearchPhase3ValidationTests(unittest.TestCase):
    def test_metrics_parser_extracts_trade_and_summary_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            _write_runner_artifacts(output, trades=3, profits=[100.0, -50.0, 25.0], directions=["long", "short", "long"])
            metrics = extract_metrics(
                trade_log_path=output / "trades.csv",
                equity_curve_path=output / "equity.csv",
                summary_path=output / "run_summary.json",
            )
            self.assertEqual(metrics["total_trades"]["value"], 3)
            self.assertEqual(metrics["long_trade_count"]["value"], 2)
            self.assertEqual(metrics["short_trade_count"]["value"], 1)
            self.assertEqual(metrics["median_trade"]["value"], 25.0)
            self.assertEqual(metrics["costs"]["availability"], "unavailable")

    def test_missing_metric_availability_is_explicit(self) -> None:
        metrics = extract_metrics()
        self.assertEqual(metrics["profit_factor"]["availability"], "unavailable")
        self.assertIn("artifact", metrics["profit_factor"]["reason"])

    def test_validation_v2_concentration_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            dataset = _register_temp_dataset(db_path, temp)
            experiment_id = "EXP_TEST_CONCENTRATION"
            context = runner.prepare_run(
                db_path=db_path,
                strategy_spec_path=REPO_ROOT / "automated" / "specs" / "strategies" / "failed_breakout_reversal_v1.yaml",
                dataset_id=dataset["dataset_id"],
                experiment_id=experiment_id,
                output_root=temp / "research_runs",
            )
            runner_output = temp / "runner_output"
            _write_runner_artifacts(
                runner_output,
                trades=31,
                profits=[1000.0] + [1.0 for _ in range(30)],
                directions=["long", "short"] * 15 + ["long"],
            )
            runner.attach_runner_outputs(
                db_path=db_path,
                experiment_id=experiment_id,
                runner_output_dir=runner_output,
                research_output_dir=context["output_dir"],
            )
            report = validation.build_validation_report(db_path, experiment_id)
            self.assertEqual(report["sections"]["concentration_gate"]["status"], "warn")

    def test_cost_and_execution_gates_pass_from_strategy_spec(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            dataset = _register_temp_dataset(db_path, temp)
            payload = _experiment_payload(dataset)
            registry.create_experiment(db_path, payload)
            runner_output = temp / "runner_output"
            _write_runner_artifacts(runner_output, trades=31)
            for artifact_type, filename in [
                ("trade_log", "trades.csv"),
                ("equity_curve", "equity.csv"),
                ("metrics_json", "run_summary.json"),
                ("raw_backtest_output", "terminal_run.log"),
            ]:
                registry.attach_artifact(db_path, payload["experiment_id"], artifact_type, runner_output / filename)
            report = validation.build_validation_report(db_path, payload["experiment_id"])
            self.assertEqual(report["sections"]["cost_assumption_gate"]["status"], "pass")
            self.assertEqual(report["sections"]["execution_assumption_gate"]["status"], "pass")


class ResearchPhase4PortfolioTests(unittest.TestCase):
    def test_equity_csv_parser_documents_current_mt5_format(self) -> None:
        info = portfolio.inspect_equity_csv(REPO_ROOT / "automated" / "reports" / "fbr_xauusd_h4_baseline" / "equity.csv")
        self.assertEqual(info["timestamp_column"], "time")
        self.assertEqual(info["equity_or_balance_column"], "equity")
        self.assertEqual(info["value_semantics"], "absolute_account_value")
        self.assertEqual(info["timezone"], "not_inferable_from_equity_csv")

    def test_return_stream_extracts_daily_pct_returns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            equity = Path(temp_dir) / "equity.csv"
            equity.write_text(
                "time\tbalance\tequity\n"
                "2026.01.01 00:00:00\t100\t100\n"
                "2026.01.01 12:00:00\t101\t101\n"
                "2026.01.02 00:00:00\t102\t102\n",
                encoding="utf-8",
            )
            stream = portfolio.extract_return_stream(experiment_id="EXP_STREAM", equity_path=equity)
            self.assertEqual(stream.status, "available")
            self.assertEqual(stream.metadata["return_type"], "pct_return")
            self.assertAlmostEqual(stream.returns["2026-01-01"], 0.01)
            self.assertAlmostEqual(stream.returns["2026-01-02"], 1 / 101)

    def test_portfolio_config_schema_validation(self) -> None:
        config = portfolio.validate_portfolio_config(
            {
                "portfolio_id": "p",
                "name": "P",
                "experiments": ["EXP_A"],
                "frequency": "daily",
            }
        )
        self.assertEqual(config["correlation"]["tail_threshold_quantile"], 0.20)
        with self.assertRaises(SchemaValidationError):
            portfolio.validate_portfolio_config({"portfolio_id": "bad", "name": "Bad", "experiments": []})

    def test_correlation_matrix_with_synthetic_return_streams(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            _create_experiment_with_equity(db_path, temp, "EXP_A", [100, 101, 102, 103, 104])
            _create_experiment_with_equity(db_path, temp, "EXP_B", [100, 101, 102, 103, 104])
            report = portfolio.build_portfolio_report(db_path, portfolio_config=_portfolio_config(["EXP_A", "EXP_B"]))
            corr = report["correlation_matrix"]["EXP_A"]["EXP_B"]["value"]
            self.assertAlmostEqual(corr, 1.0)

    def test_one_strategy_portfolio_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            _create_experiment_with_equity(db_path, temp, "EXP_ONLY", [100, 101, 100, 102])
            report = portfolio.build_portfolio_report(db_path, portfolio_config=_portfolio_config(["EXP_ONLY"]))
            self.assertEqual(report["status"], "warn")
            self.assertIn("at least two", report["average_abs_correlation"]["EXP_ONLY"]["reason"])

    def test_tail_correlation_calculation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            _create_experiment_with_equity(db_path, temp, "EXP_A", [100, 99, 98, 97, 98, 97, 96])
            _create_experiment_with_equity(db_path, temp, "EXP_B", [100, 99, 98, 97, 98, 97, 96])
            report = portfolio.build_portfolio_report(db_path, portfolio_config=_portfolio_config(["EXP_A", "EXP_B"]))
            item = report["tail_correlation"]["EXP_A"]["correlation_when_portfolio_down"]
            self.assertEqual(item["availability"], "available")
            self.assertAlmostEqual(item["value"], 1.0)

    def test_drawdown_overlap_calculation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            _create_experiment_with_equity(db_path, temp, "EXP_A", [100, 99, 98, 101, 100])
            _create_experiment_with_equity(db_path, temp, "EXP_B", [100, 99, 98, 101, 100])
            report = portfolio.build_portfolio_report(db_path, portfolio_config=_portfolio_config(["EXP_A", "EXP_B"]))
            self.assertEqual(report["drawdown_overlap"]["EXP_A"]["availability"], "available")
            self.assertAlmostEqual(report["drawdown_overlap"]["EXP_A"]["value"], 1.0)

    def test_unavailable_equity_artifact_handling(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            dataset = _register_temp_dataset(db_path, temp)
            payload = _experiment_payload(dataset)
            payload["experiment_id"] = "EXP_NO_EQUITY"
            registry.create_experiment(db_path, payload)
            report = portfolio.build_portfolio_report(db_path, portfolio_config=_portfolio_config(["EXP_NO_EQUITY"]))
            self.assertEqual(report["data_availability"]["EXP_NO_EQUITY"]["status"], "not_available")
            self.assertIn("equity_curve", report["data_availability"]["EXP_NO_EQUITY"]["reason"])

    def test_portfolio_report_generation_and_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            _create_experiment_with_equity(db_path, temp, "EXP_A", [100, 101, 102, 103])
            _create_experiment_with_equity(db_path, temp, "EXP_B", [100, 100.5, 101, 101.5])
            config_path = temp / "portfolio.yaml"
            config_path.write_text(
                "portfolio_id: test_portfolio\n"
                "name: Test Portfolio\n"
                "experiments:\n"
                "  - EXP_A\n"
                "  - EXP_B\n"
                "frequency: daily\n",
                encoding="utf-8",
            )
            output = temp / "portfolio_report.json"
            report = portfolio.write_configured_portfolio_report(db_path, config_path, output)
            self.assertTrue(output.is_file())
            self.assertEqual(report["portfolio_id"], "test_portfolio")
            from automated.research import cli

            with redirect_stdout(StringIO()):
                self.assertEqual(cli.main(["--db", str(db_path), "portfolio", "validate-config", "--portfolio", str(config_path)]), 0)


class ResearchPhase5And6Tests(unittest.TestCase):
    def test_lifecycle_state_schema_rejects_unknown_state(self) -> None:
        data = load_yaml(REPO_ROOT / "automated" / "specs" / "strategies" / "failed_breakout_reversal_v1.yaml")
        data["lifecycle"]["state"] = "moonshot"
        with self.assertRaises(SchemaValidationError):
            validate_strategy_spec(data)

    def test_lifecycle_allowed_and_blocked_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            spec_path = _temp_strategy_spec(temp, lifecycle_state="baseline_testing")
            allowed = lifecycle.evaluate_transition(db_path, strategy=spec_path, to_state="robustness_testing", strictness="lenient")
            blocked = lifecycle.evaluate_transition(db_path, strategy=spec_path, to_state="production", strictness="lenient")
            self.assertNotEqual(allowed["requirements"][0]["status"], "block")
            self.assertEqual(blocked["status"], "blocked")

    def test_lifecycle_evaluate_does_not_mutate_strategy_spec(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            spec_path = _temp_strategy_spec(temp, lifecycle_state="stat_review")
            before = spec_path.read_text(encoding="utf-8")
            lifecycle.evaluate_transition(db_path, strategy=spec_path, to_state="portfolio_review", strictness="lenient")
            self.assertEqual(spec_path.read_text(encoding="utf-8"), before)

    def test_lifecycle_propose_creates_transition_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            spec_path = _temp_strategy_spec(temp, lifecycle_state="baseline_testing")
            result = lifecycle.propose_transition(
                db_path,
                strategy=spec_path,
                to_state="robustness_testing",
                experiment_id=None,
                reason="Advance to robustness queue.",
                snapshot_dir=temp / "snapshots",
            )
            stored = registry.get_lifecycle_transition(db_path, result["transition"]["transition_id"])
            self.assertIsNotNone(stored)
            self.assertEqual(stored["status"], "proposed")
            self.assertTrue(Path(stored["gate_snapshot_path"]).is_file())

    def test_lifecycle_apply_updates_state_only_when_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            spec_path = _temp_strategy_spec(temp, lifecycle_state="baseline_testing")
            result = lifecycle.propose_transition(
                db_path,
                strategy=spec_path,
                to_state="archived",
                experiment_id=None,
                reason="Archive inactive research line.",
                snapshot_dir=temp / "snapshots",
            )
            applied = lifecycle.apply_transition(db_path, transition_id=result["transition"]["transition_id"], strictness="lenient")
            self.assertEqual(applied["status"], "applied")
            self.assertEqual(load_yaml(spec_path)["lifecycle"]["state"], "archived")

            blocked_result = lifecycle.propose_transition(
                db_path,
                strategy=spec_path,
                to_state="production",
                experiment_id=None,
                reason="Invalid jump.",
                snapshot_dir=temp / "snapshots",
            )
            blocked = lifecycle.apply_transition(db_path, transition_id=blocked_result["transition"]["transition_id"], strictness="lenient")
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(load_yaml(spec_path)["lifecycle"]["state"], "archived")

    def test_lifecycle_strictness_modes_for_missing_advanced_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            spec_path = _temp_strategy_spec(temp, lifecycle_state="stat_review")
            experiment_id = "EXP_STRICTNESS"
            dataset = _register_temp_dataset(db_path, temp)
            payload = _experiment_payload(dataset)
            payload["experiment_id"] = experiment_id
            registry.create_experiment(db_path, payload)
            _write_json(temp / "validation_report.json", {"gate_status": "pass", "hard_failures": [], "sections": {}})
            registry.attach_artifact(db_path, experiment_id, "validation_report", temp / "validation_report.json")
            lenient = lifecycle.evaluate_transition(
                db_path,
                strategy=spec_path,
                to_state="portfolio_review",
                experiment_id=experiment_id,
                strictness="lenient",
            )
            strict = lifecycle.evaluate_transition(
                db_path,
                strategy=spec_path,
                to_state="portfolio_review",
                experiment_id=experiment_id,
                strictness="strict",
            )
            self.assertEqual(lenient["status"], "warn")
            self.assertEqual(strict["status"], "blocked")

    def test_strict_blocks_not_implemented_robustness_and_walk_forward(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            spec_path = _temp_strategy_spec(temp, lifecycle_state="robustness_testing")
            experiment_id = "EXP_STRICT_NOT_IMPLEMENTED"
            dataset = _register_temp_dataset(db_path, temp)
            payload = _experiment_payload(dataset)
            payload["experiment_id"] = experiment_id
            registry.create_experiment(db_path, payload)
            _write_json(
                temp / "validation_report.json",
                {
                    "gate_status": "warn",
                    "hard_failures": [],
                    "sections": {
                        "placeholder_advanced_gates": {
                            "parameter_robustness": {"status": "not_implemented"},
                            "walk_forward": {"status": "not_implemented"},
                        }
                    },
                },
            )
            registry.attach_artifact(db_path, experiment_id, "validation_report", temp / "validation_report.json")
            normal = lifecycle.evaluate_transition(
                db_path,
                strategy=spec_path,
                to_state="stat_review",
                experiment_id=experiment_id,
                strictness="normal",
            )
            strict = lifecycle.evaluate_transition(
                db_path,
                strategy=spec_path,
                to_state="stat_review",
                experiment_id=experiment_id,
                strictness="strict",
            )
            self.assertEqual(normal["status"], "warn")
            self.assertEqual(strict["status"], "blocked")
            self.assertTrue(any(item["requirement"] == "walk_forward_implemented" for item in strict["blockers"]))

    def test_missing_review_artifacts_warn_or_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            spec_path = _temp_strategy_spec(temp, lifecycle_state="portfolio_review")
            normal = lifecycle.evaluate_transition(db_path, strategy=spec_path, to_state="paper_trading", strictness="normal")
            lenient = lifecycle.evaluate_transition(db_path, strategy=spec_path, to_state="paper_trading", strictness="lenient")
            self.assertEqual(normal["status"], "blocked")
            self.assertEqual(lenient["status"], "warn")

    def test_red_team_rejection_blocks_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            experiment_id = "EXP_RED_TEAM"
            dataset = _register_temp_dataset(db_path, temp)
            payload = _experiment_payload(dataset)
            payload["experiment_id"] = experiment_id
            payload["status"] = "completed"
            registry.create_experiment(db_path, payload)
            spec_path = _temp_strategy_spec(temp, lifecycle_state="portfolio_review")
            _write_json(temp / "portfolio_report.json", {"status": "pass", "portfolio_id": "p"})
            registry.attach_artifact(db_path, experiment_id, "portfolio_report", temp / "portfolio_report.json")
            red_team = temp / "red_team.yaml"
            red_team.write_text(
                "agent_role: red_team_reviewer\n"
                "experiment_id: EXP_RED_TEAM\n"
                "possible_lookahead: none found\n"
                "survivorship_concerns: none\n"
                "hidden_beta_trend_vol_exposure: material\n"
                "overfit_filter_concerns: high\n"
                "single_trade_dependence: low\n"
                "adjacent_parameter_failure: unknown\n"
                "rejection_reasons:\n"
                "  - Hidden beta exposure unresolved.\n"
                "decision: reject\n"
                "required_followups:\n"
                "  - Decompose beta exposure.\n",
                encoding="utf-8",
            )
            agents.attach_output(db_path, experiment_id=experiment_id, path=red_team)
            result = lifecycle.evaluate_transition(
                db_path,
                strategy=spec_path,
                to_state="paper_trading",
                experiment_id=experiment_id,
                strictness="normal",
            )
            self.assertEqual(result["status"], "blocked")
            self.assertTrue(any(item["requirement"] == "no_red_team_rejection" for item in result["blockers"]))

    def test_agent_contract_loading_and_permissions(self) -> None:
        contracts = agents.list_contracts()
        self.assertTrue(any(item["role_name"] == "red_team_reviewer" for item in contracts))
        permissions = agents.permissions_for_role("red_team_reviewer")
        self.assertFalse(permissions["can_modify_mql5"])

    def test_agent_output_schema_validation_and_attach(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            experiment_id = "EXP_AGENT_ATTACH"
            dataset = _register_temp_dataset(db_path, temp)
            payload = _experiment_payload(dataset)
            payload["experiment_id"] = experiment_id
            registry.create_experiment(db_path, payload)
            review = temp / "stat_review.yaml"
            review.write_text(
                "agent_role: statistical_reviewer\n"
                "experiment_id: EXP_AGENT_ATTACH\n"
                "sample_size_assessment: acceptable\n"
                "number_of_trials: 1\n"
                "multiple_testing_risk: low\n"
                "non_normal_return_concerns: acknowledged\n"
                "overlapping_trade_concerns: none\n"
                "train_test_leakage_concerns: none found\n"
                "decision: pass\n"
                "required_followups: []\n",
                encoding="utf-8",
            )
            self.assertEqual(agents.validate_output_file(review)["artifact_type"], "statistical_review")
            attached = agents.attach_output(db_path, experiment_id=experiment_id, path=review)
            self.assertEqual(attached["artifact_type"], "statistical_review")
            artifacts = registry.list_artifacts(db_path, experiment_id)
            self.assertTrue(any(item["artifact_type"] == "statistical_review" for item in artifacts))

    def test_attached_statistical_review_permits_portfolio_review_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            experiment_id = "EXP_STAT_REVIEW_ATTACHED"
            dataset = _register_temp_dataset(db_path, temp)
            payload = _experiment_payload(dataset)
            payload["experiment_id"] = experiment_id
            registry.create_experiment(db_path, payload)
            spec_path = _temp_strategy_spec(temp, lifecycle_state="stat_review")
            _write_json(temp / "validation_report.json", {"gate_status": "pass", "hard_failures": [], "sections": {}})
            registry.attach_artifact(db_path, experiment_id, "validation_report", temp / "validation_report.json")
            review = temp / "stat_review.yaml"
            _write_statistical_review(review, experiment_id)
            agents.attach_output(db_path, experiment_id=experiment_id, path=review)
            result = lifecycle.evaluate_transition(
                db_path,
                strategy=spec_path,
                to_state="portfolio_review",
                experiment_id=experiment_id,
                strictness="normal",
            )
            self.assertEqual(result["status"], "pass")

    def test_forbidden_mql5_modification_permission_check(self) -> None:
        result = agents.check_permission(
            role_name="strategy_spec_agent",
            action="edit_mql5",
            files=["automated/strategies/example/Example.mq5"],
        )
        self.assertEqual(result["status"], "denied")
        self.assertTrue(any(".mq5" in blocker or "MQL5" in blocker for blocker in result["blockers"]))

    def test_implementation_task_schema(self) -> None:
        result = agents.validate_output(
            {
                "implementation_task_id": "IMPL_TEST",
                "requested_by": "human",
                "reason": "Need code implementation after structural review.",
                "files_to_change": ["automated/strategies/example/Example.mq5"],
                "expected_behavior_change": "Add one approved entry condition.",
                "tests_required": ["unit tests", "mocked backtest wrapper test"],
                "human_approved": False,
                "created_at": "2026-05-11T00:00:00+00:00",
                "status": "proposed",
            }
        )
        self.assertEqual(result["artifact_type"], "implementation_task")

    def test_lifecycle_cli_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            spec_path = _temp_strategy_spec(temp, lifecycle_state="baseline_testing")
            from automated.research import cli

            with redirect_stdout(StringIO()):
                self.assertEqual(
                    cli.main(["--db", str(db_path), "lifecycle", "show", "--strategy", str(spec_path)]),
                    0,
                )

    def test_invalid_artifact_type_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            dataset = _register_temp_dataset(db_path, temp)
            payload = _experiment_payload(dataset)
            registry.create_experiment(db_path, payload)
            artifact = temp / "artifact.txt"
            artifact.write_text("x\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                registry.attach_artifact(db_path, payload["experiment_id"], "mystery_artifact", artifact)

    def test_invalid_experiment_status_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            dataset = _register_temp_dataset(db_path, temp)
            payload = _experiment_payload(dataset)
            payload["status"] = "almost_done"
            with self.assertRaises(ValueError):
                registry.create_experiment(db_path, payload)
            self.assertIn("planned", EXPERIMENT_STATUSES)

    def test_schema_version_exists_after_init(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            registry.init_db(db_path)
            version = registry.get_schema_version(db_path)
            self.assertIsNotNone(version)
            self.assertGreaterEqual(version["version"], 2)

    def test_repo_owned_artifact_paths_are_stored_relative(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            dataset = _register_temp_dataset(db_path, temp)
            payload = _experiment_payload(dataset)
            registry.create_experiment(db_path, payload)
            artifact = REPO_ROOT / "automated" / "reports" / "fbr_xauusd_h4_baseline" / "run_summary.json"
            registry.attach_artifact(db_path, payload["experiment_id"], "metrics_json", artifact)
            stored = registry.list_artifacts(db_path, payload["experiment_id"])[0]
            self.assertFalse(Path(stored["path"]).is_absolute())
            self.assertNotIn("/home/mrw/", stored["path"])

    def test_cli_happy_path_smoke_with_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            dataset = _register_temp_dataset(db_path, temp)
            experiment_id = "EXP_CLI_HAPPY_PATH"
            output_root = temp / "research_runs"
            runner_output = temp / "runner_output"
            _write_runner_artifacts(runner_output, trades=31)
            red_team = temp / "red_team_pass.yaml"
            _write_red_team_review(red_team, experiment_id, decision="pass")
            spec_path = _temp_strategy_spec(temp, lifecycle_state="portfolio_review")
            from automated.research import cli

            commands = [
                ["--db", str(db_path), "validate-strategy-spec", str(REPO_ROOT / "automated" / "specs" / "strategies" / "failed_breakout_reversal_v1.yaml")],
                [
                    "--db",
                    str(db_path),
                    "experiment",
                    "prepare-run",
                    "--strategy",
                    str(REPO_ROOT / "automated" / "specs" / "strategies" / "failed_breakout_reversal_v1.yaml"),
                    "--dataset-id",
                    dataset["dataset_id"],
                    "--experiment-id",
                    experiment_id,
                    "--output-root",
                    str(output_root),
                ],
                [
                    "--db",
                    str(db_path),
                    "experiment",
                    "attach-artifacts",
                    "--experiment-id",
                    experiment_id,
                    "--output-dir",
                    str(runner_output),
                    "--research-output-dir",
                    str(output_root / experiment_id),
                ],
                ["--db", str(db_path), "validation", "generate", "--experiment-id", experiment_id, "--output", str(temp / "validation_report_again.json")],
                ["--db", str(db_path), "generate-portfolio-report", experiment_id, "--output", str(temp / "portfolio_report.json")],
                ["--db", str(db_path), "experiment", "metrics", "--experiment-id", experiment_id],
                ["--db", str(db_path), "agent", "attach-output", "--experiment-id", experiment_id, "--file", str(red_team)],
                [
                    "--db",
                    str(db_path),
                    "lifecycle",
                    "evaluate",
                    "--strategy",
                    str(spec_path),
                    "--to-state",
                    "paper_trading",
                    "--experiment-id",
                    experiment_id,
                    "--strictness",
                    "lenient",
                ],
                [
                    "--db",
                    str(db_path),
                    "lifecycle",
                    "propose",
                    "--strategy",
                    str(spec_path),
                    "--to-state",
                    "paper_trading",
                    "--experiment-id",
                    experiment_id,
                    "--strictness",
                    "lenient",
                    "--reason",
                    "CLI happy-path smoke proposal.",
                    "--snapshot-dir",
                    str(temp / "lifecycle_snapshots"),
                ],
                ["--db", str(db_path), "lifecycle", "history", "--strategy", str(spec_path)],
            ]
            for command in commands:
                with redirect_stdout(StringIO()):
                    self.assertEqual(cli.main(command), 0, command)


class ResearchPhase7SweepTests(unittest.TestCase):
    def test_sweep_config_validation_and_invalid_type(self) -> None:
        config = {
            "sweep_type": "parameter_robustness",
            "parent_experiment_id": "EXP_PARENT",
            "budget": {"max_child_experiments": 3, "max_parameters_changed_per_child": 1},
            "parameters": {"InpRangeLookback": {"values": [20, 30]}},
        }
        self.assertEqual(sweeps.validate_sweep_config(config)["sweep_type"], "parameter_robustness")
        bad = dict(config)
        bad["sweep_type"] = "anything_goes"
        with self.assertRaises(SchemaValidationError):
            sweeps.validate_sweep_config(bad)

    def test_one_variable_planning_and_budget_enforcement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_SWEEP_PARENT")
            config = _sweep_config(parent["experiment_id"], values=[20, 30, 40])
            plan = sweeps.plan_sweep(db_path, config)
            self.assertEqual(len(plan["children"]), 2)
            self.assertTrue(all(len(child["parameter_diff"]) == 1 for child in plan["children"]))
            config["budget"]["max_child_experiments"] = 1
            with self.assertRaises(SchemaValidationError):
                sweeps.plan_sweep(db_path, config)

    def test_grid_mode_budget_failure_and_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_GRID_PARENT")
            config = {
                "sweep_type": "parameter_robustness",
                "parent_experiment_id": parent["experiment_id"],
                "budget": {"max_child_experiments": 10, "max_parameters_changed_per_child": 2},
                "mode": "grid",
                "parameters": {
                    "InpRangeLookback": {"values": [20, 30]},
                    "InpAdverseAtrMultiplier": {"values": [1.2, 1.5]},
                },
            }
            plan = sweeps.plan_sweep(db_path, config)
            self.assertTrue(plan["warnings"])
            config["budget"]["max_child_experiments"] = 1
            with self.assertRaises(SchemaValidationError):
                sweeps.plan_sweep(db_path, config)

    def test_prepare_creates_child_context_and_materialized_files_without_touching_originals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_PREPARE_SWEEP_PARENT")
            config_path = temp / "sweep.yaml"
            _write_sweep_config(config_path, parent["experiment_id"], values=[20, 30])
            original_set = (REPO_ROOT / "automated" / "runs" / "sets" / "fbr_xauusd_h4_baseline.set").read_text(encoding="utf-8")
            result = sweeps.prepare_sweep(db_path, config_path=config_path, output_root=temp / "research_runs")
            children = registry.list_sweep_children(db_path, result["sweep_id"])
            self.assertEqual(len(children), 1)
            child_id = children[0]["child_experiment_id"]
            context_path = temp / "research_runs" / child_id / "run_context.json"
            self.assertTrue(context_path.is_file())
            child_set = temp / "research_runs" / child_id / "raw" / "parameters.set"
            self.assertIn("InpRangeLookback=30", child_set.read_text(encoding="utf-8"))
            self.assertEqual((REPO_ROOT / "automated" / "runs" / "sets" / "fbr_xauusd_h4_baseline.set").read_text(encoding="utf-8"), original_set)
            self.assertIsNotNone(registry.get_sweep(db_path, result["sweep_id"]))

    def test_missing_parameter_key_fails_materialization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_MISSING_KEY_PARENT")
            config = _sweep_config(parent["experiment_id"], values=[1])
            config["parameters"] = {"NotARealInput": {"values": [1]}}
            with self.assertRaises(SchemaValidationError):
                sweeps.plan_sweep(db_path, config)

    def test_sweep_dry_run_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_DRY_RUN_PARENT")
            config_path = temp / "sweep.yaml"
            _write_sweep_config(config_path, parent["experiment_id"], values=[20, 30])
            result = sweeps.prepare_sweep(db_path, config_path=config_path, output_root=temp / "research_runs")
            dry = sweeps.run_sweep(db_path, sweep_id=result["sweep_id"], dry_run=True, output_root=temp / "research_runs")
            self.assertTrue(dry["dry_run"])
            self.assertEqual(len(dry["children_to_run"]), 1)

    def test_mocked_child_run_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_RUN_SWEEP_PARENT")
            config_path = temp / "sweep.yaml"
            _write_sweep_config(config_path, parent["experiment_id"], values=[20, 30])
            result = sweeps.prepare_sweep(db_path, config_path=config_path, output_root=temp / "research_runs")
            child_id = registry.list_sweep_children(db_path, result["sweep_id"])[0]["child_experiment_id"]
            fake_runner = _write_fake_runner(temp)
            try:
                run_result = sweeps.run_sweep(
                    db_path,
                    sweep_id=result["sweep_id"],
                    runner_script=fake_runner,
                    output_root=temp / "research_runs",
                )
                self.assertEqual(run_result["status"], "completed")
                self.assertIsNotNone(registry.get_experiment_metrics(db_path, child_id))
            finally:
                shutil.rmtree(REPO_ROOT / "automated" / "reports" / child_id, ignore_errors=True)

    def test_sweep_summary_and_plateau_score_from_synthetic_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_SUMMARY_PARENT")
            config_path = temp / "sweep.yaml"
            _write_sweep_config(config_path, parent["experiment_id"], values=[20, 30, 40])
            result = sweeps.prepare_sweep(db_path, config_path=config_path, output_root=temp / "research_runs")
            registry.upsert_experiment_metrics(db_path, parent["experiment_id"], _metrics(net_return=0.10, profit_factor=1.5))
            for index, child in enumerate(registry.list_sweep_children(db_path, result["sweep_id"])):
                registry.upsert_experiment_metrics(
                    db_path,
                    child["child_experiment_id"],
                    _metrics(net_return=0.05 + index * 0.01, profit_factor=1.2),
                )
            summary = sweeps.summarize_sweep(db_path, sweep_id=result["sweep_id"], output_path=temp / "summary.json")
            self.assertEqual(summary["status"], "pass")
            self.assertEqual(summary["robustness"]["number_of_children_completed"], 2)
            self.assertIn("InpRangeLookback", summary["robustness"]["plateau_score"])

    def test_parameter_robustness_fixture_summary_warns_on_isolated_pass_and_surfaces_in_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            from automated.research import cli

            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_FBR_FIXTURE_SWEEP_PARENT")
            config_path = temp / "fbr_parameter_robustness.yaml"
            config = _sweep_config(parent["experiment_id"], values=[0.0, 0.05, 0.10, 0.15, 0.20])
            config["parameters"] = {
                "min_break_distance_atr": {
                    "key": "InpMinBreakDistanceAtr",
                    "values": [0.0, 0.05, 0.10, 0.15, 0.20],
                }
            }
            import yaml

            config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
            result = sweeps.prepare_sweep(db_path, config_path=config_path, output_root=temp / "research_runs")
            registry.upsert_experiment_metrics(db_path, parent["experiment_id"], _metrics(net_return=-0.01, profit_factor=0.9))
            fixture_metrics = {
                0.05: _metrics(net_return=-0.03, profit_factor=0.7),
                0.10: _metrics(net_return=0.04, profit_factor=1.2),
                0.15: _metrics(net_return=-0.01, profit_factor=0.95),
                0.20: _metrics(net_return=-0.08, profit_factor=0.6),
            }
            for child in registry.list_sweep_children(db_path, result["sweep_id"]):
                diff = json.loads(child["parameter_diff_json"])
                value = float(diff["InpMinBreakDistanceAtr"]["to"])
                registry.upsert_experiment_metrics(db_path, child["child_experiment_id"], fixture_metrics[value])

            summary_path = temp / "sweep_summary.json"
            with redirect_stdout(StringIO()):
                self.assertEqual(
                    cli.main(
                        [
                            "--db",
                            str(db_path),
                            "sweep",
                            "summarize",
                            "--sweep-id",
                            result["sweep_id"],
                            "--output",
                            str(summary_path),
                        ]
                    ),
                    0,
                )
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            plateau = summary["robustness"]["plateau_score"]["InpMinBreakDistanceAtr"]
            self.assertEqual(summary["status"], "warn")
            self.assertEqual(summary["robustness"]["number_of_children_completed"], 4)
            self.assertEqual(summary["robustness"]["percent_profitable"], 0.25)
            self.assertEqual(plateau["status"], "warn")
            self.assertEqual(plateau["isolated_passes"], 1)

            report = validation.build_validation_report(db_path, parent["experiment_id"])
            parameter_gate = report["sections"]["placeholder_advanced_gates"]["parameter_robustness"]
            self.assertEqual(parameter_gate["sweep_id"], result["sweep_id"])
            self.assertEqual(parameter_gate["status"], "warn")
            self.assertEqual(
                parameter_gate["key_metrics"]["plateau_score"]["InpMinBreakDistanceAtr"]["status"],
                "warn",
            )

    def test_validation_report_includes_sweep_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_VALIDATION_SWEEP_PARENT")
            config_path = temp / "sweep.yaml"
            _write_sweep_config(config_path, parent["experiment_id"], values=[20, 30])
            result = sweeps.prepare_sweep(db_path, config_path=config_path, output_root=temp / "research_runs")
            child = registry.list_sweep_children(db_path, result["sweep_id"])[0]
            registry.upsert_experiment_metrics(db_path, child["child_experiment_id"], _metrics(net_return=0.05, profit_factor=1.2))
            sweeps.summarize_sweep(db_path, sweep_id=result["sweep_id"], output_path=temp / "summary.json")
            report = validation.build_validation_report(db_path, parent["experiment_id"])
            parameter_gate = report["sections"]["placeholder_advanced_gates"]["parameter_robustness"]
            self.assertEqual(parameter_gate["sweep_id"], result["sweep_id"])
            self.assertEqual(parameter_gate["status"], "pass")

    def test_lifecycle_strict_mode_uses_sweep_summary_for_parameter_robustness(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_LIFECYCLE_SWEEP_PARENT")
            config_path = temp / "sweep.yaml"
            _write_sweep_config(config_path, parent["experiment_id"], values=[20, 30])
            result = sweeps.prepare_sweep(db_path, config_path=config_path, output_root=temp / "research_runs")
            child = registry.list_sweep_children(db_path, result["sweep_id"])[0]
            registry.upsert_experiment_metrics(db_path, child["child_experiment_id"], _metrics(net_return=0.05, profit_factor=1.2))
            sweeps.summarize_sweep(db_path, sweep_id=result["sweep_id"], output_path=temp / "summary.json")
            validation_path = temp / "validation.json"
            validation.write_validation_report(db_path, parent["experiment_id"], validation_path)
            spec_path = _temp_strategy_spec(temp, lifecycle_state="robustness_testing")
            evaluation = lifecycle.evaluate_transition(
                db_path,
                strategy=spec_path,
                to_state="stat_review",
                experiment_id=parent["experiment_id"],
                strictness="strict",
            )
            self.assertFalse(any(item["requirement"] == "robustness_implemented" for item in evaluation["blockers"]))
            self.assertTrue(any(item["requirement"] == "walk_forward_implemented" for item in evaluation["blockers"]))


class ResearchPhase8QueueTests(unittest.TestCase):
    def test_queue_schema_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_QUEUE_VALIDATE_PARENT")
            queue_path = temp / "queue.yaml"
            _write_queue_config(queue_path, parent["experiment_id"])
            result = queue.validate_queue(db_path, queue_path)
            self.assertEqual(result["status"], "valid")
            self.assertEqual(result["items"][0]["task_type"], "parameter_robustness")
            self.assertIsNotNone(registry.get_queue_item(db_path, "QUEUE_PARAM_TEST"))

    def test_queue_invalid_permissions_rejected_and_final_holdout_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_QUEUE_PERMISSIONS_PARENT")
            queue_path = temp / "queue.yaml"
            _write_queue_config(
                queue_path,
                parent["experiment_id"],
                permissions={"allow_mql5_edits": True},
            )
            with self.assertRaises(SchemaValidationError):
                queue.validate_queue(db_path, queue_path)

            _write_queue_config(
                queue_path,
                parent["experiment_id"],
                permissions={"allow_final_holdout": True},
            )
            with self.assertRaises(SchemaValidationError):
                queue.validate_queue(db_path, queue_path)

    def test_queue_permission_schema_fails_closed_on_missing_or_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_QUEUE_PERMISSION_SCHEMA_PARENT")
            queue_path = temp / "queue.yaml"
            _write_queue_config(queue_path, parent["experiment_id"])
            data = load_yaml(queue_path)
            data["permissions"].pop("allow_dataset_changes")
            _write_yaml(queue_path, data)
            with self.assertRaises(SchemaValidationError):
                queue.validate_queue(db_path, queue_path)

            _write_queue_config(queue_path, parent["experiment_id"])
            data = load_yaml(queue_path)
            data["permissions"]["allow_surprise_authority"] = True
            _write_yaml(queue_path, data)
            with self.assertRaises(SchemaValidationError):
                queue.validate_queue(db_path, queue_path)

    def test_queue_rejects_absolute_and_traversal_config_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_QUEUE_PATH_PARENT")
            queue_path = temp / "queue.yaml"
            _write_queue_config(queue_path, parent["experiment_id"])
            data = load_yaml(queue_path)
            data["sweep_config"] = "/tmp/sweep.yaml"
            _write_yaml(queue_path, data)
            with self.assertRaises(SchemaValidationError):
                queue.validate_queue(db_path, queue_path)

            _write_queue_config(queue_path, parent["experiment_id"])
            data = load_yaml(queue_path)
            data["sweep_config"]["portfolio_config"] = "../portfolio.yaml"
            _write_yaml(queue_path, data)
            with self.assertRaises(SchemaValidationError):
                queue.validate_queue(db_path, queue_path)

    def test_queue_budget_enforcement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_QUEUE_BUDGET_PARENT")
            queue_path = temp / "queue.yaml"
            _write_queue_config(queue_path, parent["experiment_id"], max_child_experiments=1, values=[20, 30, 40])
            with self.assertRaises(SchemaValidationError):
                queue.validate_queue(db_path, queue_path)

    def test_queue_budget_blocks_before_creating_experiments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            dataset = _register_temp_dataset(db_path, temp)
            queue_path = temp / "queue.yaml"
            _write_baseline_queue_config(queue_path, dataset_id=dataset["dataset_id"])
            data = load_yaml(queue_path)
            data["budget"]["max_experiments"] = 0
            _write_yaml(queue_path, data)
            before = _table_count(db_path, "experiments")
            with self.assertRaises(SchemaValidationError):
                queue.run_queue(db_path, queue_path, output_root=temp / "queue_runs")
            self.assertEqual(_table_count(db_path, "experiments"), before)
            self.assertFalse((temp / "queue_runs").exists())

    def test_queue_dry_run_output_and_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_QUEUE_DRY_PARENT")
            queue_path = temp / "queue.yaml"
            _write_queue_config(queue_path, parent["experiment_id"])
            before = _table_counts(db_path, ["experiments", "sweeps", "research_queue_items", "research_queue_runs"])
            dry = queue.run_queue(db_path, queue_path, dry_run=True)
            self.assertTrue(dry["dry_run"])
            self.assertEqual(dry["tasks_that_would_run"][0]["sweeps_would_prepare"][0]["child_count"], 1)
            self.assertEqual(_table_counts(db_path, ["experiments", "sweeps", "research_queue_items", "research_queue_runs"]), before)
            self.assertFalse((queue.QUEUE_RUN_ROOT / dry["queue_id"]).exists())

            from automated.research import cli

            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(cli.main(["--db", str(db_path), "queue", "run", "--queue", str(queue_path), "--dry-run"]), 0)
            self.assertIn("tasks_that_would_run", output.getvalue())

    def test_queue_run_creates_sweep_records_with_mock_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_QUEUE_RUN_PARENT")
            queue_path = temp / "queue.yaml"
            _write_queue_config(
                queue_path,
                parent["experiment_id"],
                allow_runner_execution=True,
                max_failed_runs=1,
            )
            fake_runner = _write_fake_runner(temp)
            result = queue.run_queue(
                db_path,
                queue_path,
                runner_script=fake_runner,
                output_root=temp / "queue_runs",
            )
            try:
                self.assertIn(result["status"], {"completed", "completed_with_warnings"})
                self.assertEqual(len(result["sweeps_created"]), 1)
                self.assertIsNotNone(registry.get_sweep(db_path, result["sweeps_created"][0]))
                child_id = registry.list_sweep_children(db_path, result["sweeps_created"][0])[0]["child_experiment_id"]
                self.assertIsNotNone(registry.get_experiment_metrics(db_path, child_id))
            finally:
                for experiment_id in result["experiments_created"]:
                    shutil.rmtree(REPO_ROOT / "automated" / "reports" / experiment_id, ignore_errors=True)

    def test_queue_never_applies_lifecycle_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            dataset = _register_temp_dataset(db_path, temp)
            queue_path = temp / "queue.yaml"
            _write_baseline_queue_config(
                queue_path,
                dataset_id=dataset["dataset_id"],
                lifecycle_proposal={"to_state": "robustness_testing", "strictness": "lenient"},
            )
            spec_path = REPO_ROOT / "automated" / "specs" / "strategies" / "failed_breakout_reversal_v1.yaml"
            before = spec_path.read_text(encoding="utf-8")
            result = queue.run_queue(db_path, queue_path, output_root=temp / "queue_runs")
            self.assertEqual(spec_path.read_text(encoding="utf-8"), before)
            self.assertEqual(len(result["lifecycle_transition_proposals"]), 1)
            transition = registry.get_lifecycle_transition(db_path, result["lifecycle_transition_proposals"][0])
            self.assertEqual(transition["status"], "proposed")
            self.assertTrue(any(item["queue_id"] == "QUEUE_BASELINE_TEST" for item in result["item_status_history"]))

    def test_lifecycle_proposal_skipped_when_permission_false(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            dataset = _register_temp_dataset(db_path, temp)
            queue_path = temp / "queue.yaml"
            _write_baseline_queue_config(
                queue_path,
                dataset_id=dataset["dataset_id"],
                lifecycle_proposal={"to_state": "robustness_testing", "strictness": "lenient"},
            )
            data = load_yaml(queue_path)
            data["permissions"]["allow_lifecycle_propose"] = False
            _write_yaml(queue_path, data)
            result = queue.run_queue(db_path, queue_path, output_root=temp / "queue_runs")
            self.assertEqual(result["lifecycle_transition_proposals"], [])
            self.assertFalse(registry.list_lifecycle_transitions(db_path, "failed_breakout_reversal_v1"))
            self.assertTrue(any("lifecycle proposal skipped" in warning for warning in result["warnings"]))

    def test_failure_budget_stops_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            dataset = _register_temp_dataset(db_path, temp)
            queue_path = temp / "queue.yaml"
            _write_two_baseline_queue_config(queue_path, dataset_id=dataset["dataset_id"])
            fake_runner = _write_failing_runner(temp)
            result = queue.run_queue(
                db_path,
                queue_path,
                runner_script=fake_runner,
                output_root=temp / "queue_runs",
            )
            try:
                self.assertEqual(result["status"], "failed")
                self.assertEqual(len(result["failures"]), 1)
                self.assertEqual(len(result["items"]), 2)
                self.assertEqual(result["items"][1]["status"], "skipped")
            finally:
                for experiment_id in result["experiments_created"]:
                    shutil.rmtree(REPO_ROOT / "automated" / "reports" / experiment_id, ignore_errors=True)

    def test_morning_report_generation_includes_failed_variants(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            dataset = _register_temp_dataset(db_path, temp)
            queue_path = temp / "queue.yaml"
            _write_baseline_queue_config(queue_path, dataset_id=dataset["dataset_id"], allow_runner_execution=True)
            fake_runner = _write_failing_runner(temp)
            result = queue.run_queue(
                db_path,
                queue_path,
                runner_script=fake_runner,
                output_root=temp / "queue_runs",
            )
            report = queue.generate_morning_report(db_path, run_id=result["queue_run_id"], output_root=temp / "queue_runs")
            try:
                self.assertTrue(Path(report["json_path"]).is_file())
                self.assertTrue(Path(report["markdown_path"]).is_file())
                self.assertEqual(len(report["report"]["failed_candidates"]), 1)
                self.assertIn(result["experiments_created"][0], report["report"]["archive_candidates"])
                self.assertEqual(len(report["report"]["failed_items"]), 1)
            finally:
                for experiment_id in result["experiments_created"]:
                    shutil.rmtree(REPO_ROOT / "automated" / "reports" / experiment_id, ignore_errors=True)

    def test_morning_report_includes_skipped_and_blocked_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            dataset = _register_temp_dataset(db_path, temp)
            queue_path = temp / "queue.yaml"
            _write_two_baseline_queue_config(queue_path, dataset_id=dataset["dataset_id"])
            fake_runner = _write_failing_runner(temp)
            result = queue.run_queue(
                db_path,
                queue_path,
                runner_script=fake_runner,
                output_root=temp / "queue_runs",
            )
            report = queue.generate_morning_report(db_path, run_id=result["queue_run_id"], output_root=temp / "queue_runs")
            try:
                self.assertEqual(len(report["report"]["skipped_items"]), 1)
                self.assertTrue(report["report"]["blocked_items"])
                self.assertTrue(report["report"]["item_status_history"])
            finally:
                for experiment_id in result["experiments_created"]:
                    shutil.rmtree(REPO_ROOT / "automated" / "reports" / experiment_id, ignore_errors=True)

    def test_red_team_queue_creates_pending_request_not_fake_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_QUEUE_RED_TEAM_PARENT")
            queue_path = temp / "queue.yaml"
            _write_red_team_queue_config(queue_path, parent["experiment_id"])
            result = queue.run_queue(db_path, queue_path, output_root=temp / "queue_runs")
            self.assertEqual(result["status"], "completed_with_warnings")
            self.assertTrue(result["artifacts_created"])
            request = load_yaml(result["artifacts_created"][0])
            self.assertEqual(request["status"], "pending_agent_review")
            artifacts = registry.list_artifacts(db_path, parent["experiment_id"])
            self.assertTrue(any(artifact["artifact_type"] == "review_request" for artifact in artifacts))
            self.assertFalse(any(artifact["artifact_type"] == "red_team_review" for artifact in artifacts))

    def test_rerunning_same_queue_creates_new_queue_run_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_QUEUE_RERUN_PARENT")
            queue_path = temp / "queue.yaml"
            _write_red_team_queue_config(queue_path, parent["experiment_id"])
            first = queue.run_queue(db_path, queue_path, output_root=temp / "queue_runs")
            second = queue.run_queue(db_path, queue_path, output_root=temp / "queue_runs")
            self.assertNotEqual(first["queue_run_id"], second["queue_run_id"])
            self.assertTrue((temp / "queue_runs" / first["queue_run_id"]).is_dir())
            self.assertTrue((temp / "queue_runs" / second["queue_run_id"]).is_dir())
            runs = registry.list_queue_runs(db_path, "QUEUE_RED_TEAM_TEST")
            self.assertEqual(len(runs), 2)


class ResearchPhase7PathResolutionTests(unittest.TestCase):
    def test_queue_child_runner_config_has_child_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_PATH_RESOLVE_PARENT")
            config_path = temp / "sweep.yaml"
            _write_sweep_config(config_path, parent["experiment_id"], values=[20, 30])
            result = sweeps.prepare_sweep(db_path, config_path=config_path, output_root=temp / "research_runs")
            child = registry.list_sweep_children(db_path, result["sweep_id"])[0]
            child_id = child["child_experiment_id"]
            runner_conf = temp / "research_runs" / child_id / "raw" / "runner.conf"
            self.assertTrue(runner_conf.is_file())
            content = runner_conf.read_text(encoding="utf-8")
            run_id_match = next((line for line in content.splitlines() if line.strip().startswith("RUN_ID=")), None)
            self.assertIsNotNone(run_id_match, "RUN_ID not found in runner.conf")
            self.assertIn(child_id, run_id_match, f"RUN_ID does not reference child experiment {child_id}")

    def test_queue_child_uses_queue_local_parameter_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_PATH_LOCAL_PARAM_PARENT")
            config_path = temp / "sweep.yaml"
            _write_sweep_config(config_path, parent["experiment_id"], values=[20, 30])
            result = sweeps.prepare_sweep(db_path, config_path=config_path, output_root=temp / "research_runs")
            child = registry.list_sweep_children(db_path, result["sweep_id"])[0]
            child_id = child["child_experiment_id"]
            runner_conf = temp / "research_runs" / child_id / "raw" / "runner.conf"
            self.assertTrue(runner_conf.is_file())
            content = runner_conf.read_text(encoding="utf-8")
            ea_line = next((line for line in content.splitlines() if line.strip().startswith("EA_SET_FILE=")), None)
            self.assertIsNotNone(ea_line, "EA_SET_FILE not found in runner.conf")
            ea_value = ea_line.split("=", 1)[1].strip().strip('"').strip("'")
            ea_path = Path(ea_value)
            self.assertTrue(ea_path.is_absolute(), f"EA_SET_FILE should be absolute, got: {ea_value}")
            self.assertTrue(ea_path.exists(), f"EA_SET_FILE path does not exist: {ea_value}")
            expected_child_set = (temp / "research_runs" / child_id / "raw" / "parameters.set").resolve()
            self.assertEqual(ea_path, expected_child_set,
                             f"EA_SET_FILE {ea_path} does not match expected {expected_child_set}")

    def test_distinct_sweep_children_have_distinct_parameter_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_DISTINCT_CHILD_PARENT")
            config_path = temp / "sweep.yaml"
            _write_sweep_config(config_path, parent["experiment_id"], values=[20, 30, 40])
            result = sweeps.prepare_sweep(db_path, config_path=config_path, output_root=temp / "research_runs")
            children = registry.list_sweep_children(db_path, result["sweep_id"])
            self.assertGreaterEqual(len(children), 2, "need at least 2 children for this test")
            paths = []
            hashes = []
            for child in children:
                child_id = child["child_experiment_id"]
                runner_conf = temp / "research_runs" / child_id / "raw" / "runner.conf"
                content = runner_conf.read_text(encoding="utf-8")
                ea_line = next((line for line in content.splitlines() if line.strip().startswith("EA_SET_FILE=")))
                ea_value = ea_line.split("=", 1)[1].strip().strip('"').strip("'")
                ea_path = Path(ea_value)
                self.assertTrue(ea_path.is_file(), f"EA_SET_FILE path does not exist for child {child_id}: {ea_value}")
                paths.append(str(ea_path))
                hashes.append(hash_parameter_set(ea_path))
            self.assertEqual(len(set(paths)), len(paths), "children should have distinct parameter file paths")
            self.assertEqual(len(set(hashes)), len(hashes), "children should have distinct parameter_set_hash values")

    def test_original_baseline_set_not_modified(self) -> None:
        baseline_path = REPO_ROOT / "automated" / "runs" / "sets" / "fbr_xauusd_h4_baseline.set"
        original_content = baseline_path.read_bytes()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_BASELINE_UNCHANGED_PARENT")
            config_path = temp / "sweep.yaml"
            _write_sweep_config(config_path, parent["experiment_id"], values=[20, 30])
            sweeps.prepare_sweep(db_path, config_path=config_path, output_root=temp / "research_runs")
            self.assertEqual(baseline_path.read_bytes(), original_content,
                             "original baseline .set was modified by sweep preparation")

    def test_execution_resolution_cannot_use_repo_root_parameters_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_RESOLUTION_PARENT")
            config_path = temp / "sweep.yaml"
            _write_sweep_config(config_path, parent["experiment_id"], values=[20, 30])
            result = sweeps.prepare_sweep(db_path, config_path=config_path, output_root=temp / "research_runs")
            child = registry.list_sweep_children(db_path, result["sweep_id"])[0]
            child_id = child["child_experiment_id"]
            runner_conf = temp / "research_runs" / child_id / "raw" / "runner.conf"
            content = runner_conf.read_text(encoding="utf-8")
            ea_line = next((line for line in content.splitlines() if line.strip().startswith("EA_SET_FILE=")))
            ea_value = ea_line.split("=", 1)[1].strip().strip('"').strip("'")
            ea_path = Path(ea_value)
            self.assertTrue(ea_path.is_absolute())
            self.assertTrue(ea_path.exists())
            legacy_path = REPO_ROOT / "automated" / "runs" / "sets" / ea_path.name
            repo_runs_sets_path = REPO_ROOT / "automated" / "runs" / "sets" / ea_path.name
            self.assertNotEqual(ea_path, repo_runs_sets_path,
                                "EA_SET_FILE should NOT resolve to automated/runs/sets/<name>")

    def test_no_mt5_wine_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            db_path = temp / "registry.sqlite"
            parent = _create_parent_experiment(db_path, temp, "EXP_NO_MT5_PARENT")
            config_path = temp / "sweep.yaml"
            _write_sweep_config(config_path, parent["experiment_id"], values=[20, 30])
            result = sweeps.prepare_sweep(db_path, config_path=config_path, output_root=temp / "research_runs")
            child = registry.list_sweep_children(db_path, result["sweep_id"])[0]
            child_id = child["child_experiment_id"]
            runner_conf = temp / "research_runs" / child_id / "raw" / "runner.conf"
            content = runner_conf.read_text(encoding="utf-8")
            self.assertIn("EA_SET_FILE=", content)
            ea_line = next(line for line in content.splitlines() if line.strip().startswith("EA_SET_FILE="))
            ea_value = ea_line.split("=", 1)[1].strip().strip('"').strip("'")
            self.assertTrue(Path(ea_value).is_absolute())
            self.assertTrue(Path(ea_value).is_file())
            context = runner.experiment_debug_runner_paths(temp / "research_runs" / child_id)
            self.assertEqual(context["experiment_id"], child_id)
            self.assertTrue(context["runner_config_exists"])
            self.assertTrue(context["parameter_file_exists"])
            self.assertIsNotNone(context["ea_set_file_raw"])
            self.assertTrue(context["ea_set_file_raw"].startswith("/"),
                            f"ea_set_file_raw should be absolute: {context['ea_set_file_raw']}")


def _register_temp_dataset(db_path: Path, temp: Path) -> dict[str, str]:
    bars = temp / "dataset_bars.csv"
    bars.write_text(
        "time\topen\thigh\tlow\tclose\ttick_volume\n"
        "2026.01.01 00:00:00\t1\t2\t0.5\t1.5\t10\n"
        "2026.01.01 04:00:00\t1.5\t2.5\t1\t2\t11\n",
        encoding="utf-8",
    )
    return datasets.register_dataset(db_path, bars_path=bars, symbol="XAUUSD", timeframe="H4")


def _temp_strategy_spec(temp: Path, *, lifecycle_state: str) -> Path:
    import yaml

    data = load_yaml(REPO_ROOT / "automated" / "specs" / "strategies" / "failed_breakout_reversal_v1.yaml")
    data["lifecycle"]["state"] = lifecycle_state
    data["lifecycle"]["allowed_next_states"] = sorted(lifecycle.ALLOWED_TRANSITIONS[lifecycle_state])
    data["status"] = lifecycle_state
    path = temp / "failed_breakout_reversal_v1.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_yaml(path: Path, data: dict[str, object]) -> None:
    import yaml

    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _table_count(db_path: Path, table: str) -> int:
    registry.init_db(db_path)
    connection = registry.connect(db_path)
    try:
        row = connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
    finally:
        connection.close()
    return int(row["count"])


def _table_counts(db_path: Path, tables: list[str]) -> dict[str, int]:
    return {table: _table_count(db_path, table) for table in tables}


def _write_statistical_review(path: Path, experiment_id: str) -> None:
    path.write_text(
        f"agent_role: statistical_reviewer\n"
        f"experiment_id: {experiment_id}\n"
        "sample_size_assessment: acceptable\n"
        "number_of_trials: 1\n"
        "multiple_testing_risk: low\n"
        "non_normal_return_concerns: acknowledged\n"
        "overlapping_trade_concerns: none\n"
        "train_test_leakage_concerns: none found\n"
        "decision: pass\n"
        "required_followups: []\n",
        encoding="utf-8",
    )


def _write_red_team_review(path: Path, experiment_id: str, *, decision: str) -> None:
    rejection_reasons = "[]"
    if decision == "reject":
        rejection_reasons = "\n  - Hidden beta exposure unresolved."
    path.write_text(
        f"agent_role: red_team_reviewer\n"
        f"experiment_id: {experiment_id}\n"
        "possible_lookahead: none found\n"
        "survivorship_concerns: none\n"
        "hidden_beta_trend_vol_exposure: no hard concern\n"
        "overfit_filter_concerns: moderate\n"
        "single_trade_dependence: low\n"
        "adjacent_parameter_failure: not evaluated\n"
        f"rejection_reasons: {rejection_reasons}\n"
        f"decision: {decision}\n"
        "required_followups: []\n",
        encoding="utf-8",
    )


def _create_parent_experiment(db_path: Path, temp: Path, experiment_id: str) -> dict[str, object]:
    dataset = _register_temp_dataset(db_path, temp)
    payload = _experiment_payload(dataset)
    payload["experiment_id"] = experiment_id
    payload["status"] = "completed"
    registry.create_experiment(db_path, payload)
    return payload


def _sweep_config(parent_experiment_id: str, *, values: list[object]) -> dict[str, object]:
    return {
        "sweep_type": "parameter_robustness",
        "parent_experiment_id": parent_experiment_id,
        "strategy_id": "failed_breakout_reversal_v1",
        "budget": {
            "max_child_experiments": 25,
            "max_parameters_changed_per_child": 1,
            "require_one_variable_at_a_time": True,
        },
        "parameters": {"InpRangeLookback": {"values": values}},
        "mode": "one_variable_at_a_time",
        "baseline_source": "parent_experiment",
    }


def _write_sweep_config(path: Path, parent_experiment_id: str, *, values: list[object]) -> None:
    import yaml

    path.write_text(yaml.safe_dump(_sweep_config(parent_experiment_id, values=values), sort_keys=False), encoding="utf-8")


def _metrics(*, net_return: float, profit_factor: float) -> dict[str, object]:
    return {
        "period_type": "full",
        "net_return": net_return,
        "cagr": None,
        "sharpe": 1.0,
        "sortino": None,
        "max_drawdown": 0.05,
        "calmar": None,
        "win_rate": 60.0,
        "avg_trade": 1.0,
        "median_trade": 1.0,
        "profit_factor": profit_factor,
        "exposure_time": None,
        "turnover": None,
        "trade_count": 31,
        "best_trade_pct_of_total": None,
        "cost_sensitivity_score": None,
        "parameter_stability_score": None,
        "correlation_to_portfolio": None,
        "notes": "synthetic sweep metric",
    }


def _write_fake_runner(temp: Path) -> Path:
    fake_runner = temp / "fake_runner.py"
    fake_runner.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, re, sys\n"
        f"repo = pathlib.Path({str(REPO_ROOT)!r})\n"
        "text = pathlib.Path(sys.argv[1]).read_text()\n"
        "run_id = re.search(r'RUN_ID=\"?([^\"\\n]+)', text).group(1)\n"
        "out = repo / 'automated' / 'reports' / run_id\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "header = 'strategy_id\\tsymbol\\ttimeframe\\tentry_time\\texit_time\\tdirection\\tentry_price\\texit_price\\tstop_price\\ttarget_price\\tvolume\\tprofit\\tr_multiple\\texit_reason\\n'\n"
        "rows = ''.join(['s\\tXAUUSD\\tPERIOD_H4\\t2026.01.01 04:00:00\\t2026.01.01 08:00:00\\tlong\\t1\\t2\\t0\\t3\\t1\\t10\\t1\\tclosed\\n' for _ in range(31)])\n"
        "(out / 'trades.csv').write_text(header + rows)\n"
        "(out / 'equity.csv').write_text('time\\tbalance\\tequity\\n2026.01.01 00:00:00\\t100\\t100\\n2026.01.02 00:00:00\\t101\\t101\\n')\n"
        "(out / 'bars.csv').write_text('time\\topen\\thigh\\tlow\\tclose\\ttick_volume\\n2026.01.01 00:00:00\\t1\\t2\\t0.5\\t1.5\\t10\\n')\n"
        "(out / 'run_summary.json').write_text('{\"trades\":31,\"net_profit\":10,\"start_balance\":100,\"max_equity_drawdown_pct\":5,\"win_rate_pct\":60,\"expectancy\":0.32,\"profit_factor\":2}')\n"
        "(out / 'terminal_run.log').write_text('fake ok\\n')\n",
        encoding="utf-8",
    )
    fake_runner.chmod(fake_runner.stat().st_mode | 0o111)
    return fake_runner


def _write_failing_runner(temp: Path) -> Path:
    fake_runner = temp / "failing_runner.py"
    fake_runner.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, re, sys\n"
        f"repo = pathlib.Path({str(REPO_ROOT)!r})\n"
        "text = pathlib.Path(sys.argv[1]).read_text()\n"
        "run_id = re.search(r'RUN_ID=\"?([^\"\\n]+)', text).group(1)\n"
        "out = repo / 'automated' / 'reports' / run_id\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'terminal_run.log').write_text('fake failure\\n')\n"
        "sys.exit(2)\n",
        encoding="utf-8",
    )
    fake_runner.chmod(fake_runner.stat().st_mode | 0o111)
    return fake_runner


def _write_queue_config(
    path: Path,
    parent_experiment_id: str,
    *,
    permissions: dict[str, object] | None = None,
    allow_runner_execution: bool = False,
    max_child_experiments: int = 2,
    max_failed_runs: int = 0,
    values: list[object] | None = None,
) -> None:
    import yaml

    values = values or [20, 30]
    item_permissions = {
        "allow_runner_execution": allow_runner_execution,
        "allow_mql5_edits": False,
        "allow_dataset_changes": False,
        "allow_validation_threshold_changes": False,
        "allow_lifecycle_apply": False,
        "allow_lifecycle_propose": True,
        "allow_final_holdout": False,
    }
    if permissions:
        item_permissions.update(permissions)
    config = {
        "queue_id": "QUEUE_PARAM_TEST",
        "priority": 10,
        "hypothesis_id": "HYP_FAILED_BREAKOUT_REVERSAL_001",
        "strategy_id": "failed_breakout_reversal_v1",
        "task_type": "parameter_robustness",
        "parent_experiment_id": parent_experiment_id,
        "requested_by": "test",
        "allowed_agent_roles": ["robustness_agent"],
        "budget": {
            "max_experiments": 0,
            "max_child_experiments": max_child_experiments,
            "max_runtime_minutes": 30,
            "max_parameters_changed_per_child": 1,
            "max_sweeps": 1,
            "max_failed_runs": max_failed_runs,
            "max_disk_usage_mb": 128,
            "require_one_variable_at_a_time": True,
        },
        "permissions": item_permissions,
        "required_outputs": ["sweep_plan", "sweep_summary"],
        "sweep_config": {
            "sweep_type": "parameter_robustness",
            "parent_experiment_id": parent_experiment_id,
            "strategy_id": "failed_breakout_reversal_v1",
            "budget": {
                "max_child_experiments": max_child_experiments,
                "max_parameters_changed_per_child": 1,
                "require_one_variable_at_a_time": True,
            },
            "parameters": {"InpRangeLookback": {"values": values}},
            "mode": "one_variable_at_a_time",
        },
        "status": "queued",
        "created_at": "2026-05-11T00:00:00+00:00",
        "notes": "test queue",
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _write_baseline_queue_config(
    path: Path,
    *,
    dataset_id: str,
    allow_runner_execution: bool = False,
    lifecycle_proposal: dict[str, object] | None = None,
) -> None:
    import yaml

    config = {
        "queue_id": "QUEUE_BASELINE_TEST",
        "priority": 1,
        "hypothesis_id": "HYP_FAILED_BREAKOUT_REVERSAL_001",
        "strategy_id": "failed_breakout_reversal_v1",
        "task_type": "baseline_experiment",
        "dataset_id": dataset_id,
        "requested_by": "test",
        "allowed_agent_roles": ["backtest_runner"],
        "budget": {
            "max_experiments": 1,
            "max_child_experiments": 0,
            "max_runtime_minutes": 30,
            "max_parameters_changed_per_child": 1,
            "max_sweeps": 0,
            "max_failed_runs": 0,
            "max_disk_usage_mb": 128,
            "require_one_variable_at_a_time": True,
        },
        "permissions": {
            "allow_runner_execution": allow_runner_execution,
            "allow_mql5_edits": False,
            "allow_dataset_changes": False,
            "allow_validation_threshold_changes": False,
            "allow_lifecycle_apply": False,
            "allow_lifecycle_propose": True,
            "allow_final_holdout": False,
        },
        "required_outputs": ["run_context", "validation_report"],
        "status": "queued",
        "created_at": "2026-05-11T00:00:00+00:00",
        "notes": "baseline test queue",
    }
    if lifecycle_proposal:
        config["lifecycle_proposal"] = lifecycle_proposal
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _write_two_baseline_queue_config(path: Path, *, dataset_id: str) -> None:
    import yaml

    first = load_yaml(_write_temp_baseline_item(path.parent / "first.yaml", dataset_id=dataset_id, queue_id="QUEUE_BASELINE_FAIL_1"))
    second = load_yaml(_write_temp_baseline_item(path.parent / "second.yaml", dataset_id=dataset_id, queue_id="QUEUE_BASELINE_FAIL_2"))
    path.write_text(yaml.safe_dump({"queue_id": "QUEUE_TWO_BASELINES", "items": [first, second]}, sort_keys=False), encoding="utf-8")


def _write_temp_baseline_item(path: Path, *, dataset_id: str, queue_id: str) -> Path:
    _write_baseline_queue_config(path, dataset_id=dataset_id, allow_runner_execution=True)
    data = load_yaml(path)
    data["queue_id"] = queue_id
    data["budget"]["max_failed_runs"] = 0
    import yaml

    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _write_red_team_queue_config(path: Path, parent_experiment_id: str) -> None:
    import yaml

    config = {
        "queue_id": "QUEUE_RED_TEAM_TEST",
        "priority": 5,
        "hypothesis_id": "HYP_FAILED_BREAKOUT_REVERSAL_001",
        "strategy_id": "failed_breakout_reversal_v1",
        "task_type": "red_team_review",
        "parent_experiment_id": parent_experiment_id,
        "requested_by": "test",
        "allowed_agent_roles": ["red_team_reviewer"],
        "budget": {
            "max_experiments": 0,
            "max_child_experiments": 0,
            "max_runtime_minutes": 30,
            "max_parameters_changed_per_child": 1,
            "max_sweeps": 0,
            "max_failed_runs": 0,
            "max_disk_usage_mb": 128,
            "require_one_variable_at_a_time": True,
        },
        "permissions": {
            "allow_runner_execution": False,
            "allow_mql5_edits": False,
            "allow_dataset_changes": False,
            "allow_validation_threshold_changes": False,
            "allow_lifecycle_apply": False,
            "allow_lifecycle_propose": True,
            "allow_final_holdout": False,
        },
        "required_outputs": ["review_request"],
        "status": "queued",
        "created_at": "2026-05-11T00:00:00+00:00",
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _experiment_payload(dataset: dict[str, str]) -> dict[str, object]:
    return {
        "experiment_id": "EXP_TEST_FAILED_BREAKOUT",
        "hypothesis_id": "HYP_FAILED_BREAKOUT_REVERSAL_001",
        "strategy_id": "failed_breakout_reversal_v1",
        "strategy_version": "v1",
        "run_reason": "manual",
        "created_by": "human",
        "created_at": registry.utc_now(),
        "spec_hash": "spec_hash",
        "parameter_set_hash": "parameter_hash",
        "dataset_id": dataset["dataset_id"],
        "dataset_bundle_id": None,
        "dataset_hash": dataset["file_hash"],
        "dataset_bundle_hash": None,
        "code_version": "unavailable:not_a_git_repository",
        "execution_config_hash": "execution_hash",
        "cost_config_hash": "cost_hash",
        "engine": "mt5",
        "implementation_files": {
            "config": "automated/runs/fbr_xauusd_h4_baseline.conf",
            "parameters": "automated/runs/sets/fbr_xauusd_h4_baseline.set",
            "expert_advisor": "automated/strategies/failed_breakout_reversal_v1/FailedBreakoutReversalV1.mq5",
        },
        "implementation_mode": "wrapped_existing_files",
        "execution_timing": {
            "signal_bar": "closed_bar",
            "entry_bar": "next_bar",
            "assumed_fill_price": "market_first_tick_or_tester_fill_at_next_bar",
        },
        "timeframe": "H4",
        "universe": ["XAUUSD"],
        "parent_experiment_id": None,
        "rerun_of_experiment_id": None,
        "is_artifact_regeneration": False,
        "change_type": "baseline",
        "change_summary": "test baseline",
        "rationale": "test rationale",
        "parameter_diff": None,
        "structural_diff": None,
        "research_budget_snapshot": {"max_parameter_sets": 300},
        "complexity_score": 1,
        "min_trades_required": 1,
        "cost_assumptions_documented": True,
        "dataset_metadata_present": True,
        "hypothesis_present": True,
        "validation_report_path": None,
        "gate_status": "incomplete",
        "started_at": None,
        "completed_at": None,
        "status": "planned",
    }


def _write_runner_artifacts(
    output_dir: Path,
    trades: int,
    profits: list[float] | None = None,
    directions: list[str] | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    header = (
        "strategy_id\tsymbol\ttimeframe\tentry_time\texit_time\tdirection\tentry_price\texit_price\t"
        "stop_price\ttarget_price\tvolume\tprofit\tr_multiple\texit_reason\n"
    )
    profits = profits or [10.0 for _ in range(trades)]
    directions = directions or ["long" for _ in range(trades)]
    rows = []
    for index in range(trades):
        profit = profits[index % len(profits)]
        direction = directions[index % len(directions)]
        rows.append(
            f"s\tXAUUSD\tPERIOD_H4\t2026.01.01 04:00:00\t2026.01.01 08:00:00\t{direction}\t1\t2\t0\t3\t1\t{profit}\t1\tclosed\n"
        )
    (output_dir / "trades.csv").write_text(header + "".join(rows), encoding="utf-8")
    (output_dir / "equity.csv").write_text(
        "time\tbalance\tequity\n"
        "2026.01.01 00:00:00\t100\t100\n"
        "2026.01.01 04:00:00\t101\t101\n"
        "2026.01.01 08:00:00\t100\t100\n",
        encoding="utf-8",
    )
    (output_dir / "bars.csv").write_text(
        "time\topen\thigh\tlow\tclose\ttick_volume\n2026.01.01 00:00:00\t1\t2\t0.5\t1.5\t10\n",
        encoding="utf-8",
    )
    (output_dir / "run_summary.json").write_text(
        json.dumps(
            {
                "trades": trades,
                "net_profit": sum(profits[:trades]) if len(profits) >= trades else sum(profits) * (trades / len(profits)),
                "start_balance": 100,
                "max_equity_drawdown_pct": 0,
                "win_rate_pct": len([profit for profit in profits[:trades] if profit > 0]) / trades * 100 if len(profits) >= trades else 100,
                "expectancy": (sum(profits[:trades]) if len(profits) >= trades else sum(profits) * (trades / len(profits))) / trades,
                "profit_factor": 2,
                "gross_profit": sum(profit for profit in profits[:trades] if profit > 0) if len(profits) >= trades else None,
                "gross_loss": sum(profit for profit in profits[:trades] if profit < 0) if len(profits) >= trades else None,
            }
        ),
        encoding="utf-8",
    )
    (output_dir / "terminal_run.log").write_text("ok\n", encoding="utf-8")


def _portfolio_config(experiment_ids: list[str]) -> dict[str, object]:
    return {
        "portfolio_id": "test_portfolio",
        "name": "Test Portfolio",
        "experiments": experiment_ids,
        "frequency": "daily",
        "correlation": {"rolling_windows": [3], "tail_threshold_quantile": 0.40},
        "promotion_thresholds": {
            "max_average_abs_corr": 0.70,
            "max_tail_corr": 0.80,
            "max_drawdown_overlap": 0.75,
        },
    }


def _create_experiment_with_equity(
    db_path: Path,
    temp: Path,
    experiment_id: str,
    equity_values: list[float],
) -> None:
    dataset = _register_temp_dataset(db_path, temp)
    payload = _experiment_payload(dataset)
    payload["experiment_id"] = experiment_id
    registry.create_experiment(db_path, payload)
    equity = temp / f"{experiment_id}_equity.csv"
    rows = ["time\tbalance\tequity\n"]
    for index, value in enumerate(equity_values, start=1):
        rows.append(f"2026.01.{index:02d} 00:00:00\t{value}\t{value}\n")
    equity.write_text("".join(rows), encoding="utf-8")
    registry.attach_artifact(db_path, experiment_id, "equity_curve", equity)


if __name__ == "__main__":
    unittest.main()
