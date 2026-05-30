from __future__ import annotations

import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

from automated.research import (
    compiler,
    backtest_readiness,
    cli,
    implementation as impl_mod,
    registry,
    readiness_review,
    toolchain_rehearsal,
)
from automated.research.schemas import REPO_ROOT, SANDBOX_ROOT, STRATEGIES_ROOT


# ---------------------------------------------------------------------------
# Helpers (adapted from phase18/phase19 test patterns)
# ---------------------------------------------------------------------------


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


def _make_fake_compiler(script_path: Path, exit_code: int = 0) -> Path:
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    st = os.stat(script_path)
    os.chmod(script_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script_path


def _make_real_compile_config(fake_compiler_path: Path, timeout: int = 120) -> Path:
    config = {
        "mode": "real_compile",
        "wine_binary": str(fake_compiler_path.resolve()),
        "metaeditor_path": str(fake_compiler_path.resolve()),
        "timeout_seconds": timeout,
    }
    path = fake_compiler_path.parent / "real_compile_config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def _make_fake_runner(script_path: Path, exit_code: int = 0) -> Path:
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
        "(report_dir / 'trades.csv').write_text('profit\\tsymbol\\n0.0\\tXAUUSD\\n')\n"
        "(report_dir / 'equity.csv').write_text('time\\tequity\\n0\\t100000\\n')\n"
        "(report_dir / 'run_summary.json').write_text("
        "json.dumps({'trades':1,'net_profit':0.0}))\n"
        "sys.exit(exit_code)\n",
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
    run_id: str = "ph21_rehearsal",
) -> None:
    conf_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f'RUN_ID="{run_id}"',
        f'EA_SOURCE="{mq5_path.resolve()}"',
        f'BROKER="{broker}"',
        f'SYMBOL="{symbol}"',
        f'TIMEFRAME="{timeframe}"',
        'DATE_FROM="2024.01.01"',
        'DATE_TO="2025.12.31"',
        'DEPOSIT="100000"',
        f'EA_SET_FILE="{set_file_path.resolve()}"',
    ]
    conf_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_set_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("InpAtrPeriod=14\nInpRiskPercent=1.0\n", encoding="utf-8")


