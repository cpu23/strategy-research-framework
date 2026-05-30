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

from automated.research import (
    backtest_readiness,
    cli,
    compiler,
    implementation as impl_mod,
    readiness_review,
    registry,
)
from automated.research.hashing import file_sha256
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
        "(report_dir / 'run_summary.json').write_text(json.dumps({'trades':1,'net_profit':0.0}))\n"
        "(report_dir / 'terminal_run.log').write_text('backtest complete\\n')\n"
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
    run_id: str = "ph20_readiness",
    strategy_id: str = "ph20_test",
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


def _make_set_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("InpAtrPeriod=14\nInpRiskPercent=1.0\n", encoding="utf-8")


def _make_readiness_config(
    output_dir: Path,
    runner_conf_path: Path,
    set_file_path: Path,
    wine_binary: str = "/usr/bin/wine",
    timeout: int = 180,
) -> Path:
    config = {
        "mode": "real_backtest_readiness",
        "wine_binary": wine_binary,
        "wine_prefix": None,
        "terminal_path": None,
        "terminal_data_dir": None,
        "timeout_seconds": timeout,
        "max_duration_seconds": timeout,
        "expected_symbol": "XAUUSD",
        "expected_timeframe": "H4",
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


# =====================================================================
# WP 20B — Digest tests
# =====================================================================


class ResearchPhase20DigestCompileTests(unittest.TestCase):
    """Real compile evidence includes SHA-256 digests."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.mq5_path = self.temp_path / "test_strategy.mq5"
        _make_mq5(self.mq5_path)
        self.expected_mq5_sha256 = file_sha256(self.mq5_path)
        self.fake_compiler = self.temp_path / "fake_compiler.py"
        _make_fake_compiler(self.fake_compiler, exit_code=0)
        self.config_path = _make_real_compile_config(self.fake_compiler)
        self.config = compiler.load_real_compile_config(self.config_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_real_compile_evidence_includes_mq5_digest(self):
        result = compiler.run_real_compile(
            self.mq5_path, self.config,
            config_path=self.config_path,
        )
        input_digests = result.get("input_digests", {})
        self.assertIn("generated_mq5", input_digests)
        self.assertEqual(input_digests["generated_mq5"], self.expected_mq5_sha256)

    def test_real_compile_evidence_includes_config_digest(self):
        result = compiler.run_real_compile(
            self.mq5_path, self.config,
            config_path=self.config_path,
        )
        input_digests = result.get("input_digests", {})
        self.assertIn("compile_config", input_digests)
        expected_config_sha256 = file_sha256(self.config_path)
        self.assertEqual(input_digests["compile_config"], expected_config_sha256)

    def test_real_compile_evidence_digest_no_config_path(self):
        result = compiler.run_real_compile(self.mq5_path, self.config)
        input_digests = result.get("input_digests", {})
        self.assertIn("generated_mq5", input_digests)
        self.assertNotIn("compile_config", input_digests)


class ResearchPhase20DigestBacktestReadinessTests(unittest.TestCase):
    """Backtest readiness evidence includes SHA-256 digests."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.db_path = self.temp_path / "registry.sqlite"
        registry.init_db(self.db_path)
        self.strategy_id = "ph20_bt_digest"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"
        _make_mq5(self.mq5_path)
        self.create_result = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            sandbox_dir=self.sandbox,
            generated_files=[f"{self.strategy_id}.mq5"],
            created_by="ph20_test",
        )
        self.impl_req_id = self.create_result["implementation_request_id"]
        impl_mod.compile_check(self.db_path, self.impl_req_id, mock=True)

        self.runner_dir = self.temp_path / "runner"
        self.runner_dir.mkdir(parents=True, exist_ok=True)
        self.fake_runner = self.runner_dir / "run_backtest.sh"
        self.conf_path = self.runner_dir / f"{self.strategy_id}.conf"
        self.set_path = self.runner_dir / f"{self.strategy_id}.set"
        _make_set_file(self.set_path)
        _make_runner_conf(self.conf_path, self.mq5_path, self.set_path)
        self.output_dir = self.temp_path / "readiness_output"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_readiness_evidence_includes_mq5_digest(self):
        _make_fake_runner(self.fake_runner, exit_code=0)
        config_path = _make_readiness_config(
            self.output_dir, self.conf_path, self.set_path,
        )
        config = backtest_readiness.load_real_backtest_readiness_config(config_path)
        result = backtest_readiness.run_real_backtest_readiness(
            self.db_path, self.impl_req_id, config,
            runner_script=self.fake_runner,
            readiness_config_path=config_path,
        )
        digests = result.get("input_digests", {})
        self.assertIn("generated_mq5", digests)
        self.assertEqual(digests["generated_mq5"], file_sha256(self.mq5_path))

    def test_readiness_evidence_includes_conf_digest(self):
        _make_fake_runner(self.fake_runner, exit_code=0)
        config_path = _make_readiness_config(
            self.output_dir, self.conf_path, self.set_path,
        )
        config = backtest_readiness.load_real_backtest_readiness_config(config_path)
        result = backtest_readiness.run_real_backtest_readiness(
            self.db_path, self.impl_req_id, config,
            runner_script=self.fake_runner,
            readiness_config_path=config_path,
        )
        digests = result.get("input_digests", {})
        self.assertIn("generated_conf", digests)
        self.assertEqual(digests["generated_conf"], file_sha256(self.conf_path))

    def test_readiness_evidence_includes_set_digest(self):
        _make_fake_runner(self.fake_runner, exit_code=0)
        config_path = _make_readiness_config(
            self.output_dir, self.conf_path, self.set_path,
        )
        config = backtest_readiness.load_real_backtest_readiness_config(config_path)
        result = backtest_readiness.run_real_backtest_readiness(
            self.db_path, self.impl_req_id, config,
            runner_script=self.fake_runner,
            readiness_config_path=config_path,
        )
        digests = result.get("input_digests", {})
        self.assertIn("generated_set", digests)
        self.assertEqual(digests["generated_set"], file_sha256(self.set_path))

    def test_readiness_evidence_includes_config_digest(self):
        _make_fake_runner(self.fake_runner, exit_code=0)
        config_path = _make_readiness_config(
            self.output_dir, self.conf_path, self.set_path,
        )
        config = backtest_readiness.load_real_backtest_readiness_config(config_path)
        result = backtest_readiness.run_real_backtest_readiness(
            self.db_path, self.impl_req_id, config,
            runner_script=self.fake_runner,
            readiness_config_path=config_path,
        )
        digests = result.get("input_digests", {})
        self.assertIn("readiness_config", digests)
        self.assertEqual(digests["readiness_config"], file_sha256(config_path))

    def test_readiness_evidence_no_config_path(self):
        _make_fake_runner(self.fake_runner, exit_code=0)
        config_path = _make_readiness_config(
            self.output_dir, self.conf_path, self.set_path,
        )
        config = backtest_readiness.load_real_backtest_readiness_config(config_path)
        result = backtest_readiness.run_real_backtest_readiness(
            self.db_path, self.impl_req_id, config,
            runner_script=self.fake_runner,
        )
        digests = result.get("input_digests", {})
        self.assertIn("generated_mq5", digests)
        self.assertIn("generated_conf", digests)
        self.assertIn("generated_set", digests)
        self.assertNotIn("readiness_config", digests)


