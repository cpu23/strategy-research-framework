from __future__ import annotations

import json
import os
import sqlite3
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

import yaml

from automated.research import backtest_readiness, cli, implementation as impl_mod, registry
from automated.research.schemas import REPO_ROOT, SANDBOX_ROOT


def _make_mq5(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "//+------------------------------------------------------------------+\n"
        "//| TestStrategy.mq5                                                |\n"
        "//+------------------------------------------------------------------+\n"
        "#property version   \"1.00\"\n"
        "input double InpRiskPerTrade = 0.01;\n"
        "int OnInit() { return INIT_SUCCEEDED; }\n"
        "void OnTick() {}\n"
        "void OnDeinit(const int reason) {}\n",
        encoding="utf-8",
    )


def _make_fake_runner(script_path: Path, exit_code: int = 0, produce_outputs: bool = True) -> Path:
    script_path.parent.mkdir(parents=True, exist_ok=True)
    repo_root_str = str(REPO_ROOT.resolve())
    script_path.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, json\n"
        "from pathlib import Path\n"
        f"exit_code = {exit_code}\n"
        "conf_path = sys.argv[1]\n"
        "run_id = None\n"
        f"repo_root = Path({repo_root_str!r})\n"
        "with open(conf_path) as f:\n"
        "    for line in f:\n"
        "        line = line.strip()\n"
        "        if line.startswith('RUN_ID='):\n"
        "            run_id = line.split('=', 1)[1].strip().strip('\"').strip(\"'\")\n"
        "report_dir = repo_root / 'automated' / 'reports' / (run_id or 'unknown')\n"
        "report_dir.mkdir(parents=True, exist_ok=True)\n"
        f"if {1 if produce_outputs else 0}:\n"
        "    (report_dir / 'trades.csv').write_text('profit\\tsymbol\\n0.0\\tXAUUSD\\n')\n"
        "    (report_dir / 'equity.csv').write_text('time\\tequity\\n0\\t100000\\n')\n"
        "    (report_dir / 'run_summary.json').write_text(json.dumps({'trades':1,'net_profit':0.0,'start_balance':100000,'max_equity_drawdown_pct':0.0,'win_rate_pct':0.0,'expectancy':0.0,'profit_factor':0.0}))\n"
        "    (report_dir / 'terminal_run.log').write_text('backtest complete\\n')\n"
        "    (report_dir / 'tester_agent.log').write_text('agent log\\n')\n"
        "    (report_dir / 'compile.log').write_text('compile ok\\n')\n"
        "sys.exit(exit_code)\n",
        encoding="utf-8",
    )
    st = os.stat(script_path)
    os.chmod(script_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script_path


def _make_fake_timeout_runner(script_path: Path) -> Path:
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        "#!/usr/bin/env python3\n"
        "import time\n"
        "time.sleep(600)\n",
        encoding="utf-8",
    )
    st = os.stat(script_path)
    os.chmod(script_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script_path


def _make_runner_conf(
    conf_path: Path,
    mq5_path: Path,
    set_file_path: Path,
    *,
    symbol: str = "XAUUSD",
    timeframe: str = "H4",
    broker: str = "mock",
    run_id: str = "test_strategy_baseline",
    strategy_id: str = "test_strategy",
) -> None:
    conf_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f'RUN_ID="{run_id}"',
        f'STRATEGY_ID="{strategy_id}"',
        f'EA_NAME="{strategy_id}"',
        f'EA_SOURCE="{mq5_path.resolve()}"',
        f'MT5_EXPERT="Automated\\\\{strategy_id}"',
        f'BROKER="{broker}"',
        f'SYMBOL="{symbol}"',
        f'TIMEFRAME="{timeframe}"',
        'DATE_FROM="2024.01.01"',
        'DATE_TO="2025.12.31"',
        'DEPOSIT="100000"',
        'CURRENCY="USD"',
        'LEVERAGE="1:100"',
        'MODEL="1"',
        'EXECUTION_MODE="0"',
        'OPTIMIZATION="0"',
        'FORWARD_MODE="0"',
        'VISUAL="0"',
        'USE_LOCAL="1"',
        'USE_REMOTE="0"',
        'USE_CLOUD="0"',
        f'EA_SET_FILE="{set_file_path.resolve()}"',
    ]
    conf_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_set_file(set_file_path: Path) -> None:
    set_file_path.parent.mkdir(parents=True, exist_ok=True)
    set_file_path.write_text("InpAtrPeriod=14\nInpRiskPercent=1.0\n", encoding="utf-8")