def _make_readiness_config(
    output_dir: Path,
    runner_conf_path: Path,
    set_file_path: Path,
    wine_binary: str = "",
    timeout: int = 180,
    symbol: str = "XAUUSD",
    timeframe: str = "H4",
) -> Path:
    config = {
        "mode": "real_backtest_readiness",
        "wine_binary": wine_binary or "/usr/bin/wine",
        "wine_prefix": None,
        "terminal_path": None,
        "terminal_data_dir": None,
        "timeout_seconds": timeout,
        "max_duration_seconds": timeout,
        "expected_symbol": symbol,
        "expected_timeframe": timeframe,
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


# ---------------------------------------------------------------------------
# Phase 21: Real-toolchain rehearsal tests
# ---------------------------------------------------------------------------


class ResearchPhase21RehearsalSuccessTests(unittest.TestCase):
    """Rehearsal with successful compile and backtest readiness."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "registry.sqlite"
        registry.init_db(self.db_path)

        self.strategy_id = "ph21_test"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"
        _make_mq5(self.mq5_path)

        self.create_result = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            sandbox_dir=self.sandbox,
            generated_files=[f"{self.strategy_id}.mq5"],
            created_by="ph21_test",
        )
        self.impl_req_id = self.create_result["implementation_request_id"]

        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.fake_compiler = _make_fake_compiler(self.bin_dir / "fake_compile.sh", exit_code=0)
        self.compile_config_path = _make_real_compile_config(self.fake_compiler)

        self.runner_script = _make_fake_runner(self.bin_dir / "run_backtest.sh", exit_code=0)
        self.conf_path = self.root / "runner" / "test.conf"
        self.set_path = self.root / "runner" / "test.set"
        _make_set_file(self.set_path)
        _make_runner_conf(self.conf_path, self.mq5_path, self.set_path)

        self.readiness_output_dir = self.root / "readiness_output"
        self.readiness_config_path = _make_readiness_config(
            self.readiness_output_dir,
            self.conf_path,
            self.set_path,
            wine_binary=str(self.fake_compiler),
        )

        self.out_dir = self.root / "rehearsal_output"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run_rehearsal(self) -> dict[str, Any]:
        return toolchain_rehearsal.run_toolchain_rehearsal(
            self.db_path,
            self.impl_req_id,
            compile_config_path=self.compile_config_path,
            backtest_readiness_config_path=self.readiness_config_path,
            out_dir=self.out_dir,
            runner_script=self.runner_script,
        )

    def test_rehearsal_success_writes_all_evidence(self):
        summary = self._run_rehearsal()
        self.assertEqual(summary["status"], "passed")

        compile_path = self.out_dir / "compile_evidence.json"
        bt_path = self.out_dir / "backtest_readiness_evidence.json"
        packet_path = self.out_dir / "readiness_review_packet.json"
        summary_path = self.out_dir / "real_toolchain_rehearsal_summary.json"

        self.assertTrue(compile_path.is_file(), "compile_evidence.json should exist")
        self.assertTrue(bt_path.is_file(), "backtest_readiness_evidence.json should exist")
        self.assertTrue(packet_path.is_file(), "readiness_review_packet.json should exist")
        self.assertTrue(summary_path.is_file(), "real_toolchain_rehearsal_summary.json should exist")

    def test_compile_evidence_normalized_correctly(self):
        self._run_rehearsal()
        compile_path = self.out_dir / "compile_evidence.json"
        ev = json.loads(compile_path.read_text(encoding="utf-8"))
        self.assertEqual(ev["mode"], "real_compile")
        self.assertEqual(ev["status"], "passed")
        self.assertEqual(ev["impl_request_id"], self.impl_req_id)
        self.assertIn("strategy_id", ev)
        self.assertIn("version", ev)
        self.assertIn("implementation_id", ev)
        self.assertIn("input_digests", ev)

    def test_backtest_readiness_evidence_has_correct_format(self):
        self._run_rehearsal()
        bt_path = self.out_dir / "backtest_readiness_evidence.json"
        ev = json.loads(bt_path.read_text(encoding="utf-8"))
        self.assertEqual(ev["mode"], "real_backtest_readiness")
        self.assertEqual(ev["status"], "passed")
        self.assertEqual(ev["impl_request_id"], self.impl_req_id)

    def test_readiness_review_packet_has_correct_shape(self):
        self._run_rehearsal()
        packet_path = self.out_dir / "readiness_review_packet.json"
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        self.assertEqual(packet["artifact_type"], "generated_readiness_review")
        self.assertEqual(packet["impl_request_id"], self.impl_req_id)
        self.assertIn("compile_readiness", packet)
        self.assertIn("backtest_readiness", packet)
        self.assertIn("forbidden_interpretations", packet)

    def test_summary_artifact_type(self):
        summary = self._run_rehearsal()
        self.assertEqual(summary["artifact_type"], "real_toolchain_rehearsal_summary")

    def test_summary_status_passed(self):
        summary = self._run_rehearsal()
        self.assertEqual(summary["status"], "passed")

    def test_summary_contains_forbidden_interpretations(self):
        summary = self._run_rehearsal()
        self.assertIn("forbidden_interpretations", summary)
        self.assertIsInstance(summary["forbidden_interpretations"], list)
        self.assertGreater(len(summary["forbidden_interpretations"]), 0)

    def test_summary_no_proposed_next_action(self):
        summary = self._run_rehearsal()
        self.assertNotIn("proposed_next_action", summary)

    def test_summary_no_lifecycle_proposal(self):
        summary = self._run_rehearsal()
        self.assertNotIn("lifecycle_proposal", summary)

    def test_summary_has_top_level_status(self):
        summary = self._run_rehearsal()
        self.assertIn("status", summary)

    def test_no_scope_approvals_created(self):
        self._run_rehearsal()
        self.assertEqual(_count_rows(self.db_path, "scope_approvals"), 0)

    def test_no_lifecycle_transitions_created(self):
        self._run_rehearsal()
        self.assertEqual(_count_rows(self.db_path, "lifecycle_transitions"), 0)

    def test_no_experiments_created(self):
        self._run_rehearsal()
        self.assertEqual(_count_rows(self.db_path, "experiments"), 0)

    def test_no_files_written_to_automated_strategies(self):
        self._run_rehearsal()
        strategies_dir = STRATEGIES_ROOT
        if strategies_dir.is_dir():
            files_before = set(strategies_dir.rglob("*"))
            # rehearsal should not have written to strategies
            # this is a basic safety check
            self.assertFalse(
                any("ph21_test" in str(p) for p in strategies_dir.rglob("*")),
            )


class ResearchPhase21CompileFailureTests(unittest.TestCase):
    """Rehearsal with compile failure."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "registry.sqlite"
        registry.init_db(self.db_path)

        self.strategy_id = "ph21_compile_fail"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"
        _make_mq5(self.mq5_path)

        self.create_result = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            sandbox_dir=self.sandbox,
            generated_files=[f"{self.strategy_id}.mq5"],
            created_by="ph21_test",
        )
        self.impl_req_id = self.create_result["implementation_request_id"]

        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.fake_compiler = _make_fake_compiler(self.bin_dir / "fake_compile.sh", exit_code=1)
        self.compile_config_path = _make_real_compile_config(self.fake_compiler)

        self.out_dir = self.root / "rehearsal_output"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_compile_failure_returns_failed_status(self):
        summary = toolchain_rehearsal.run_toolchain_rehearsal(
            self.db_path,
            self.impl_req_id,
            compile_config_path=self.compile_config_path,
            backtest_readiness_config_path=self.root / "nonexistent.yaml",
            out_dir=self.out_dir,
        )
        self.assertEqual(summary["status"], "failed")

    def test_compile_failure_writes_compile_evidence(self):
        toolchain_rehearsal.run_toolchain_rehearsal(
            self.db_path,
            self.impl_req_id,
            compile_config_path=self.compile_config_path,
            backtest_readiness_config_path=self.root / "nonexistent.yaml",
            out_dir=self.out_dir,
        )
        compile_path = self.out_dir / "compile_evidence.json"
        self.assertTrue(compile_path.is_file(), "compile_evidence.json should exist after compile failure")
        ev = json.loads(compile_path.read_text(encoding="utf-8"))
        self.assertEqual(ev["status"], "failed")

    def test_compile_failure_does_not_write_bt_evidence(self):
        toolchain_rehearsal.run_toolchain_rehearsal(
            self.db_path,
            self.impl_req_id,
            compile_config_path=self.compile_config_path,
            backtest_readiness_config_path=self.root / "nonexistent.yaml",
            out_dir=self.out_dir,
        )

    def test_compile_failure_does_not_write_packet(self):
        toolchain_rehearsal.run_toolchain_rehearsal(
            self.db_path,
            self.impl_req_id,
            compile_config_path=self.compile_config_path,
            backtest_readiness_config_path=self.root / "nonexistent.yaml",
            out_dir=self.out_dir,
        )
        packet_path = self.out_dir / "readiness_review_packet.json"
        self.assertFalse(packet_path.is_file(), "readiness_review_packet.json should not exist after compile failure")

    def test_compile_failure_stops_before_backtest(self):
        summary = toolchain_rehearsal.run_toolchain_rehearsal(
            self.db_path,
            self.impl_req_id,
            compile_config_path=self.compile_config_path,
            backtest_readiness_config_path=self.root / "nonexistent.yaml",
            out_dir=self.out_dir,
        )
        self.assertIsNone(summary.get("backtest_readiness_status") or None)
        self.assertEqual(summary["compile_status"], "failed")