# =====================================================================
# WP 20C — Readiness review packet tests
# =====================================================================


class ResearchPhase20ReadinessReviewPacketTests(unittest.TestCase):
    """Readiness review packet built from explicit evidence files."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        self.compile_evidence = {
            "mode": "real_compile",
            "status": "passed",
            "errors": [],
            "impl_request_id": "IMPL_REQ_20260513_PH20TEST",
            "implementation_id": "IMPL_20260513_PH20TEST",
            "strategy_id": "ph20_review",
            "version": "v1",
            "input_digests": {
                "generated_mq5": "a" * 64,
                "compile_config": "b" * 64,
            },
        }
        self.compile_evidence_path = self.temp_path / "compile_evidence.json"
        self.compile_evidence_path.write_text(
            json.dumps(self.compile_evidence), encoding="utf-8",
        )

        self.bt_evidence = {
            "mode": "real_backtest_readiness",
            "status": "passed",
            "errors": [],
            "impl_request_id": "IMPL_REQ_20260513_PH20TEST",
            "implementation_id": "IMPL_20260513_PH20TEST",
            "strategy_id": "ph20_review",
            "version": "v1",
            "runner_conf_path": "/tmp/test.conf",
            "set_file_path": "/tmp/test.set",
            "report_paths": [],
            "log_paths": [],
            "input_digests": {
                "generated_mq5": "a" * 64,
                "generated_conf": "c" * 64,
                "generated_set": "d" * 64,
                "readiness_config": "e" * 64,
            },
        }
        self.bt_evidence_path = self.temp_path / "bt_evidence.json"
        self.bt_evidence_path.write_text(
            json.dumps(self.bt_evidence), encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_packet_built_from_explicit_evidence_files(self):
        packet = readiness_review.build_readiness_review_packet(
            compile_evidence_path=self.compile_evidence_path,
            backtest_readiness_evidence_path=self.bt_evidence_path,
            impl_request_id="IMPL_REQ_20260513_PH20TEST",
        )
        self.assertEqual(packet["compile_readiness"]["status"], "passed")
        self.assertEqual(packet["backtest_readiness"]["status"], "passed")
        self.assertIn("generated_mq5", packet["compile_readiness"]["input_digests"])
        self.assertIn("generated_mq5", packet["backtest_readiness"]["input_digests"])
        self.assertIn("generated_conf", packet["backtest_readiness"]["input_digests"])
        self.assertIn("generated_set", packet["backtest_readiness"]["input_digests"])

    def test_packet_artifact_type(self):
        packet = readiness_review.build_readiness_review_packet(
            compile_evidence_path=self.compile_evidence_path,
            backtest_readiness_evidence_path=self.bt_evidence_path,
        )
        self.assertEqual(packet["artifact_type"], "generated_readiness_review")

    def test_packet_proposed_next_action_is_safe_value(self):
        allowed = readiness_review.ALLOWED_MANUAL_ACTIONS
        packet = readiness_review.build_readiness_review_packet(
            compile_evidence_path=self.compile_evidence_path,
            backtest_readiness_evidence_path=self.bt_evidence_path,
        )
        self.assertIn(packet["proposed_next_manual_action"], allowed)

    def test_packet_does_not_contain_forbidden_action_values(self):
        packet = readiness_review.build_readiness_review_packet(
            compile_evidence_path=self.compile_evidence_path,
            backtest_readiness_evidence_path=self.bt_evidence_path,
        )
        for forbidden in readiness_review.FORBIDDEN_ACTION_VALUES:
            self.assertNotIn(forbidden, str(packet.get("proposed_next_manual_action", "")))
        self.assertNotIn("proposed_next_action", packet)
        self.assertNotIn("lifecycle_proposal", packet)

    def test_packet_contains_forbidden_interpretations(self):
        packet = readiness_review.build_readiness_review_packet(
            compile_evidence_path=self.compile_evidence_path,
            backtest_readiness_evidence_path=self.bt_evidence_path,
        )
        self.assertIn("forbidden_interpretations", packet)
        self.assertTrue(len(packet["forbidden_interpretations"]) > 0)
        for entry in packet["forbidden_interpretations"]:
            self.assertTrue(entry.startswith("not_"))

    def test_packet_output_written_to_file(self):
        out_path = self.temp_path / "packet.json"
        packet = readiness_review.build_readiness_review_packet(
            compile_evidence_path=self.compile_evidence_path,
            backtest_readiness_evidence_path=self.bt_evidence_path,
            output_path=out_path,
        )
        self.assertTrue(out_path.is_file())
        loaded = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertEqual(loaded["artifact_type"], "generated_readiness_review")

    def test_packet_rejects_unsafe_out_path(self):
        unsafe = REPO_ROOT / "automated" / "strategies" / "packet.json"
        with self.assertRaises(ValueError):
            readiness_review.build_readiness_review_packet(
                compile_evidence_path=self.compile_evidence_path,
                backtest_readiness_evidence_path=self.bt_evidence_path,
                output_path=unsafe,
            )

    def test_packet_rejects_mismatched_impl_request_id(self):
        packet = readiness_review.build_readiness_review_packet(
            compile_evidence_path=self.compile_evidence_path,
            backtest_readiness_evidence_path=self.bt_evidence_path,
            impl_request_id="DIFFERENT_ID",
        )
        self.assertTrue(
            any("impl_request_id" in w for w in packet.get("warnings", [])),
            msg="Expected warning about mismatched impl_request_id",
        )

    def test_packet_rejects_mismatched_strategy_id(self):
        bt_different = dict(self.bt_evidence)
        bt_different["strategy_id"] = "other_strategy"
        bt_path = self.temp_path / "bt_mismatch.json"
        bt_path.write_text(json.dumps(bt_different), encoding="utf-8")

        packet = readiness_review.build_readiness_review_packet(
            compile_evidence_path=self.compile_evidence_path,
            backtest_readiness_evidence_path=bt_path,
        )
        self.assertTrue(
            any("strategy_id" in w for w in packet.get("warnings", [])),
            msg="Expected warning about mismatched strategy_id",
        )

    def test_packet_rejects_mismatched_version(self):
        bt_different = dict(self.bt_evidence)
        bt_different["version"] = "v2"
        bt_path = self.temp_path / "bt_ver_mismatch.json"
        bt_path.write_text(json.dumps(bt_different), encoding="utf-8")

        packet = readiness_review.build_readiness_review_packet(
            compile_evidence_path=self.compile_evidence_path,
            backtest_readiness_evidence_path=bt_path,
        )
        self.assertTrue(
            any("version" in w for w in packet.get("warnings", [])),
            msg="Expected warning about mismatched version",
        )

    def test_packet_rejects_invalid_compile_evidence_mode(self):
        bad = dict(self.compile_evidence)
        bad["mode"] = "wrong_mode"
        bad_path = self.temp_path / "bad_compile.json"
        bad_path.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaises(ValueError):
            readiness_review.build_readiness_review_packet(
                compile_evidence_path=bad_path,
                backtest_readiness_evidence_path=self.bt_evidence_path,
            )


# =====================================================================
# WP 20D — CLI tests
# =====================================================================


class ResearchPhase20CLITests(unittest.TestCase):
    """CLI-level tests for readiness-review."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.impl_req_id = "IMPL_REQ_20260513_PH20CLI"

        self.compile_evidence = {
            "mode": "real_compile",
            "status": "passed",
            "errors": [],
            "impl_request_id": self.impl_req_id,
            "implementation_id": "IMPL_20260513_CLI",
            "strategy_id": "ph20_cli",
            "version": "v1",
            "input_digests": {"generated_mq5": "a" * 64},
        }
        self.compile_path = self.temp_path / "compile.json"
        self.compile_path.write_text(json.dumps(self.compile_evidence), encoding="utf-8")

        self.bt_evidence = {
            "mode": "real_backtest_readiness",
            "status": "passed",
            "errors": [],
            "impl_request_id": self.impl_req_id,
            "implementation_id": "IMPL_20260513_CLI",
            "strategy_id": "ph20_cli",
            "version": "v1",
            "input_digests": {"generated_mq5": "a" * 64},
        }
        self.bt_path = self.temp_path / "bt.json"
        self.bt_path.write_text(json.dumps(self.bt_evidence), encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cli_readiness_review_stdout(self):
        out = StringIO()
        with redirect_stdout(out):
            rc = cli.main([
                "implementation", "readiness-review",
                self.impl_req_id,
                "--compile-evidence", str(self.compile_path),
                "--backtest-readiness-evidence", str(self.bt_path),
            ])
        self.assertEqual(rc, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["artifact_type"], "generated_readiness_review")

    def test_cli_readiness_review_out_file(self):
        out_path = self.temp_path / "packet.json"
        out = StringIO()
        with redirect_stdout(out):
            rc = cli.main([
                "implementation", "readiness-review",
                self.impl_req_id,
                "--compile-evidence", str(self.compile_path),
                "--backtest-readiness-evidence", str(self.bt_path),
                "--out", str(out_path),
            ])
        self.assertEqual(rc, 0)
        result = json.loads(out.getvalue())
        self.assertEqual(result["status"], "written")
        self.assertTrue(out_path.is_file())

    def test_cli_rejects_unsafe_out_path(self):
        unsafe = REPO_ROOT / "automated" / "strategies" / "packet.json"
        out = StringIO()
        with redirect_stdout(out):
            rc = cli.main([
                "implementation", "readiness-review",
                self.impl_req_id,
                "--compile-evidence", str(self.compile_path),
                "--backtest-readiness-evidence", str(self.bt_path),
                "--out", str(unsafe),
            ])
        self.assertEqual(rc, 1)
        data = json.loads(out.getvalue())
        self.assertIn("error", data)

    def test_cli_rejects_missing_compile_evidence(self):
        missing = self.temp_path / "nonexistent.json"
        out = StringIO()
        with redirect_stdout(out):
            rc = cli.main([
                "implementation", "readiness-review",
                self.impl_req_id,
                "--compile-evidence", str(missing),
                "--backtest-readiness-evidence", str(self.bt_path),
            ])
        self.assertEqual(rc, 1)
        data = json.loads(out.getvalue())
        self.assertIn("not found", data.get("error", ""))


# =====================================================================
# WP 20C — Side-effect tests
# =====================================================================


class ResearchPhase20SideEffectTests(unittest.TestCase):
    """Readiness review creates no side effects."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        self.compile_evidence = {
            "mode": "real_compile",
            "status": "passed",
            "errors": [],
            "impl_request_id": "IMPL_REQ_SIDE_EFFECT",
            "implementation_id": "IMPL_SIDE_EFFECT",
            "strategy_id": "ph20_side",
            "version": "v1",
            "input_digests": {"generated_mq5": "a" * 64},
        }
        self.compile_path = self.temp_path / "compile.json"
        self.compile_path.write_text(json.dumps(self.compile_evidence), encoding="utf-8")

        self.bt_evidence = {
            "mode": "real_backtest_readiness",
            "status": "passed",
            "errors": [],
            "impl_request_id": "IMPL_REQ_SIDE_EFFECT",
            "implementation_id": "IMPL_SIDE_EFFECT",
            "strategy_id": "ph20_side",
            "version": "v1",
            "input_digests": {"generated_mq5": "a" * 64},
        }
        self.bt_path = self.temp_path / "bt.json"
        self.bt_path.write_text(json.dumps(self.bt_evidence), encoding="utf-8")

        self.db_path = self.temp_path / "registry.sqlite"
        registry.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_readiness_review_creates_no_scope_approvals(self):
        readiness_review.build_readiness_review_packet(
            compile_evidence_path=self.compile_path,
            backtest_readiness_evidence_path=self.bt_path,
        )
        self.assertEqual(_count_rows(self.db_path, "scope_approvals"), 0)

    def test_readiness_review_creates_no_lifecycle_transitions(self):
        readiness_review.build_readiness_review_packet(
            compile_evidence_path=self.compile_path,
            backtest_readiness_evidence_path=self.bt_path,
        )
        self.assertEqual(_count_rows(self.db_path, "lifecycle_transitions"), 0)

    def test_readiness_review_creates_no_experiments(self):
        readiness_review.build_readiness_review_packet(
            compile_evidence_path=self.compile_path,
            backtest_readiness_evidence_path=self.bt_path,
        )
        self.assertEqual(_count_rows(self.db_path, "experiments"), 0)


# =====================================================================
# WP 20C — Module isolation tests
# =====================================================================


class ResearchPhase20ModuleIsolationTests(unittest.TestCase):
    """queue.py and generated_candidate.py must not reference readiness_review."""

    def _check_module_not_imported(self, mod_path: Path, mod_name: str) -> None:
        source = mod_path.read_text(encoding="utf-8")
        self.assertNotIn(
            "readiness_review", source,
            f"{mod_name} must not import or reference readiness_review",
        )

    def test_queue_does_not_reference_readiness_review(self):
        import automated.research.queue as queue_mod
        self._check_module_not_imported(Path(queue_mod.__file__), "queue.py")

    def test_generated_candidate_does_not_reference_readiness_review(self):
        import automated.research.generated_candidate as m
        self._check_module_not_imported(Path(m.__file__), "generated_candidate.py")

    def test_generated_baseline_does_not_reference_readiness_review(self):
        import automated.research.generated_baseline as m
        self._check_module_not_imported(Path(m.__file__), "generated_baseline.py")

    def test_generated_robustness_does_not_reference_readiness_review(self):
        import automated.research.generated_robustness as m
        self._check_module_not_imported(Path(m.__file__), "generated_robustness.py")

    def test_generated_final_holdout_does_not_reference_readiness_review(self):
        import automated.research.generated_final_holdout as m
        self._check_module_not_imported(Path(m.__file__), "generated_final_holdout.py")

    def test_candidate_decision_code_does_not_import_readiness_review(self):
        import automated.research.generated_candidate as m
        source = Path(m.__file__).read_text(encoding="utf-8")
        self.assertNotIn("readiness_review", source)

    def test_queue_code_does_not_import_readiness_review(self):
        import automated.research.queue as m
        source = Path(m.__file__).read_text(encoding="utf-8")
        self.assertNotIn("readiness_review", source)


# =====================================================================
# WP 20C — Deny-list tests
# =====================================================================


class ResearchPhase20DenyListTests(unittest.TestCase):
    """Deny-list terms must not appear as allowed values."""

    def test_deny_terms_not_in_allowed_actions(self):
        allowed = readiness_review.ALLOWED_MANUAL_ACTIONS
        for term in ("promote_to_production", "production_candidate", "live_trading_candidate"):
            self.assertNotIn(term, allowed,
                             f"{term} must not be in ALLOWED_MANUAL_ACTIONS")

    def test_deny_terms_not_in_cli_readiness_review_command_source(self):
        import automated.research.cli as cli_mod
        source = Path(cli_mod.__file__).read_text(encoding="utf-8")
        for term in ("promote_to_production", "production_candidate", "live_trading_candidate"):
            self.assertNotIn(term, source,
                              f"{term} must not appear in cli.py readiness-review area")

    def test_deny_terms_not_added_as_allowed_values_anywhere(self):
        for term in ("promote_to_production", "production_candidate", "live_trading_candidate"):
            self.assertNotIn(term, readiness_review.ALLOWED_MANUAL_ACTIONS,
                             f"{term} must not be in ALLOWED_MANUAL_ACTIONS")
        for mod_name in ("generated_candidate", "generated_baseline", "generated_robustness", "generated_final_holdout"):
            import importlib
            mod = importlib.import_module(f"automated.research.{mod_name}")
            all_attrs = set()
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if isinstance(attr, (set, frozenset)):
                    all_attrs.update(attr)
            for term in ("promote_to_production", "production_candidate", "live_trading_candidate"):
                self.assertNotIn(term, all_attrs,
                                 f"{term} must not be an allowed value in {mod_name}.py")


if __name__ == "__main__":
    unittest.main()
