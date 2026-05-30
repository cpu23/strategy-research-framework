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

from automated.research import cli, compiler, implementation as impl_mod, registry
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


def _make_fake_timeout_compiler(script_path: Path) -> Path:
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


def _count_rows(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return row[0] if row else 0
    except Exception:
        return 0
    finally:
        conn.close()


class ResearchPhase18FakeCompileTests(unittest.TestCase):
    """Real compile tests using a fake compiler executable."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.strategy_id = "ph18_fake_compile"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"
        _make_mq5(self.mq5_path)
        self.create_result = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            sandbox_dir=self.sandbox,
            generated_files=[f"{self.strategy_id}.mq5"],
            created_by="ph18_test",
        )
        self.impl_req_id = self.create_result["implementation_request_id"]
        self.fake_compiler = Path(self.temp_dir.name) / "fake_compiler.py"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _real_compile(self, fake_exit_code: int = 0) -> dict[str, Any]:
        compiler_path = _make_fake_compiler(self.fake_compiler, exit_code=fake_exit_code)
        config_path = _make_real_compile_config(compiler_path)
        config = compiler.load_real_compile_config(config_path)
        return impl_mod.compile_check(
            self.db_path, self.impl_req_id, mock=False, real_compile_config=config
        )

    def _check_no_side_effects(self) -> None:
        self.assertEqual(
            _count_rows(self.db_path, "scope_approvals"), 0,
            "Real compile must not create approval records",
        )
        self.assertEqual(
            _count_rows(self.db_path, "lifecycle_transitions"), 0,
            "Real compile must not create lifecycle transitions",
        )
        self.assertEqual(
            _count_rows(self.db_path, "experiments"), 0,
            "Real compile must not create experiments",
        )

    def test_real_compile_success_creates_passed_evidence(self):
        result = self._real_compile(fake_exit_code=0)
        self.assertEqual(result["compile_status"], "passed")
        self.assertIn("implementation_id", result)
        self.assertIn("generated_mq5_path", result)
        self._check_no_side_effects()

    def test_real_compile_failure_creates_failed_evidence(self):
        result = self._real_compile(fake_exit_code=1)
        self.assertEqual(result["compile_status"], "failed")
        self.assertIn("implementation_id", result)
        self._check_no_side_effects()

    def test_real_compile_missing_compiler_rejected(self):
        missing = Path(self.temp_dir.name) / "nonexistent_compiler.sh"
        config_data = {
            "mode": "real_compile",
            "wine_binary": str(missing),
            "wine_prefix": None,
            "metaeditor_path": str(missing),
            "timeout_seconds": 120,
        }
        config_path = Path(self.temp_dir.name) / "missing_config.yaml"
        config_path.write_text(yaml.safe_dump(config_data), encoding="utf-8")
        config = compiler.load_real_compile_config(config_path)
        result = impl_mod.compile_check(
            self.db_path, self.impl_req_id, mock=False, real_compile_config=config
        )
        self.assertEqual(result["compile_status"], "failed")
        self.assertTrue(
            any("not found" in e for e in result.get("errors", [])),
            msg=f"Expected binary-not-found error, got: {result.get('errors')}",
        )
        self._check_no_side_effects()

    def test_timeout_path_returns_failed(self):
        compiler_path = _make_fake_timeout_compiler(self.fake_compiler)
        config_data = {
            "mode": "real_compile",
            "wine_binary": str(compiler_path.resolve()),
            "wine_prefix": None,
            "metaeditor_path": str(compiler_path.resolve()),
            "timeout_seconds": 1,
        }
        config_path = Path(self.temp_dir.name) / "timeout_config.yaml"
        config_path.write_text(yaml.safe_dump(config_data), encoding="utf-8")
        config = compiler.load_real_compile_config(config_path)
        result = impl_mod.compile_check(
            self.db_path, self.impl_req_id, mock=False, real_compile_config=config
        )
        self.assertEqual(result["compile_status"], "failed")
        self._check_no_side_effects()


class ResearchPhase18UnsafePathTests(unittest.TestCase):
    """Real compile must reject unsafe paths via run_real_compile."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.fake_compiler = Path(self.temp_dir.name) / "fake_compiler.py"
        _make_fake_compiler(self.fake_compiler, exit_code=0)
        self.config = compiler.RealCompileConfig(
            mode="real_compile",
            wine_binary=str(self.fake_compiler.resolve()),
            wine_prefix=None,
            metaeditor_path=str(self.fake_compiler.resolve()),
            timeout_seconds=120,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_production_path_rejected(self):
        prod_path = REPO_ROOT / "automated" / "strategies" / "SomeEA" / "v1"
        prod_path.mkdir(parents=True, exist_ok=True)
        mq5 = prod_path / "SomeEA.mq5"
        _make_mq5(mq5)
        result = compiler.run_real_compile(mq5, self.config)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(
            any("must be under" in e.lower() or "forbidden" in e.lower() for e in result.get("errors", [])),
            msg=f"Expected path rejection, got: {result.get('errors')}",
        )

    def test_non_sandbox_path_rejected(self):
        outside = Path(self.temp_dir.name) / "outside_sandbox" / "v1"
        outside.mkdir(parents=True, exist_ok=True)
        mq5 = outside / "Outside.mq5"
        _make_mq5(mq5)
        result = compiler.run_real_compile(mq5, self.config)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(
            any("must be under" in e.lower() for e in result.get("errors", [])),
            msg=f"Expected sandbox path rejection, got: {result.get('errors')}",
        )


class ResearchPhase18MockCompilePreservedTests(unittest.TestCase):
    """Existing mock compile behavior must be unchanged."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.strategy_id = "ph18_mock_preserved"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"
        _make_mq5(self.mq5_path)
        self.create_result = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            sandbox_dir=self.sandbox,
            generated_files=[f"{self.strategy_id}.mq5"],
            created_by="ph18_test",
        )
        self.impl_req_id = self.create_result["implementation_request_id"]

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_mock_compile_returns_mock_checked(self):
        result = impl_mod.compile_check(self.db_path, self.impl_req_id, mock=True)
        self.assertEqual(result["compile_status"], "mock_checked")

    def test_non_mock_without_config_returns_mock_checked_fallback(self):
        result = impl_mod.compile_check(self.db_path, self.impl_req_id, mock=False)
        self.assertEqual(result["compile_status"], "mock_checked")
        self.assertTrue(
            any("--real-compile-config" in e for e in result.get("errors", [])),
            msg="Expected message about --real-compile-config",
        )

    def test_compile_check_ignores_real_config_when_mock_is_true(self):
        result = impl_mod.compile_check(
            self.db_path, self.impl_req_id, mock=True, real_compile_config=None
        )
        self.assertEqual(result["compile_status"], "mock_checked")


class ResearchPhase18CLITests(unittest.TestCase):
    """CLI-level tests for --real-compile-config and mutual exclusion."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.strategy_id = "ph18_cli"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"
        _make_mq5(self.mq5_path)
        self.create_result = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            sandbox_dir=self.sandbox,
            generated_files=[f"{self.strategy_id}.mq5"],
            created_by="ph18_test",
        )
        self.impl_req_id = self.create_result["implementation_request_id"]

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cli_mock_flag_works(self):
        out = StringIO()
        with redirect_stdout(out):
            rc = cli.main([
                "--db", str(self.db_path),
                "implementation", "compile-check",
                "--mock",
                self.impl_req_id,
            ])
        self.assertEqual(rc, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["compile_status"], "mock_checked")

    def test_cli_mock_and_real_config_mutually_exclusive(self):
        fake_compiler = Path(self.temp_dir.name) / "fake.py"
        _make_fake_compiler(fake_compiler, exit_code=0)
        config_path = _make_real_compile_config(fake_compiler)
        out = StringIO()
        with redirect_stdout(out):
            rc = cli.main([
                "--db", str(self.db_path),
                "implementation", "compile-check",
                "--mock",
                "--real-compile-config", str(config_path),
                self.impl_req_id,
            ])
        self.assertEqual(rc, 1)
        data = json.loads(out.getvalue())
        self.assertIn("mutually exclusive", data.get("error", ""))

    def test_cli_real_compile_config_success(self):
        fake_compiler = Path(self.temp_dir.name) / "fake_success.py"
        _make_fake_compiler(fake_compiler, exit_code=0)
        config_path = _make_real_compile_config(fake_compiler)
        out = StringIO()
        with redirect_stdout(out):
            rc = cli.main([
                "--db", str(self.db_path),
                "implementation", "compile-check",
                "--real-compile-config", str(config_path),
                self.impl_req_id,
            ])
        self.assertEqual(rc, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["compile_status"], "passed")

    def test_cli_real_compile_config_failure(self):
        fake_compiler = Path(self.temp_dir.name) / "fake_fail.py"
        _make_fake_compiler(fake_compiler, exit_code=1)
        config_path = _make_real_compile_config(fake_compiler)
        out = StringIO()
        with redirect_stdout(out):
            rc = cli.main([
                "--db", str(self.db_path),
                "implementation", "compile-check",
                "--real-compile-config", str(config_path),
                self.impl_req_id,
            ])
        self.assertEqual(rc, 1)
        data = json.loads(out.getvalue())
        self.assertEqual(data["compile_status"], "failed")

    def test_cli_missing_config_rejected(self):
        missing = Path(self.temp_dir.name) / "no_such_config.yaml"
        out = StringIO()
        with redirect_stdout(out):
            rc = cli.main([
                "--db", str(self.db_path),
                "implementation", "compile-check",
                "--real-compile-config", str(missing),
                self.impl_req_id,
            ])
        self.assertEqual(rc, 1)
        data = json.loads(out.getvalue())
        self.assertIn("not found", data.get("error", ""))


class ResearchPhase18ConfigValidationTests(unittest.TestCase):
    """Config loading and validation."""

    def test_valid_config_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_data = {
                "mode": "real_compile",
                "wine_binary": "/usr/bin/wine",
                "wine_prefix": "/home/user/wine",
                "metaeditor_path": "/home/user/metaeditor64.exe",
                "timeout_seconds": 60,
            }
            config_path.write_text(yaml.safe_dump(config_data), encoding="utf-8")
            config = compiler.load_real_compile_config(config_path)
            self.assertEqual(config.mode, "real_compile")
            self.assertEqual(config.wine_binary, "/usr/bin/wine")
            self.assertEqual(config.wine_prefix, "/home/user/wine")
            self.assertEqual(config.timeout_seconds, 60)

    def test_wrong_mode_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text(yaml.safe_dump({"mode": "real_backtest"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                compiler.load_real_compile_config(config_path)

    def test_missing_file_rejected(self):
        with self.assertRaises(FileNotFoundError):
            compiler.load_real_compile_config("/nonexistent/path.yaml")

    def test_bad_timeout_rejected(self):
        raw = {"mode": "real_compile", "timeout_seconds": -1}
        with self.assertRaises(ValueError):
            compiler.validate_real_compile_config(raw)

    def test_bad_mode_type_rejected(self):
        raw = {"mode": 42}
        with self.assertRaises(ValueError):
            compiler.validate_real_compile_config(raw)


class ResearchPhase18SideEffectTests(unittest.TestCase):
    """Real compile must not create side effects beyond compile-check evidence."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.strategy_id = "ph18_side_effects"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"
        _make_mq5(self.mq5_path)
        self.create_result = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            sandbox_dir=self.sandbox,
            generated_files=[f"{self.strategy_id}.mq5"],
            created_by="ph18_test",
        )
        self.impl_req_id = self.create_result["implementation_request_id"]

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_real_compile_no_side_effects(self):
        fake_compiler = Path(self.temp_dir.name) / "fake_se.py"
        _make_fake_compiler(fake_compiler, exit_code=0)
        config_path = _make_real_compile_config(fake_compiler)
        config = compiler.load_real_compile_config(config_path)
        impl_mod.compile_check(
            self.db_path, self.impl_req_id, mock=False, real_compile_config=config
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


class ResearchPhase18DenyListTests(unittest.TestCase):
    """Safety test: forbidden proposal/action values must not appear as allowed values."""

    def test_deny_list_terms_not_in_compiler_module(self):
        import automated.research.compiler as comp_mod
        source = Path(comp_mod.__file__).read_text(encoding="utf-8")
        for term in ("promote_to_production", "production_candidate", "live_trading_candidate"):
            self.assertNotIn(
                term, source,
                f"{term} must not appear in compiler.py as an allowed value",
            )

    def test_deny_list_terms_not_in_compile_check(self):
        source = Path(impl_mod.__file__).read_text(encoding="utf-8")
        for term in ("promote_to_production", "production_candidate", "live_trading_candidate"):
            self.assertNotIn(
                term, source,
                f"{term} must not appear in implementation.py as an allowed value",
            )


if __name__ == "__main__":
    unittest.main()