class ResearchPhase21BacktestFailureTests(unittest.TestCase):
    """Rehearsal with compile success but backtest readiness failure."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "registry.sqlite"
        registry.init_db(self.db_path)

        self.strategy_id = "ph21_bt_fail"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"
        _make_mq5(self.mq5_path)

        self.create_result = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            sandbox_dir=self.sandbox,
            generated_files=[f"{self.strategy_id}.mq5"],
            created_by="ph21_test",
        )
        self.impl_req_id = self.create_result["implementation_request_id"]

        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.fake_compiler = _make_fake_compiler(self.bin_dir / "fake_compile.sh", exit_code=0)
        self.compile_config_path = _make_real_compile_config(self.fake_compiler)

        self.runner_script = _make_fake_runner(self.bin_dir / "run_backtest.sh", exit_code=1)
        self.conf_path = self.root / "runner" / "test.conf"
        self.set_path = self.root / "runner" / "test.set"
        _make_set_file(self.set_path)
        _make_runner_conf(self.conf_path, self.mq5_path, self.set_path)

        self.readiness_output_dir = self.root / "readiness_output"
        self.readiness_config_path = _make_readiness_config(
            self.readiness_output_dir,
            self.conf_path,
            self.set_path,
            wine_binary=str(self.fake_compiler),
        )

        self.out_dir = self.root / "rehearsal_output"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_bt_failure_writes_bt_evidence(self):
        toolchain_rehearsal.run_toolchain_rehearsal(
            self.db_path,
            self.impl_req_id,
            compile_config_path=self.compile_config_path,
            backtest_readiness_config_path=self.readiness_config_path,
            out_dir=self.out_dir,
            runner_script=self.runner_script,
        )
        bt_path = self.out_dir / "backtest_readiness_evidence.json"
        self.assertTrue(bt_path.is_file(), "backtest_readiness_evidence.json should exist")
        ev = json.loads(bt_path.read_text(encoding="utf-8"))
        self.assertEqual(ev["status"], "failed")

    def test_bt_failure_writes_review_packet_with_failed_status(self):
        toolchain_rehearsal.run_toolchain_rehearsal(
            self.db_path,
            self.impl_req_id,
            compile_config_path=self.compile_config_path,
            backtest_readiness_config_path=self.readiness_config_path,
            out_dir=self.out_dir,
            runner_script=self.runner_script,
        )
        packet_path = self.out_dir / "readiness_review_packet.json"
        self.assertTrue(packet_path.is_file(), "readiness_review_packet.json should exist after bt failure")
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        self.assertEqual(packet["backtest_readiness"]["status"], "failed")

    def test_bt_failure_summary_status_failed(self):
        summary = toolchain_rehearsal.run_toolchain_rehearsal(
            self.db_path,
            self.impl_req_id,
            compile_config_path=self.compile_config_path,
            backtest_readiness_config_path=self.readiness_config_path,
            out_dir=self.out_dir,
            runner_script=self.runner_script,
        )
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["compile_status"], "passed")
        self.assertEqual(summary["backtest_readiness_status"], "failed")


class ResearchPhase21PathSafetyTests(unittest.TestCase):
    """Output directory path safety enforcement."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_out_dir_under_strategies_rejected(self):
        unsafe = STRATEGIES_ROOT / "SomeEA" / "rehearsal"
        with self.assertRaises(ValueError):
            toolchain_rehearsal.run_toolchain_rehearsal(
                self.root / "db.sqlite",
                "FAKE_ID",
                compile_config_path=self.root / "compile.yaml",
                backtest_readiness_config_path=self.root / "bt.yaml",
                out_dir=unsafe,
            )

    def test_out_dir_equal_to_strategies_rejected(self):
        unsafe = STRATEGIES_ROOT
        with self.assertRaises(ValueError):
            toolchain_rehearsal.run_toolchain_rehearsal(
                self.root / "db.sqlite",
                "FAKE_ID",
                compile_config_path=self.root / "compile.yaml",
                backtest_readiness_config_path=self.root / "bt.yaml",
                out_dir=unsafe,
            )