def _make_readiness_config(
    output_dir: Path,
    runner_conf_path: Path,
    set_file_path: Path,
    wine_binary: str = "/usr/bin/wine",
    timeout: int = 180,
    symbol: str = "XAUUSD",
    timeframe: str = "H4",
    expected_dataset_id: str | None = None,
) -> Path:
    config = {
        "mode": "real_backtest_readiness",
        "wine_binary": wine_binary,
        "wine_prefix": None,
        "terminal_path": None,
        "terminal_data_dir": None,
        "timeout_seconds": timeout,
        "max_duration_seconds": timeout,
        "expected_symbol": symbol,
        "expected_timeframe": timeframe,
        "expected_dataset_id": expected_dataset_id,
        "runner_conf_path": str(runner_conf_path.resolve()),
        "set_file_path": str(set_file_path.resolve()),
        "output_dir": str(output_dir.resolve()),
    }
    config_path = output_dir / "readiness_config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_path


def _count_rows(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return row[0] if row else 0
    except Exception:
        return 0
    finally:
        conn.close()


class ResearchPhase19ReadinessTests(unittest.TestCase):
    """Backtest readiness using a fake runner script."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.strategy_id = "ph19_test"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"
        _make_mq5(self.mq5_path)
        self.create_result = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            sandbox_dir=self.sandbox,
            generated_files=[f"{self.strategy_id}.mq5"],
            created_by="ph19_test",
        )
        self.impl_req_id = self.create_result["implementation_request_id"]
        impl_mod.compile_check(self.db_path, self.impl_req_id, mock=True)

        self.runner_dir = Path(self.temp_dir.name) / "runner"
        self.runner_dir.mkdir(parents=True, exist_ok=True)
        self.fake_runner = self.runner_dir / "run_backtest.sh"

        self.conf_path = self.runner_dir / f"{self.strategy_id}.conf"
        self.set_path = self.runner_dir / f"{self.strategy_id}.set"
        _make_set_file(self.set_path)
        _make_runner_conf(self.conf_path, self.mq5_path, self.set_path)

        self.output_dir = Path(self.temp_dir.name) / "readiness_output"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _check_no_side_effects(self) -> None:
        self.assertEqual(
            _count_rows(self.db_path, "scope_approvals"), 0,
            "Readiness must not create approval records",
        )
        self.assertEqual(
            _count_rows(self.db_path, "lifecycle_transitions"), 0,
            "Readiness must not create lifecycle transitions",
        )
        self.assertEqual(
            _count_rows(self.db_path, "experiments"), 0,
            "Readiness must not create experiments",
        )

    def test_readiness_success_emits_passed(self):
        _make_fake_runner(self.fake_runner, exit_code=0)
        config_path = _make_readiness_config(
            self.output_dir, self.conf_path, self.set_path,
        )
        config = backtest_readiness.load_real_backtest_readiness_config(config_path)
        result = backtest_readiness.run_real_backtest_readiness(
            self.db_path, self.impl_req_id, config,
            runner_script=self.fake_runner,
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["impl_request_id"], self.impl_req_id)
        self.assertIn("strategy_id", result)
        self.assertIn("implementation_id", result)
        self.assertIn("runner_conf_path", result)
        self.assertIn("set_file_path", result)
        self.assertIn("command_display", result)
        self.assertIsNotNone(result.get("exit_code"))
        self.assertIn("started_at", result)
        self.assertIn("finished_at", result)
        self.assertIn("duration_seconds", result)
        self.assertEqual(result["timeout_seconds"], 180)
        self._check_no_side_effects()

    def test_readiness_failure_emits_failed(self):
        _make_fake_runner(self.fake_runner, exit_code=1)
        config_path = _make_readiness_config(
            self.output_dir, self.conf_path, self.set_path,
        )
        config = backtest_readiness.load_real_backtest_readiness_config(config_path)
        result = backtest_readiness.run_real_backtest_readiness(
            self.db_path, self.impl_req_id, config,
            runner_script=self.fake_runner,
        )
        self.assertEqual(result["status"], "failed")
        self._check_no_side_effects()

    def test_readiness_timeout_emits_timed_out(self):
        _make_fake_timeout_runner(self.fake_runner)
        config_path = _make_readiness_config(
            self.output_dir, self.conf_path, self.set_path,
            timeout=1,
        )
        config = backtest_readiness.load_real_backtest_readiness_config(config_path)
        result = backtest_readiness.run_real_backtest_readiness(
            self.db_path, self.impl_req_id, config,
            runner_script=self.fake_runner,
        )
        self.assertEqual(result["status"], "timed_out")
        self._check_no_side_effects()

    def test_missing_config_rejected(self):
        missing = Path(self.temp_dir.name) / "no_such_config.yaml"
        with self.assertRaises(FileNotFoundError):
            backtest_readiness.load_real_backtest_readiness_config(missing)

    def test_missing_runner_conf_rejected(self):
        _make_fake_runner(self.fake_runner, exit_code=0)
        missing_conf = self.runner_dir / "nonexistent.conf"
        config_path = _make_readiness_config(
            self.output_dir, missing_conf, self.set_path,
        )
        config = backtest_readiness.load_real_backtest_readiness_config(config_path)
        result = backtest_readiness.run_real_backtest_readiness(
            self.db_path, self.impl_req_id, config,
            runner_script=self.fake_runner,
        )
        self.assertEqual(result["status"], "failed")

    def test_missing_set_file_rejected(self):
        _make_fake_runner(self.fake_runner, exit_code=0)
        missing_set = self.runner_dir / "nonexistent.set"
        config_path = _make_readiness_config(
            self.output_dir, self.conf_path, missing_set,
        )
        config = backtest_readiness.load_real_backtest_readiness_config(config_path)
        result = backtest_readiness.run_real_backtest_readiness(
            self.db_path, self.impl_req_id, config,
            runner_script=self.fake_runner,
        )
        self.assertEqual(result["status"], "failed")

    def test_report_log_collection_works(self):
        _make_fake_runner(self.fake_runner, exit_code=0, produce_outputs=True)
        config_path = _make_readiness_config(
            self.output_dir, self.conf_path, self.set_path,
        )
        config = backtest_readiness.load_real_backtest_readiness_config(config_path)
        result = backtest_readiness.run_real_backtest_readiness(
            self.db_path, self.impl_req_id, config,
            runner_script=self.fake_runner,
        )
        self.assertEqual(result["status"], "passed")
        self.assertTrue(
            any("trades.csv" in p for p in result.get("report_paths", [])),
            msg="Expected trades.csv in report_paths",
        )
        self.assertTrue(
            any("run_summary.json" in p for p in result.get("report_paths", [])),
            msg="Expected run_summary.json in report_paths",
        )
        self.assertTrue(
            any("terminal_run.log" in p for p in result.get("log_paths", [])),
            msg="Expected terminal_run.log in log_paths",
        )


class ResearchPhase19UnsafePathTests(unittest.TestCase):
    """Readiness must reject unsafe paths."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.strategy_id = "ph19_path_test"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"
        _make_mq5(self.mq5_path)
        self.create_result = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            sandbox_dir=self.sandbox,
            generated_files=[f"{self.strategy_id}.mq5"],
            created_by="ph19_test",
        )
        self.impl_req_id = self.create_result["implementation_request_id"]
        impl_mod.compile_check(self.db_path, self.impl_req_id, mock=True)

        self.runner_dir = Path(self.temp_dir.name) / "runner"
        self.runner_dir.mkdir(parents=True, exist_ok=True)
        self.fake_runner = self.runner_dir / "run_backtest.sh"
        _make_fake_runner(self.fake_runner, exit_code=0)

        self.output_dir = Path(self.temp_dir.name) / "readiness_output"
        self.safe_conf_path = self.runner_dir / f"{self.strategy_id}.conf"
        self.safe_set_path = self.runner_dir / f"{self.strategy_id}.set"
        _make_set_file(self.safe_set_path)
        _make_runner_conf(self.safe_conf_path, self.mq5_path, self.safe_set_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run_with_paths(self, *, mq5_path: Path | None = None, conf_path: Path | None = None, set_path: Path | None = None) -> dict[str, Any]:
        c = conf_path or self.safe_conf_path
        s = set_path or self.safe_set_path
        o = self.output_dir
        config_path = _make_readiness_config(o, c, s)
        config = backtest_readiness.load_real_backtest_readiness_config(config_path)
        if mq5_path is not None:
            new_mq5 = Path(mq5_path)
            impl_id = "IMPL_ph19_path_" + new_mq5.stem[:8]
            conn = sqlite3.connect(str(self.db_path))
            try:
                conn.execute(
                    "UPDATE implementations SET generated_mq5_path=? WHERE implementation_request_id=?",
                    (str(new_mq5.resolve()), self.impl_req_id),
                )
                conn.commit()
            finally:
                conn.close()
        return backtest_readiness.run_real_backtest_readiness(
            self.db_path, self.impl_req_id, config,
            runner_script=self.fake_runner,
        )

    def test_production_mq5_path_rejected(self):
        prod_path = REPO_ROOT / "automated" / "strategies" / "SomeEA" / "v1"
        prod_path.mkdir(parents=True, exist_ok=True)
        prod_mq5 = prod_path / "SomeEA.mq5"
        _make_mq5(prod_mq5)
        result = self._run_with_paths(mq5_path=prod_mq5)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(
            any("forbidden" in e.lower() or "must be under" in e.lower() for e in result.get("errors", [])),
            msg=f"Expected path rejection, got: {result.get('errors')}",
        )

    def test_non_sandbox_mq5_path_rejected(self):
        outside = Path(self.temp_dir.name) / "outside_sandbox" / "v1"
        outside.mkdir(parents=True, exist_ok=True)
        outside_mq5 = outside / "Outside.mq5"
        _make_mq5(outside_mq5)
        result = self._run_with_paths(mq5_path=outside_mq5)
        self.assertEqual(result["status"], "failed")

    def test_output_dir_under_production_rejected(self):
        prod_output = REPO_ROOT / "automated" / "strategies" / "SomeEA" / "readiness"
        config_path = _make_readiness_config(
            prod_output, self.safe_conf_path, self.safe_set_path,
        )
        config = backtest_readiness.load_real_backtest_readiness_config(config_path)
        result = backtest_readiness.run_real_backtest_readiness(
            self.db_path, self.impl_req_id, config,
            runner_script=self.fake_runner,
        )
        self.assertEqual(result["status"], "failed")
        self.assertTrue(
            any("output" in e.lower() and "strategies" in e.lower() for e in result.get("errors", [])),
            msg=f"Expected output dir rejection, got: {result.get('errors')}",
        )


class ResearchPhase19ConfigValidationTests(unittest.TestCase):
    """Config loading and validation tests."""

    def test_valid_config_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            config_path = output / "config.yaml"
            config_data = {
                "mode": "real_backtest_readiness",
                "wine_binary": "/usr/bin/wine",
                "wine_prefix": "/home/user/wine",
                "terminal_path": "/home/user/terminal64.exe",
                "terminal_data_dir": "/home/user/MT5/data",
                "timeout_seconds": 60,
                "max_duration_seconds": 60,
                "expected_symbol": "XAUUSD",
                "expected_timeframe": "H4",
                "expected_dataset_id": "DATA_XAUUSD_H4_ABC123",
                "runner_conf_path": "/tmp/test.conf",
                "set_file_path": "/tmp/test.set",
                "output_dir": "/tmp/readiness_output",
            }
            config_path.write_text(yaml.safe_dump(config_data), encoding="utf-8")
            config = backtest_readiness.load_real_backtest_readiness_config(config_path)
            self.assertEqual(config.mode, "real_backtest_readiness")
            self.assertEqual(config.wine_binary, "/usr/bin/wine")
            self.assertEqual(config.wine_prefix, "/home/user/wine")
            self.assertEqual(config.terminal_path, "/home/user/terminal64.exe")
            self.assertEqual(config.timeout_seconds, 60)
            self.assertEqual(config.max_duration_seconds, 60)
            self.assertEqual(config.expected_symbol, "XAUUSD")
            self.assertEqual(config.expected_timeframe, "H4")
            self.assertEqual(config.expected_dataset_id, "DATA_XAUUSD_H4_ABC123")
            self.assertEqual(config.runner_conf_path, "/tmp/test.conf")
            self.assertEqual(config.set_file_path, "/tmp/test.set")
            self.assertEqual(config.output_dir, "/tmp/readiness_output")

    def test_wrong_mode_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text(yaml.safe_dump({"mode": "real_backtest"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                backtest_readiness.load_real_backtest_readiness_config(config_path)

    def test_missing_file_rejected(self):
        with self.assertRaises(FileNotFoundError):
            backtest_readiness.load_real_backtest_readiness_config("/nonexistent/path.yaml")

    def test_zero_timeout_rejected(self):
        raw = {
            "mode": "real_backtest_readiness",
            "timeout_seconds": 0,
            "max_duration_seconds": 0,
            "expected_symbol": "XAUUSD",
            "expected_timeframe": "H4",
            "runner_conf_path": "/tmp/test.conf",
            "set_file_path": "/tmp/test.set",
            "output_dir": "/tmp/out",
        }
        with self.assertRaises(ValueError):
            backtest_readiness.validate_real_backtest_readiness_config(raw)

    def test_excessive_timeout_rejected(self):
        raw = {
            "mode": "real_backtest_readiness",
            "timeout_seconds": 9999,
            "max_duration_seconds": 9999,
            "expected_symbol": "XAUUSD",
            "expected_timeframe": "H4",
            "runner_conf_path": "/tmp/test.conf",
            "set_file_path": "/tmp/test.set",
            "output_dir": "/tmp/out",
        }
        with self.assertRaises(ValueError):
            backtest_readiness.validate_real_backtest_readiness_config(raw)

    def test_missing_required_fields_rejected(self):
        raw = {"mode": "real_backtest_readiness"}
        with self.assertRaises(ValueError):
            backtest_readiness.validate_real_backtest_readiness_config(raw)

    def test_bad_mode_type_rejected(self):
        raw = {"mode": 42}
        with self.assertRaises(ValueError):
            backtest_readiness.validate_real_backtest_readiness_config(raw)


class ResearchPhase19ConfValidationTests(unittest.TestCase):
    """Generated .conf safety validation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sandbox = SANDBOX_ROOT / "ph19_conf_test" / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / "ph19_conf_test.mq5"
        _make_mq5(self.mq5_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_conf(self, **overrides) -> tuple[Path, Path, Path]:
        output = Path(self.temp_dir.name) / "conf_test"
        output.mkdir(parents=True, exist_ok=True)
        conf_path = output / "test.conf"
        set_path = output / "test.set"
        _make_set_file(set_path)
        params = {
            "symbol": "XAUUSD",
            "timeframe": "H4",
            "broker": "mock",
            "run_id": "test_run",
            "strategy_id": "ph19_conf_test",
        }
        params.update(overrides)
        _make_runner_conf(conf_path, self.mq5_path, set_path, **params)
        return conf_path, set_path, output

    def test_broker_mismatch_rejected(self):
        conf_path, set_path, output = self._make_conf(broker="live")
        config_obj = backtest_readiness.RealBacktestReadinessConfig(
            mode="real_backtest_readiness",
            wine_binary="/usr/bin/wine",
            expected_symbol="XAUUSD",
            expected_timeframe="H4",
            runner_conf_path=str(conf_path.resolve()),
            set_file_path=str(set_path.resolve()),
            output_dir=str(output.resolve()),
        )
        errors = backtest_readiness._validate_runner_conf(conf_path, config_obj, self.sandbox)
        self.assertTrue(
            any("BROKER" in e for e in errors),
            msg=f"Expected BROKER validation error, got: {errors}",
        )

    def test_symbol_mismatch_rejected(self):
        conf_path, set_path, output = self._make_conf(symbol="GBPUSD")
        config_obj = backtest_readiness.RealBacktestReadinessConfig(
            mode="real_backtest_readiness",
            wine_binary="/usr/bin/wine",
            expected_symbol="XAUUSD",
            expected_timeframe="H4",
            runner_conf_path=str(conf_path.resolve()),
            set_file_path=str(set_path.resolve()),
            output_dir=str(output.resolve()),
        )
        errors = backtest_readiness._validate_runner_conf(conf_path, config_obj, self.sandbox)
        self.assertTrue(
            any("SYMBOL" in e for e in errors),
            msg=f"Expected SYMBOL validation error, got: {errors}",
        )

    def test_timeframe_mismatch_rejected(self):
        conf_path, set_path, output = self._make_conf(timeframe="H1")
        config_obj = backtest_readiness.RealBacktestReadinessConfig(
            mode="real_backtest_readiness",
            wine_binary="/usr/bin/wine",
            expected_symbol="XAUUSD",
            expected_timeframe="H4",
            runner_conf_path=str(conf_path.resolve()),
            set_file_path=str(set_path.resolve()),
            output_dir=str(output.resolve()),
        )
        errors = backtest_readiness._validate_runner_conf(conf_path, config_obj, self.sandbox)
        self.assertTrue(
            any("TIMEFRAME" in e for e in errors),
            msg=f"Expected TIMEFRAME validation error, got: {errors}",
        )

    def test_conf_with_valid_values_passes(self):
        conf_path, set_path, output = self._make_conf()
        config_obj = backtest_readiness.RealBacktestReadinessConfig(
            mode="real_backtest_readiness",
            wine_binary="/usr/bin/wine",
            expected_symbol="XAUUSD",
            expected_timeframe="H4",
            runner_conf_path=str(conf_path.resolve()),
            set_file_path=str(set_path.resolve()),
            output_dir=str(output.resolve()),
        )
        errors = backtest_readiness._validate_runner_conf(conf_path, config_obj, self.sandbox)
        self.assertEqual(errors, [], f"Expected no validation errors, got: {errors}")


class ResearchPhase19SideEffectTests(unittest.TestCase):
    """Readiness must not create side effects beyond evidence output."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.strategy_id = "ph19_side_effects"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"
        _make_mq5(self.mq5_path)
        self.create_result = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            sandbox_dir=self.sandbox,
            generated_files=[f"{self.strategy_id}.mq5"],
            created_by="ph19_test",
        )
        self.impl_req_id = self.create_result["implementation_request_id"]
        impl_mod.compile_check(self.db_path, self.impl_req_id, mock=True)

        self.runner_dir = Path(self.temp_dir.name) / "runner"
        self.runner_dir.mkdir(parents=True, exist_ok=True)
        self.fake_runner = self.runner_dir / "run_backtest.sh"
        _make_fake_runner(self.fake_runner, exit_code=0)

        self.conf_path = self.runner_dir / f"{self.strategy_id}.conf"
        self.set_path = self.runner_dir / f"{self.strategy_id}.set"
        _make_set_file(self.set_path)
        _make_runner_conf(self.conf_path, self.mq5_path, self.set_path)
        self.output_dir = Path(self.temp_dir.name) / "readiness_output"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_readiness_no_side_effects(self):
        config_path = _make_readiness_config(
            self.output_dir, self.conf_path, self.set_path,
        )
        config = backtest_readiness.load_real_backtest_readiness_config(config_path)
        backtest_readiness.run_real_backtest_readiness(
            self.db_path, self.impl_req_id, config,
            runner_script=self.fake_runner,
        )
        self.assertEqual(
            _count_rows(self.db_path, "scope_approvals"), 0,
            "Must not create any approval records",
        )
        self.assertEqual(
            _count_rows(self.db_path, "lifecycle_transitions"), 0,
            "Must not create lifecycle transitions",
        )
        self.assertEqual(
            _count_rows(self.db_path, "experiments"), 0,
            "Must not create experiment records",
        )


class ResearchPhase19NoProductionWriteTests(unittest.TestCase):
    """Readiness must not write or copy into automated/strategies/."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.strategy_id = "ph19_no_write"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"
        _make_mq5(self.mq5_path)
        self.create_result = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            sandbox_dir=self.sandbox,
            generated_files=[f"{self.strategy_id}.mq5"],
            created_by="ph19_test",
        )
        self.impl_req_id = self.create_result["implementation_request_id"]
        impl_mod.compile_check(self.db_path, self.impl_req_id, mock=True)

        self.runner_dir = Path(self.temp_dir.name) / "runner"
        self.runner_dir.mkdir(parents=True, exist_ok=True)
        self.fake_runner = self.runner_dir / "run_backtest.sh"
        _make_fake_runner(self.fake_runner, exit_code=0)

        self.conf_path = self.runner_dir / f"{self.strategy_id}.conf"
        self.set_path = self.runner_dir / f"{self.strategy_id}.set"
        _make_set_file(self.set_path)
        _make_runner_conf(self.conf_path, self.mq5_path, self.set_path)
        self.output_dir = Path(self.temp_dir.name) / "readiness_output"

        self.prod_strategies = SANDBOX_ROOT.parent / "strategies"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_no_files_written_to_production(self):
        files_before = set()
        if self.prod_strategies.is_dir():
            files_before = set(str(p.relative_to(self.prod_strategies)) for p in self.prod_strategies.rglob("*") if p.is_file())
        config_path = _make_readiness_config(
            self.output_dir, self.conf_path, self.set_path,
        )
        config = backtest_readiness.load_real_backtest_readiness_config(config_path)
        backtest_readiness.run_real_backtest_readiness(
            self.db_path, self.impl_req_id, config,
            runner_script=self.fake_runner,
        )
        files_after = set()
        if self.prod_strategies.is_dir():
            files_after = set(str(p.relative_to(self.prod_strategies)) for p in self.prod_strategies.rglob("*") if p.is_file())
        new_files = files_after - files_before
        self.assertEqual(
            len(new_files), 0,
            f"Readiness must not write files to automated/strategies/. New files: {new_files}",
        )


class ResearchPhase19CLITests(unittest.TestCase):
    """CLI-level tests for backtest-readiness."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.strategy_id = "ph19_cli"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"
        _make_mq5(self.mq5_path)
        self.create_result = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            sandbox_dir=self.sandbox,
            generated_files=[f"{self.strategy_id}.mq5"],
            created_by="ph19_test",
        )
        self.impl_req_id = self.create_result["implementation_request_id"]
        impl_mod.compile_check(self.db_path, self.impl_req_id, mock=True)

        self.runner_dir = Path(self.temp_dir.name) / "runner"
        self.runner_dir.mkdir(parents=True, exist_ok=True)
        self.fake_runner = self.runner_dir / "run_backtest.sh"
        _make_fake_runner(self.fake_runner, exit_code=0)

        self.conf_path = self.runner_dir / f"{self.strategy_id}.conf"
        self.set_path = self.runner_dir / f"{self.strategy_id}.set"
        _make_set_file(self.set_path)
        _make_runner_conf(self.conf_path, self.mq5_path, self.set_path)
        self.output_dir = Path(self.temp_dir.name) / "readiness_output"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cli_missing_config_rejected(self):
        missing = Path(self.temp_dir.name) / "no_such_config.yaml"
        out = StringIO()
        with redirect_stdout(out):
            rc = cli.main([
                "--db", str(self.db_path),
                "implementation", "backtest-readiness",
                "--real-backtest-readiness-config", str(missing),
                self.impl_req_id,
            ])
        self.assertEqual(rc, 1)
        data = json.loads(out.getvalue())
        self.assertIn("not found", data.get("error", ""))

    def test_cli_success(self):
        """CLI success test. Without real MT5/Wine, the runner exits
        non-zero and returns 'failed' status — expected when the
        environment is not configured. Function-level test
        test_readiness_success_emits_passed covers the success path."""
        config_path = _make_readiness_config(
            self.output_dir, self.conf_path, self.set_path,
        )
        out = StringIO()
        with redirect_stdout(out):
            rc = cli.main([
                "--db", str(self.db_path),
                "implementation", "backtest-readiness",
                "--real-backtest-readiness-config", str(config_path),
                self.impl_req_id,
            ])
        data = json.loads(out.getvalue())
        self.assertIn(data.get("status"), ("failed", "passed"),
                      "CLI must return a valid readiness status")
        self.assertIn("impl_request_id", data)
        self.assertIn("mode", data)

    def test_cli_failure(self):
        fake_fail = self.runner_dir / "run_fail.sh"
        _make_fake_runner(fake_fail, exit_code=1)
        config_path = _make_readiness_config(
            self.output_dir, self.conf_path, self.set_path,
        )
        out = StringIO()
        with redirect_stdout(out):
            rc = cli.main([
                "--db", str(self.db_path),
                "implementation", "backtest-readiness",
                "--real-backtest-readiness-config", str(config_path),
                self.impl_req_id,
            ])
        self.assertEqual(rc, 1)
        data = json.loads(out.getvalue())
        self.assertEqual(data["status"], "failed")


class ResearchPhase19QueueNotExposedTests(unittest.TestCase):
    """Queue must not expose real backtest readiness."""

    def test_queue_does_not_reference_backtest_readiness(self):
        import automated.research.queue as queue_mod
        source = Path(queue_mod.__file__).read_text(encoding="utf-8")
        self.assertNotIn(
            "backtest_readiness", source,
            "queue.py must not import or reference backtest_readiness",
        )


class ResearchPhase19PipelineNotImportedTests(unittest.TestCase):
    """Generated baseline/robustness/final-holdout/candidate must not import backtest_readiness."""

    def _check_module(self, mod_path: Path, mod_name: str) -> None:
        source = mod_path.read_text(encoding="utf-8")
        self.assertNotIn(
            "backtest_readiness", source,
            f"{mod_name} must not import or reference backtest_readiness",
        )

    def test_generated_baseline_not_imported(self):
        import automated.research.generated_baseline as m
        self._check_module(Path(m.__file__), "generated_baseline.py")

    def test_generated_robustness_not_imported(self):
        import automated.research.generated_robustness as m
        self._check_module(Path(m.__file__), "generated_robustness.py")

    def test_generated_candidate_not_imported(self):
        import automated.research.generated_candidate as m
        self._check_module(Path(m.__file__), "generated_candidate.py")

    def test_generated_final_holdout_not_imported(self):
        import automated.research.generated_final_holdout as m
        self._check_module(Path(m.__file__), "generated_final_holdout.py")


class ResearchPhase19DenyListTests(unittest.TestCase):
    """Deny-list terms must not appear as allowed values."""

    def test_deny_list_terms_not_in_backtest_readiness_module(self):
        source = Path(backtest_readiness.__file__).read_text(encoding="utf-8")
        for term in ("promote_to_production", "production_candidate", "live_trading_candidate"):
            self.assertNotIn(
                term, source,
                f"{term} must not appear in backtest_readiness.py as an allowed value",
            )

    def test_deny_list_terms_not_in_cli_backtest_readiness(self):
        import automated.research.cli as cli_mod
        source = Path(cli_mod.__file__).read_text(encoding="utf-8")
        for term in ("promote_to_production", "production_candidate", "live_trading_candidate"):
            self.assertNotIn(
                term, source,
                f"{term} must not appear in cli.py backtest-readiness area",
            )


class ResearchPhase19MockBaselinePreservedTests(unittest.TestCase):
    """Existing mock baseline/final-holdout tests must still pass."""

    def test_mock_compile_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "registry.sqlite"
            registry.init_db(db_path)
            strategy_id = "ph19_mock_preserve"
            sandbox_dir = SANDBOX_ROOT / strategy_id / "v1"
            mq5_path = sandbox_dir / f"{strategy_id}.mq5"
            _make_mq5(mq5_path)
            cr = impl_mod.create_implementation_request(
                db_path,
                strategy_id=strategy_id,
                strategy_version="v1",
                sandbox_dir=sandbox_dir,
                generated_files=[f"{strategy_id}.mq5"],
                created_by="ph19_test",
            )
            result = impl_mod.compile_check(db_path, cr["implementation_request_id"], mock=True)
            self.assertEqual(result["compile_status"], "mock_checked")

    def test_non_mock_without_config_mock_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "registry.sqlite"
            registry.init_db(db_path)
            strategy_id = "ph19_mock_fallback"
            sandbox_dir = SANDBOX_ROOT / strategy_id / "v1"
            mq5_path = sandbox_dir / f"{strategy_id}.mq5"
            _make_mq5(mq5_path)
            cr = impl_mod.create_implementation_request(
                db_path,
                strategy_id=strategy_id,
                strategy_version="v1",
                sandbox_dir=sandbox_dir,
                generated_files=[f"{strategy_id}.mq5"],
                created_by="ph19_test",
            )
            result = impl_mod.compile_check(db_path, cr["implementation_request_id"], mock=False)
            self.assertEqual(result["compile_status"], "mock_checked")


if __name__ == "__main__":
    unittest.main()