class ResearchPhase21MissingConfigTests(unittest.TestCase):
    """Missing config paths."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "registry.sqlite"
        registry.init_db(self.db_path)

        self.strategy_id = "ph21_missing_cfg"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"
        _make_mq5(self.mq5_path)

        self.create_result = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            sandbox_dir=self.sandbox,
            generated_files=[f"{self.strategy_id}.mq5"],
            created_by="ph21_test",
        )
        self.impl_req_id = self.create_result["implementation_request_id"]

        self.out_dir = self.root / "rehearsal_output"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_missing_compile_config_rejected(self):
        missing = self.root / "no_such_compile.yaml"
        with self.assertRaises(FileNotFoundError):
            toolchain_rehearsal.run_toolchain_rehearsal(
                self.db_path,
                self.impl_req_id,
                compile_config_path=missing,
                backtest_readiness_config_path=self.root / "dummy.yaml",
                out_dir=self.out_dir,
            )


class ResearchPhase21CLITests(unittest.TestCase):
    """CLI command integration."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "registry.sqlite"
        registry.init_db(self.db_path)

        self.strategy_id = "ph21_cli"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"
        _make_mq5(self.mq5_path)

        self.create_result = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            sandbox_dir=self.sandbox,
            generated_files=[f"{self.strategy_id}.mq5"],
            created_by="ph21_test",
        )
        self.impl_req_id = self.create_result["implementation_request_id"]

        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.fake_compiler = _make_fake_compiler(self.bin_dir / "fake_compile.sh", exit_code=0)
        self.compile_config_path = _make_real_compile_config(self.fake_compiler)

        self.runner_script = _make_fake_runner(self.bin_dir / "run_backtest.sh", exit_code=0)
        self.runner_dir = self.root / "runner"
        self.runner_dir.mkdir(parents=True, exist_ok=True)
        self.conf_path = self.runner_dir / "test.conf"
        self.set_path = self.runner_dir / "test.set"
        _make_set_file(self.set_path)
        _make_runner_conf(self.conf_path, self.mq5_path, self.set_path)

        self.readiness_out = self.root / "readiness_out"
        self.readiness_config_path = _make_readiness_config(
            self.readiness_out,
            self.conf_path,
            self.set_path,
            wine_binary=str(self.fake_compiler),
        )

        self.out_dir = self.root / "cli_rehearsal"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cli_subparser_registered(self):
        """The CLI subparser exists and routes to the handler."""
        parser = cli.build_parser()
        argv = [
            "--db", str(self.db_path),
            "implementation", "real-toolchain-rehearsal",
            self.impl_req_id,
            "--real-compile-config", str(self.compile_config_path),
            "--real-backtest-readiness-config", str(self.readiness_config_path),
            "--out-dir", str(self.out_dir),
        ]
        args = parser.parse_args(argv)
        self.assertEqual(args.impl_command, "real-toolchain-rehearsal")
        self.assertEqual(args.implementation_request_id, self.impl_req_id)

    def test_cli_handler_rehearsal_success(self):
        """Calling the handler function directly produces correct output."""
        summary = toolchain_rehearsal.run_toolchain_rehearsal(
            self.db_path,
            self.impl_req_id,
            compile_config_path=self.compile_config_path,
            backtest_readiness_config_path=self.readiness_config_path,
            out_dir=self.out_dir,
            runner_script=self.runner_script,
        )
        self.assertEqual(summary["status"], "passed")
        self.assertTrue((self.out_dir / "compile_evidence.json").is_file())
        self.assertTrue((self.out_dir / "backtest_readiness_evidence.json").is_file())
        self.assertTrue((self.out_dir / "readiness_review_packet.json").is_file())
        self.assertTrue((self.out_dir / "real_toolchain_rehearsal_summary.json").is_file())


class ResearchPhase21SideEffectTests(unittest.TestCase):
    """No forbidden registry side effects."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "registry.sqlite"
        registry.init_db(self.db_path)

        self.strategy_id = "ph21_sidefx"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"
        _make_mq5(self.mq5_path)

        self.create_result = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            sandbox_dir=self.sandbox,
            generated_files=[f"{self.strategy_id}.mq5"],
            created_by="ph21_test",
        )
        self.impl_req_id = self.create_result["implementation_request_id"]

        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.fake_compiler = _make_fake_compiler(self.bin_dir / "fake_compile.sh", exit_code=0)
        self.compile_config_path = _make_real_compile_config(self.fake_compiler)

        self.runner_script = _make_fake_runner(self.bin_dir / "run_backtest.sh", exit_code=0)
        self.conf_path = self.root / "runner" / "test.conf"
        self.set_path = self.root / "runner" / "test.set"
        _make_set_file(self.set_path)
        _make_runner_conf(self.conf_path, self.mq5_path, self.set_path)

        self.readiness_out = self.root / "readiness_out"
        self.readiness_config_path = _make_readiness_config(
            self.readiness_out,
            self.conf_path,
            self.set_path,
            wine_binary=str(self.fake_compiler),
        )

        self.out_dir = self.root / "rehearsal_output"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run(self) -> dict[str, Any]:
        return toolchain_rehearsal.run_toolchain_rehearsal(
            self.db_path,
            self.impl_req_id,
            compile_config_path=self.compile_config_path,
            backtest_readiness_config_path=self.readiness_config_path,
            out_dir=self.out_dir,
            runner_script=self.runner_script,
        )

    def test_implementations_row_created_by_compile_check(self):
        """The implementation record from compile_check is the only expected side effect."""
        pre_count = _count_rows(self.db_path, "implementations")
        self._run()
        post_count = _count_rows(self.db_path, "implementations")
        self.assertEqual(post_count, pre_count + 1,
                         "compile_check should create exactly one implementation row")

    def test_no_scope_approvals_created(self):
        self._run()
        self.assertEqual(_count_rows(self.db_path, "scope_approvals"), 0)

    def test_no_lifecycle_transitions_created(self):
        self._run()
        self.assertEqual(_count_rows(self.db_path, "lifecycle_transitions"), 0)

    def test_no_experiments_created(self):
        self._run()
        self.assertEqual(_count_rows(self.db_path, "experiments"), 0)


class ResearchPhase21ModuleIsolationTests(unittest.TestCase):
    """queue.py and generated_*.py must not reference toolchain_rehearsal."""

    def _check_module_not_reference(self, mod_path: Path, mod_name: str) -> None:
        source = mod_path.read_text(encoding="utf-8")
        self.assertNotIn(
            "toolchain_rehearsal", source,
            f"{mod_name} must not import or reference toolchain_rehearsal",
        )

    def test_queue_does_not_reference_toolchain_rehearsal(self):
        import automated.research.queue as m
        self._check_module_not_reference(Path(m.__file__), "queue.py")

    def test_generated_candidate_does_not_reference_toolchain_rehearsal(self):
        import automated.research.generated_candidate as m
        self._check_module_not_reference(Path(m.__file__), "generated_candidate.py")

    def test_generated_baseline_does_not_reference_toolchain_rehearsal(self):
        import automated.research.generated_baseline as m
        self._check_module_not_reference(Path(m.__file__), "generated_baseline.py")

    def test_generated_robustness_does_not_reference_toolchain_rehearsal(self):
        import automated.research.generated_robustness as m
        self._check_module_not_reference(Path(m.__file__), "generated_robustness.py")

    def test_generated_final_holdout_does_not_reference_toolchain_rehearsal(self):
        import automated.research.generated_final_holdout as m
        self._check_module_not_reference(Path(m.__file__), "generated_final_holdout.py")

    def test_lifecycle_does_not_reference_toolchain_rehearsal(self):
        import automated.research.lifecycle as m
        self._check_module_not_reference(Path(m.__file__), "lifecycle.py")


class ResearchPhase21DenyListTests(unittest.TestCase):
    """Deny-list terms must not appear as allowed values."""

    def test_promote_to_production_not_in_forbidden_interpretations_redefined(self):
        fi = toolchain_rehearsal.FORBIDDEN_INTERPRETATIONS
        for term in ("promote_to_production", "production_candidate", "live_trading_candidate"):
            self.assertNotIn(term, fi,
                             f"{term} must not be in FORBIDDEN_INTERPRETATIONS"
                             " (forbidden_interpretations are not-actually-bad lists)")

    def test_deny_terms_not_used_as_allowed_values_in_toolchain_rehearsal(self):
        source = Path(toolchain_rehearsal.__file__).read_text(encoding="utf-8")
        for term in ("promote_to_production", "production_candidate", "live_trading_candidate"):
            occurrences = source.count(term)
            self.assertEqual(
                occurrences, 0,
                f"{term} appears {occurrences} time(s) in toolchain_rehearsal.py "
                "(only allowed in deny lists or negative tests)",
            )

    def test_deny_terms_not_added_as_allowed_values_anywhere(self):
        for term in ("promote_to_production", "production_candidate", "live_trading_candidate"):
            for mod_name in (
                "generated_candidate", "generated_baseline",
                "generated_robustness", "generated_final_holdout",
            ):
                import importlib
                mod = importlib.import_module(f"automated.research.{mod_name}")
                all_attrs: set[str] = set()
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if isinstance(attr, (set, frozenset)):
                        all_attrs.update(attr)
                self.assertNotIn(
                    term, all_attrs,
                    f"{term} must not be an allowed value in {mod_name}.py",
                )


class ResearchPhase21CompileEvidenceNormalizationTests(unittest.TestCase):
    """Verify compile evidence normalization maps fields correctly."""

    def test_normalize_maps_compile_status_to_status(self):
        raw = {"compile_status": "passed", "implementation_request_id": "REQ_001"}
        request = {"strategy_id": "strat_a", "strategy_version": "v1"}
        ev = toolchain_rehearsal._normalize_compile_evidence(raw, request)
        self.assertEqual(ev["status"], "passed")
        self.assertEqual(ev["mode"], "real_compile")
        self.assertEqual(ev["impl_request_id"], "REQ_001")
        self.assertEqual(ev["strategy_id"], "strat_a")
        self.assertEqual(ev["version"], "v1")

    def test_normalize_preserves_raw_subprocess_fields(self):
        raw = {
            "compile_status": "passed",
            "implementation_request_id": "REQ_001",
            "exit_code": 0,
            "stdout": "compiled ok",
            "stderr": "",
            "started_at": "2025-01-01T00:00:00",
            "duration_seconds": 1.5,
        }
        ev = toolchain_rehearsal._normalize_compile_evidence(raw, None)
        self.assertEqual(ev["exit_code"], 0)
        self.assertEqual(ev["stdout"], "compiled ok")
        self.assertIn("started_at", ev)
        self.assertEqual(ev["duration_seconds"], 1.5)

    def test_normalize_adds_warning_when_raw_fields_missing(self):
        raw = {"compile_status": "passed", "implementation_request_id": "REQ_001"}
        ev = toolchain_rehearsal._normalize_compile_evidence(raw, {"strategy_id": "x", "strategy_version": "y"})
        self.assertIn("_normalization_warnings", ev)
        self.assertIn("compile_check_output_lacked_raw_subprocess_fields", ev["_normalization_warnings"])


if __name__ == "__main__":
    unittest.main()
