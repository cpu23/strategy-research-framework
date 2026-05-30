from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from automated.research import cli, implementation as impl_mod, registry
from automated.research.schemas import (
    REPO_ROOT,
    SANDBOX_ROOT,
    SchemaValidationError,
    validate_implementation_request,
)


def _valid_request_data() -> dict:
    return {
        "implementation_request_id": "IMPL_REQ_TEST_001",
        "hypothesis_id": "HYP_FAILED_BREAKOUT_REVERSAL_001",
        "strategy_id": "test_strategy",
        "strategy_version": "v1",
        "strategy_spec_path": None,
        "sandbox_dir": str(SANDBOX_ROOT / "test_strategy" / "v1"),
        "allowed_files": ["*.mq5"],
        "forbidden_files": ["automated/strategies/**"],
        "generated_files": ["TestStrategy.mq5"],
        "entry_logic": "Entry on RSI < 30",
        "exit_logic": "Exit on RSI > 70 or trailing stop",
        "risk_logic": "1% risk per trade",
        "parameters": {"InpRiskPerTrade": 0.01},
        "expected_inputs": [
            {"name": "InpRiskPerTrade", "type": "double", "required": True, "default": "0.01"},
            {"name": "InpAtrPeriod", "type": "int", "required": True, "default": "14"},
        ],
        "compile_command": "mql5",
        "test_plan": "Run backtest on XAUUSD H4",
        "created_by": "test",
        "created_at": "2026-05-12T00:00:00+00:00",
        "status": "proposed",
    }


def _write_sample_mq5(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "//+------------------------------------------------------------------+\n"
        "//| TestStrategy.mq5                                                |\n"
        "//+------------------------------------------------------------------+\n"
        "#property version   \"1.00\"\n"
        "input double InpRiskPerTrade = 0.01;\n"
        "input int    InpAtrPeriod = 14;\n"
        "input bool   InpUseSessionFilter = true;\n"
        "input int    InpMagicNumber = 12345;\n"
        "int OnInit() { return INIT_SUCCEEDED; }\n"
        "void OnTick() {\n"
        "   CTrade trade;\n"
        "   trade.Buy(0.1, _Symbol, 0, 0, 0);\n"
        "}\n"
        "void OnDeinit(const int reason) {}\n",
        encoding="utf-8",
    )


def _write_dangerous_mq5(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "//+------------------------------------------------------------------+\n"
        "#import \"user32.dll\"\n"
        "   int MessageBoxA(int hWnd, string lpText, string lpCaption, int uType);\n"
        "#import\n"
        "input double InpRiskPerTrade = 0.01;\n"
        "int OnInit() { return INIT_SUCCEEDED; }\n"
        "void OnTick() {\n"
        "   ShellExecuteA(0, \"open\", \"http://evil.com\", NULL, NULL, 0);\n"
        "   WebRequest(\"GET\", \"http://evil.com/data\", NULL, 0, NULL, 0, NULL, 0);\n"
        "   int handle = FileOpen(\"data.csv\", FILE_WRITE);\n"
        "   FileWrite(handle, \"data\");\n"
        "   GlobalVariableSet(\"malicious\", 1.0);\n"
        "   double lot = 100.0;\n"
        "   CTrade trade;\n"
        "   trade.Buy(lot, _Symbol);\n"
        "}\n"
        "void OnDeinit(const int reason) {\n"
        "   FileDelete(\"data.csv\");\n"
        "}\n",
        encoding="utf-8",
    )


def _write_no_inputs_mq5(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "//+------------------------------------------------------------------+\n"
        "int OnInit() { return INIT_SUCCEEDED; }\n"
        "void OnTick() {}\n"
        "void OnDeinit(const int reason) {}\n",
        encoding="utf-8",
    )


class ResearchPhase9SchemaTests(unittest.TestCase):
    def test_valid_request_passes_schema(self) -> None:
        data = _valid_request_data()
        result = validate_implementation_request(data)
        self.assertEqual(result["implementation_request_id"], "IMPL_REQ_TEST_001")

    def test_missing_required_fields_rejected(self) -> None:
        data = _valid_request_data()
        del data["implementation_request_id"]
        with self.assertRaises(SchemaValidationError):
            validate_implementation_request(data)

    def test_missing_strategy_id_rejected(self) -> None:
        data = _valid_request_data()
        del data["strategy_id"]
        with self.assertRaises(SchemaValidationError):
            validate_implementation_request(data)

    def test_bad_status_rejected(self) -> None:
        data = _valid_request_data()
        data["status"] = "invalid_status"
        with self.assertRaises(SchemaValidationError):
            validate_implementation_request(data)

    def test_production_strategy_path_rejected(self) -> None:
        data = _valid_request_data()
        data["sandbox_dir"] = str(REPO_ROOT / "automated" / "strategies" / "test")
        with self.assertRaises(SchemaValidationError):
            validate_implementation_request(data)

    def test_sandbox_dir_must_be_under_generated(self) -> None:
        data = _valid_request_data()
        data["sandbox_dir"] = "/tmp/random"
        with self.assertRaises(SchemaValidationError):
            validate_implementation_request(data)

    def test_generated_files_must_be_non_empty_list(self) -> None:
        data = _valid_request_data()
        data["generated_files"] = []
        with self.assertRaises(SchemaValidationError):
            validate_implementation_request(data)
        data["generated_files"] = "not_a_list"
        with self.assertRaises(SchemaValidationError):
            validate_implementation_request(data)

    def test_invalid_iso_date_rejected(self) -> None:
        data = _valid_request_data()
        data["created_at"] = "not-a-date"
        with self.assertRaises(SchemaValidationError):
            validate_implementation_request(data)

    def test_expected_inputs_validation(self) -> None:
        data = _valid_request_data()
        data["expected_inputs"] = [{"name": "InpParam", "required": True}]
        with self.assertRaises(SchemaValidationError):
            validate_implementation_request(data)

        data["expected_inputs"] = [{"name": "InpParam", "required": True, "type": "int"}]
        result = validate_implementation_request(data)
        self.assertEqual(result["implementation_request_id"], "IMPL_REQ_TEST_001")


class ResearchPhase9SandboxEnforcementTests(unittest.TestCase):
    def test_generated_impl_cannot_overwrite_existing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            mq5 = temp / "generated" / "test.mq5"
            mq5.parent.mkdir(parents=True)
            mq5.write_text("existing", encoding="utf-8")
            error = impl_mod.check_overwrite(mq5)
            self.assertIsNotNone(error)
            self.assertIn("already exists", error)

    def test_generated_impl_cannot_write_to_strategies(self) -> None:
        violations = impl_mod.check_no_production_touch(
            [REPO_ROOT / "automated" / "strategies" / "test" / "test.mq5"]
        )
        self.assertTrue(len(violations) > 0)

    def test_sandbox_path_enforced(self) -> None:
        with self.assertRaises(SchemaValidationError):
            impl_mod.assert_sandbox_path(REPO_ROOT / "automated" / "research" / "test.py")
        with self.assertRaises(SchemaValidationError):
            impl_mod.assert_sandbox_path(REPO_ROOT / "automated" / "scripts" / "test.sh")
        with self.assertRaises(SchemaValidationError):
            impl_mod.assert_sandbox_path(REPO_ROOT / "automated" / "specs" / "test.yaml")
        with self.assertRaises(SchemaValidationError):
            impl_mod.assert_sandbox_path(REPO_ROOT / "automated" / "runs" / "test.conf")
        with self.assertRaises(SchemaValidationError):
            impl_mod.assert_sandbox_path(REPO_ROOT / "tests" / "test.py")
        with self.assertRaises(SchemaValidationError):
            impl_mod.assert_sandbox_path(REPO_ROOT / "hypotheses" / "test.yaml")

    def test_generated_path_allowed(self) -> None:
        path = SANDBOX_ROOT / "test_strat" / "v1" / "test.mq5"
        try:
            impl_mod.assert_sandbox_path(path)
        except SchemaValidationError:
            self.fail(f"assert_sandbox_path raised for valid path: {path}")


class ResearchPhase9MQL5ParserTests(unittest.TestCase):
    def test_parse_basic_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mq5 = Path(temp_dir) / "test.mq5"
            mq5.write_text(
                "input double InpRiskPerTrade = 0.005;\n"
                "input int InpAtrPeriod = 14;\n"
                "input bool InpUseSessionFilter = true;\n",
                encoding="utf-8",
            )
            inputs = impl_mod.parse_mql5_inputs(mq5)
            self.assertEqual(len(inputs), 3)
            self.assertEqual(inputs[0]["name"], "InpRiskPerTrade")
            self.assertEqual(inputs[0]["type"], "double")
            self.assertEqual(inputs[0]["default"], "0.005")
            self.assertEqual(inputs[1]["name"], "InpAtrPeriod")
            self.assertEqual(inputs[1]["type"], "int")
            self.assertEqual(inputs[1]["default"], "14")
            self.assertEqual(inputs[2]["name"], "InpUseSessionFilter")
            self.assertEqual(inputs[2]["type"], "bool")
            self.assertEqual(inputs[2]["default"], "true")

    def test_parse_inputs_no_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mq5 = Path(temp_dir) / "test.mq5"
            mq5.write_text(
                "input int InpParam1;\ninput double InpParam2;\n",
                encoding="utf-8",
            )
            inputs = impl_mod.parse_mql5_inputs(mq5)
            self.assertEqual(len(inputs), 2)
            self.assertIsNone(inputs[0]["default"])
            self.assertIsNone(inputs[1]["default"])

    def test_parse_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mq5 = Path(temp_dir) / "empty.mq5"
            mq5.write_text("", encoding="utf-8")
            inputs = impl_mod.parse_mql5_inputs(mq5)
            self.assertEqual(inputs, [])

    def test_parse_no_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mq5 = Path(temp_dir) / "no_inputs.mq5"
            mq5.write_text(
                "int OnInit() { return INIT_SUCCEEDED; }\n"
                "void OnTick() {}\n",
                encoding="utf-8",
            )
            inputs = impl_mod.parse_mql5_inputs(mq5)
            self.assertEqual(inputs, [])

    def test_parse_missing_file(self) -> None:
        inputs = impl_mod.parse_mql5_inputs("/nonexistent/path.mq5")
        self.assertEqual(inputs, [])


class ResearchPhase9InputComparisonTests(unittest.TestCase):
    def test_exact_match(self) -> None:
        mq5_inputs = [
            {"name": "InpRiskPerTrade", "type": "double", "default": "0.01"},
            {"name": "InpAtrPeriod", "type": "int", "default": "14"},
        ]
        expected = [
            {"name": "InpRiskPerTrade", "type": "double", "required": True, "default": "0.01"},
            {"name": "InpAtrPeriod", "type": "int", "required": True, "default": "14"},
        ]
        result = impl_mod.compare_inputs(mq5_inputs, expected)
        self.assertTrue(result["match"])
        self.assertEqual(result["mismatches"], [])

    def test_missing_required_input(self) -> None:
        mq5_inputs = [
            {"name": "InpAtrPeriod", "type": "int", "default": "14"},
        ]
        expected = [
            {"name": "InpRiskPerTrade", "type": "double", "required": True, "default": "0.01"},
            {"name": "InpAtrPeriod", "type": "int", "required": True, "default": "14"},
        ]
        result = impl_mod.compare_inputs(mq5_inputs, expected)
        self.assertFalse(result["match"])
        self.assertTrue(any("InpRiskPerTrade" in m for m in result["mismatches"]))

    def test_type_mismatch(self) -> None:
        mq5_inputs = [
            {"name": "InpRiskPerTrade", "type": "int", "default": "1"},
        ]
        expected = [
            {"name": "InpRiskPerTrade", "type": "double", "required": True, "default": "0.01"},
        ]
        result = impl_mod.compare_inputs(mq5_inputs, expected)
        self.assertFalse(result["match"])
        self.assertTrue(any("Type mismatch" in m for m in result["mismatches"]))

    def test_default_mismatch(self) -> None:
        mq5_inputs = [
            {"name": "InpAtrPeriod", "type": "int", "default": "20"},
        ]
        expected = [
            {"name": "InpAtrPeriod", "type": "int", "required": True, "default": "14"},
        ]
        result = impl_mod.compare_inputs(mq5_inputs, expected)
        self.assertFalse(result["match"])
        self.assertTrue(any("Default mismatch" in m for m in result["mismatches"]))

    def test_unexpected_input_detected(self) -> None:
        mq5_inputs = [
            {"name": "InpRiskPerTrade", "type": "double", "default": "0.01"},
            {"name": "InpUnexpected", "type": "int", "default": "99"},
        ]
        expected = [
            {"name": "InpRiskPerTrade", "type": "double", "required": True, "default": "0.01"},
        ]
        result = impl_mod.compare_inputs(mq5_inputs, expected)
        self.assertFalse(result["match"])
        self.assertTrue(any("Unexpected" in m for m in result["mismatches"]))

    def test_allow_extra_inputs(self) -> None:
        mq5_inputs = [
            {"name": "InpRiskPerTrade", "type": "double", "default": "0.01"},
            {"name": "InpExtra", "type": "int", "default": "99"},
        ]
        expected = [
            {"name": "InpRiskPerTrade", "type": "double", "required": True, "default": "0.01"},
        ]
        result = impl_mod.compare_inputs(mq5_inputs, expected, allow_extra_inputs=True)
        self.assertTrue(result["match"])

    def test_required_not_in_expected_does_not_fail(self) -> None:
        mq5_inputs = []
        expected = []
        result = impl_mod.compare_inputs(mq5_inputs, expected)
        self.assertTrue(result["match"])


class ResearchPhase9DangerScanTests(unittest.TestCase):
    def test_dangerous_patterns_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mq5 = Path(temp_dir) / "dangerous.mq5"
            _write_dangerous_mq5(mq5)
            findings = impl_mod.scan_dangerous_patterns(mq5)
            ids = {f["id"] for f in findings}
            self.assertIn("import_directive", ids)
            self.assertIn("shell_execute", ids)
            self.assertIn("web_request", ids)
            self.assertIn("file_open", ids)
            self.assertIn("file_write", ids)
            self.assertIn("file_delete", ids)
            self.assertIn("global_variable_set", ids)
            self.assertIn("hardcoded_lot", ids)

    def test_clean_file_no_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mq5 = Path(temp_dir) / "clean.mq5"
            mq5.write_text(
                "input double InpRisk = 0.01;\n"
                "int OnInit() { return INIT_SUCCEEDED; }\n"
                "void OnTick() {}\n",
                encoding="utf-8",
            )
            findings = impl_mod.scan_dangerous_patterns(mq5)
            dangerous_ids = {f["id"] for f in findings if f["severity"] == "warning"}
            self.assertEqual(len(dangerous_ids), 0)


class ResearchPhase9LifecycleTests(unittest.TestCase):
    def test_create_request_and_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="lifecycle_test",
                strategy_version="v1",
                sandbox_dir=SANDBOX_ROOT / "lifecycle_test" / "v1",
                generated_files=["LifecycleTest.mq5"],
                created_by="test",
                hypothesis_id="HYP_TEST",
                expected_inputs=[
                    {"name": "InpParam", "type": "int", "required": True, "default": "10"},
                ],
            )
            self.assertEqual(result["status"], "proposed")
            self.assertIn("IMPL_REQ", result["implementation_request_id"])
            request = registry.get_implementation_request(db_path, result["implementation_request_id"])
            self.assertIsNotNone(request)
            self.assertEqual(request["strategy_id"], "lifecycle_test")

    def test_missing_impl_request_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            with self.assertRaises(ValueError):
                impl_mod.validate_request(db_path, "NONEXISTENT")

    def test_missing_generated_mq5_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "missing_mq5" / "v1"
            result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="missing_mq5",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["Missing.mq5"],
                created_by="test",
            )
            compile_result = impl_mod.compile_check(db_path, result["implementation_request_id"], mock=True)
            self.assertEqual(compile_result.get("status"), "failed")
            self.assertTrue(any("No .mq5 files found" in e for e in compile_result.get("errors", [])))

    def test_compile_failure_blocks_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "compile_fail" / "v1"
            sandbox.mkdir(parents=True, exist_ok=True)
            (sandbox / "Test.mq5").write_text("garbage", encoding="utf-8")
            result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="compile_fail",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["Test.mq5"],
                created_by="test",
            )
            compile_result = impl_mod.compile_check(db_path, result["implementation_request_id"], mock=True)
            self.assertEqual(compile_result["compile_status"], "mock_checked")
            impl_mod.run_diff_review(db_path, result["implementation_request_id"])
            approval = impl_mod.approve_for_baseline(
                db_path, result["implementation_request_id"],
                approved_by="test",
                require_real_compile=True,
            )
            self.assertFalse(approval["approved"])
            self.assertTrue(any("mock" in e.lower() for e in approval["errors"]))

    def test_compile_mock_ok_with_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "mock_ok" / "v1"
            sandbox.mkdir(parents=True, exist_ok=True)
            _write_sample_mq5(sandbox / "Test.mq5")
            result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="mock_ok",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["Test.mq5"],
                created_by="test",
                expected_inputs=[
                    {"name": "InpRiskPerTrade", "type": "double", "required": True, "default": "0.01"},
                    {"name": "InpAtrPeriod", "type": "int", "required": True, "default": "14"},
                ],
            )
            compile_result = impl_mod.compile_check(db_path, result["implementation_request_id"], mock=True)
            self.assertEqual(compile_result["compile_status"], "mock_checked")
            impl_mod.run_diff_review(db_path, result["implementation_request_id"])
            approval = impl_mod.approve_for_baseline(
                db_path, result["implementation_request_id"],
                approved_by="test",
                require_real_compile=False,
            )
            self.assertTrue(approval["approved"])
            self.assertTrue(approval["baseline_only"])

    def test_input_spec_mismatch_blocks_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "input_mismatch" / "v1"
            result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="input_mismatch",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["Test.mq5"],
                created_by="test",
                expected_inputs=[
                    {"name": "InpDoesNotExist", "type": "double", "required": True, "default": "99.0"},
                ],
            )
            sandbox.mkdir(parents=True, exist_ok=True)
            _write_sample_mq5(sandbox / "Test.mq5")
            impl_mod.compile_check(db_path, result["implementation_request_id"], mock=True)
            impl_mod.run_diff_review(db_path, result["implementation_request_id"])
            approval = impl_mod.approve_for_baseline(
                db_path, result["implementation_request_id"],
                approved_by="test",
                require_real_compile=False,
            )
            self.assertFalse(approval["approved"])
            self.assertTrue(any("InpDoesNotExist" in e for e in approval["errors"]))

    def test_diff_review_artifact_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "diff_review_test" / "v1"
            sandbox.mkdir(parents=True, exist_ok=True)
            _write_sample_mq5(sandbox / "Test.mq5")
            result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="diff_review_test",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["Test.mq5"],
                created_by="test",
                expected_inputs=[
                    {"name": "InpRiskPerTrade", "type": "double", "required": True, "default": "0.01"},
                    {"name": "InpAtrPeriod", "type": "int", "required": True, "default": "14"},
                ],
            )
            impl_mod.compile_check(db_path, result["implementation_request_id"], mock=True)
            review_result = impl_mod.run_diff_review(db_path, result["implementation_request_id"])
            self.assertIn("artifact_path", review_result)
            artifact_path = Path(review_result["artifact_path"])
            self.assertTrue(artifact_path.is_file())
            import yaml
            review = yaml.safe_load(artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(review["implementation_request_id"], result["implementation_request_id"])
            self.assertIn("declared_mql5_inputs", review)
            self.assertIn("expected_spec_inputs", review)
            self.assertIn("baseline_eligible", review)

    def test_forbidden_path_is_hard_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "forbidden_block" / "v1"
            sandbox.mkdir(parents=True, exist_ok=True)
            _write_sample_mq5(sandbox / "Test.mq5")
            result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="forbidden_block",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["Test.mq5"],
                created_by="test",
            )
            violations = impl_mod.check_no_production_touch(
                [sandbox / "Test.mq5"]
            )
            self.assertEqual(len(violations), 0)
            bad_violations = impl_mod.check_no_production_touch(
                [REPO_ROOT / "automated" / "strategies" / "evil" / "test.mq5"]
            )
            self.assertTrue(len(bad_violations) > 0)

    def test_approval_creates_auditable_baseline_only_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "audit_test" / "v1"
            sandbox.mkdir(parents=True, exist_ok=True)
            _write_sample_mq5(sandbox / "Test.mq5")
            result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="audit_test",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["Test.mq5"],
                created_by="test",
                expected_inputs=[
                    {"name": "InpRiskPerTrade", "type": "double", "required": True, "default": "0.01"},
                    {"name": "InpAtrPeriod", "type": "int", "required": True, "default": "14"},
                ],
            )
            impl_mod.compile_check(db_path, result["implementation_request_id"], mock=True)
            impl_mod.run_diff_review(db_path, result["implementation_request_id"])
            approval = impl_mod.approve_for_baseline(
                db_path, result["implementation_request_id"],
                approved_by="reviewer_human",
                require_real_compile=False,
            )
            self.assertTrue(approval["approved"])
            self.assertEqual(approval["approved_by"], "reviewer_human")
            self.assertTrue(approval["baseline_only"])
            self.assertIn("baseline", approval["note"].lower())
            self.assertNotIn("production", approval["note"].lower())
            impls = registry.list_implementations(db_path, result["implementation_request_id"])
            self.assertEqual(len(impls), 1)
            self.assertEqual(impls[0]["approved_for_baseline"], 1)
            self.assertEqual(impls[0]["approved_by"], "reviewer_human")

    def test_approval_does_not_copy_to_strategies(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "no_copy" / "v1"
            sandbox.mkdir(parents=True, exist_ok=True)
            _write_sample_mq5(sandbox / "Test.mq5")
            result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="no_copy",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["Test.mq5"],
                created_by="test",
                expected_inputs=[
                    {"name": "InpRiskPerTrade", "type": "double", "required": True, "default": "0.01"},
                    {"name": "InpAtrPeriod", "type": "int", "required": True, "default": "14"},
                ],
            )
            impl_mod.compile_check(db_path, result["implementation_request_id"], mock=True)
            impl_mod.run_diff_review(db_path, result["implementation_request_id"])
            impl_mod.approve_for_baseline(
                db_path, result["implementation_request_id"],
                approved_by="test",
                require_real_compile=False,
            )
            strategies_dir = REPO_ROOT / "automated" / "strategies"
            copied_files = list(strategies_dir.rglob("*Test.mq5"))
            mq5_in_strategies = [f for f in copied_files if "Test.mq5" in f.name]
            self.assertEqual(len(mq5_in_strategies), 0,
                             "Approval should not copy files into automated/strategies/")

    def test_no_mt5_wine_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "no_mt5" / "v1"
            sandbox.mkdir(parents=True, exist_ok=True)
            _write_sample_mq5(sandbox / "Test.mq5")
            result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="no_mt5",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["Test.mq5"],
                created_by="test",
            )
            compile_result = impl_mod.compile_check(db_path, result["implementation_request_id"], mock=True)
            self.assertEqual(compile_result["compile_status"], "mock_checked")
            self.assertTrue(Path(compile_result["generated_mq5_path"]).is_file())

    def test_full_lifecycle_with_mock_compile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "full_lifecycle" / "v1"
            sandbox.mkdir(parents=True, exist_ok=True)
            _write_sample_mq5(sandbox / "TestStrategy.mq5")
            result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="full_lifecycle",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["TestStrategy.mq5"],
                created_by="test",
                hypothesis_id="HYP_TEST",
                expected_inputs=[
                    {"name": "InpRiskPerTrade", "type": "double", "required": True, "default": "0.01"},
                    {"name": "InpAtrPeriod", "type": "int", "required": True, "default": "14"},
                ],
            )
            req_id = result["implementation_request_id"]

            validate_result = impl_mod.validate_request(db_path, req_id)
            self.assertTrue(validate_result["valid"])

            inspect_result = impl_mod.inspect(db_path, req_id)
            self.assertEqual(inspect_result["implementation_request_id"], req_id)
            self.assertEqual(inspect_result["status"], "validated")

            compile_result = impl_mod.compile_check(db_path, req_id, mock=True)
            self.assertEqual(compile_result["compile_status"], "mock_checked")

            review_result = impl_mod.run_diff_review(db_path, req_id)
            artifact_path = Path(review_result["artifact_path"])
            self.assertTrue(artifact_path.is_file())
            import yaml
            if artifact_path.is_file():
                review = yaml.safe_load(artifact_path.read_text(encoding="utf-8"))
                self.assertIn("baseline_eligible", review)
                self.assertIn("declared_mql5_inputs", review)

            approval = impl_mod.approve_for_baseline(
                db_path, req_id,
                approved_by="human_reviewer",
                baseline_experiment_id="EXP_BASELINE_001",
                require_real_compile=False,
            )
            self.assertTrue(approval["approved"])
            self.assertTrue(approval["baseline_only"])
            self.assertEqual(approval["baseline_experiment_id"], "EXP_BASELINE_001")

            final_request = registry.get_implementation_request(db_path, req_id)
            self.assertEqual(final_request["status"], "approved_for_baseline")

            impls = registry.list_implementations(db_path, req_id)
            self.assertEqual(len(impls), 1)
            self.assertEqual(impls[0]["approved_for_baseline"], 1)

            strategies_str = str(REPO_ROOT / "automated" / "strategies")
            sandbox_str = str(sandbox)
            self.assertNotIn(strategies_str, sandbox_str)


class ResearchPhase9QueueTests(unittest.TestCase):
    def test_queue_task_types_registered(self) -> None:
        from automated.research.queue import TASK_TYPES
        self.assertIn("implementation_request", TASK_TYPES)
        self.assertIn("implementation_compile_check", TASK_TYPES)
        self.assertIn("implementation_review", TASK_TYPES)

    def test_impl_request_creates_registry_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "queue_test" / "v1"
            result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="queue_test",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["QueueTest.mq5"],
                created_by="queue",
            )
            request = registry.get_implementation_request(db_path, result["implementation_request_id"])
            self.assertIsNotNone(request)
            self.assertEqual(request["status"], "proposed")


class ResearchPhase9CLISmokeTests(unittest.TestCase):
    def test_cli_create_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            out = StringIO()
            with redirect_stdout(out):
                rc = cli.main([
                    "--db", str(db_path),
                    "implementation", "create-request",
                    "--strategy-id", "cli_test",
                    "--strategy-version", "v1",
                    "--generated-files", "CLITest.mq5",
                    "--created-by", "cli_test",
                ])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())
            self.assertIn("implementation_request_id", data)
            self.assertEqual(data["status"], "proposed")

    def test_cli_validate_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "cli_validate" / "v1"
            sandbox.mkdir(parents=True, exist_ok=True)
            _write_sample_mq5(sandbox / "Test.mq5")
            create_result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="cli_validate",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["Test.mq5"],
                created_by="cli_test",
            )
            out = StringIO()
            with redirect_stdout(out):
                rc = cli.main([
                    "--db", str(db_path),
                    "implementation", "validate-request",
                    create_result["implementation_request_id"],
                ])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())
            self.assertTrue(data["valid"])

    def test_cli_inspect(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "cli_inspect" / "v1"
            sandbox.mkdir(parents=True, exist_ok=True)
            _write_sample_mq5(sandbox / "Test.mq5")
            create_result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="cli_inspect",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["Test.mq5"],
                created_by="cli_test",
            )
            out = StringIO()
            with redirect_stdout(out):
                rc = cli.main([
                    "--db", str(db_path),
                    "implementation", "inspect",
                    create_result["implementation_request_id"],
                ])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())
            self.assertEqual(data["implementation_request_id"], create_result["implementation_request_id"])

    def test_cli_compile_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "cli_compile" / "v1"
            sandbox.mkdir(parents=True, exist_ok=True)
            _write_sample_mq5(sandbox / "Test.mq5")
            create_result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="cli_compile",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["Test.mq5"],
                created_by="cli_test",
            )
            out = StringIO()
            with redirect_stdout(out):
                rc = cli.main([
                    "--db", str(db_path),
                    "implementation", "compile-check",
                    "--mock",
                    create_result["implementation_request_id"],
                ])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())
            self.assertEqual(data["compile_status"], "mock_checked")

    def test_cli_diff_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "cli_review" / "v1"
            sandbox.mkdir(parents=True, exist_ok=True)
            _write_sample_mq5(sandbox / "Test.mq5")
            create_result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="cli_review",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["Test.mq5"],
                created_by="cli_test",
            )
            impl_mod.compile_check(db_path, create_result["implementation_request_id"], mock=True)
            out = StringIO()
            with redirect_stdout(out):
                rc = cli.main([
                    "--db", str(db_path),
                    "implementation", "diff-review",
                    create_result["implementation_request_id"],
                ])
            self.assertEqual(rc, 0)

    def test_cli_approve_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "cli_approve" / "v1"
            sandbox.mkdir(parents=True, exist_ok=True)
            _write_sample_mq5(sandbox / "Test.mq5")
            create_result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="cli_approve",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["Test.mq5"],
                created_by="cli_test",
                expected_inputs=[
                    {"name": "InpRiskPerTrade", "type": "double", "required": True, "default": "0.01"},
                    {"name": "InpAtrPeriod", "type": "int", "required": True, "default": "14"},
                ],
            )
            impl_mod.compile_check(db_path, create_result["implementation_request_id"], mock=True)
            impl_mod.run_diff_review(db_path, create_result["implementation_request_id"])
            out = StringIO()
            with redirect_stdout(out):
                rc = cli.main([
                    "--db", str(db_path),
                    "implementation", "approve-for-baseline",
                    "--allow-mock-compile",
                    create_result["implementation_request_id"],
                    "--approved-by", "cli_test",
                ])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())
            self.assertTrue(data["approved"])
            self.assertTrue(data["baseline_only"])

    def test_tiny_generated_strategy_full_lifecycle(self) -> None:
        """Tiny generated strategy fixture: create -> validate -> inspect ->
        compile-check -> diff-review -> approve-for-baseline via CLI."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "tiny_generated_strategy" / "v0.1.0"
            sandbox.mkdir(parents=True, exist_ok=True)
            _write_sample_mq5(sandbox / "TinyStrategy.mq5")

            out = StringIO()
            with redirect_stdout(out):
                rc = cli.main([
                    "--db", str(db_path),
                    "implementation", "create-request",
                    "--strategy-id", "tiny_generated_strategy",
                    "--strategy-version", "v0.1.0",
                    "--generated-files", "TinyStrategy.mq5",
                    "--created-by", "test",
                    "--expected-inputs", json.dumps([
                        {"name": "InpRiskPerTrade", "type": "double", "required": True, "default": "0.01"},
                        {"name": "InpAtrPeriod", "type": "int", "required": True, "default": "14"},
                        {"name": "InpUseSessionFilter", "type": "bool", "required": False, "default": "true"},
                    ]),
                ])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())
            req_id = data["implementation_request_id"]

            out = StringIO()
            with redirect_stdout(out):
                rc = cli.main(["--db", str(db_path), "implementation", "validate-request", req_id])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())
            self.assertTrue(data["valid"])

            out = StringIO()
            with redirect_stdout(out):
                rc = cli.main(["--db", str(db_path), "implementation", "inspect", req_id])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())
            self.assertEqual(data["status"], "validated")

            out = StringIO()
            with redirect_stdout(out):
                rc = cli.main(["--db", str(db_path), "implementation", "compile-check", "--mock", req_id])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())
            self.assertEqual(data["compile_status"], "mock_checked")

            out = StringIO()
            with redirect_stdout(out):
                rc = cli.main(["--db", str(db_path), "implementation", "diff-review", req_id])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())

            review_path = Path(impl_mod.IMPL_REQUESTS_DIR) / req_id / "diff_review.yaml"
            self.assertTrue(review_path.is_file())
            import yaml
            review = yaml.safe_load(review_path.read_text(encoding="utf-8"))
            self.assertEqual(review["implementation_request_id"], req_id)
            self.assertIn("baseline_eligible", review)

            out = StringIO()
            with redirect_stdout(out):
                rc = cli.main([
                    "--db", str(db_path),
                    "implementation", "approve-for-baseline",
                    "--allow-mock-compile", req_id,
                    "--approved-by", "test",
                ])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())
            self.assertTrue(data["approved"])
            self.assertTrue(data["baseline_only"])

            request = registry.get_implementation_request(db_path, req_id)
            self.assertEqual(request["status"], "approved_for_baseline")
            impls = registry.list_implementations(db_path, req_id)
            self.assertEqual(impls[0]["approved_for_baseline"], 1)

            strategies_dir = REPO_ROOT / "automated" / "strategies"
            self.assertFalse(
                any("TinyStrategy.mq5" in str(f) for f in strategies_dir.rglob("*")),
                "Approval must not copy files into automated/strategies/",
            )

    def test_cli_approve_rejects_mock_without_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "cli_reject_mock" / "v1"
            sandbox.mkdir(parents=True, exist_ok=True)
            _write_sample_mq5(sandbox / "Test.mq5")
            create_result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="cli_reject_mock",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["Test.mq5"],
                created_by="cli_test",
            )
            impl_mod.compile_check(db_path, create_result["implementation_request_id"], mock=True)
            impl_mod.run_diff_review(db_path, create_result["implementation_request_id"])
            out = StringIO()
            with redirect_stdout(out):
                rc = cli.main([
                    "--db", str(db_path),
                    "implementation", "approve-for-baseline",
                    create_result["implementation_request_id"],
                    "--approved-by", "cli_test",
                ])
            self.assertEqual(rc, 1)


class ResearchPhase9BaselineHandoffGuardTests(unittest.TestCase):
    """Test the require_generated_baseline_approval guard function."""

    def _full_approval(self, db_path: Path, strategy_id: str, version: str) -> str:
        sandbox = SANDBOX_ROOT / strategy_id / version
        sandbox.mkdir(parents=True, exist_ok=True)
        _write_sample_mq5(sandbox / "Test.mq5")
        result = impl_mod.create_implementation_request(
            db_path,
            strategy_id=strategy_id,
            strategy_version=version,
            sandbox_dir=sandbox,
            generated_files=["Test.mq5"],
            created_by="test",
            expected_inputs=[
                {"name": "InpRiskPerTrade", "type": "double", "required": True, "default": "0.01"},
                {"name": "InpAtrPeriod", "type": "int", "required": True, "default": "14"},
                {"name": "InpUseSessionFilter", "type": "bool", "required": False, "default": "true"},
                {"name": "InpMagicNumber", "type": "int", "required": False, "default": "12345"},
            ],
        )
        impl_mod.compile_check(db_path, result["implementation_request_id"], mock=True)
        impl_mod.run_diff_review(db_path, result["implementation_request_id"])
        impl_mod.approve_for_baseline(
            db_path, result["implementation_request_id"],
            approved_by="test", require_real_compile=False,
        )
        return result["implementation_request_id"]

    def test_unapproved_cannot_be_used_for_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "unapproved_guard" / "v1"
            sandbox.mkdir(parents=True, exist_ok=True)
            _write_sample_mq5(sandbox / "Test.mq5")
            result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="unapproved_guard",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["Test.mq5"],
                created_by="test",
            )
            impl_mod.compile_check(db_path, result["implementation_request_id"], mock=True)
            impl_mod.run_diff_review(db_path, result["implementation_request_id"])
            guard = impl_mod.require_generated_baseline_approval(db_path, "unapproved_guard", "v1")
            self.assertFalse(guard["approved"])
            self.assertTrue(any("approved" in e.lower() for e in guard["errors"]))

    def test_no_compile_check_cannot_be_used_for_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "no_compile_guard" / "v1"
            sandbox.mkdir(parents=True, exist_ok=True)
            _write_sample_mq5(sandbox / "Test.mq5")
            impl_mod.create_implementation_request(
                db_path,
                strategy_id="no_compile_guard",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["Test.mq5"],
                created_by="test",
            )
            guard = impl_mod.require_generated_baseline_approval(db_path, "no_compile_guard", "v1")
            self.assertFalse(guard["approved"])
            self.assertTrue(any("compile" in e.lower() for e in guard["errors"]))

    def test_input_mismatch_cannot_be_used_for_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            sandbox = SANDBOX_ROOT / "mismatch_guard" / "v1"
            sandbox.mkdir(parents=True, exist_ok=True)
            _write_sample_mq5(sandbox / "Test.mq5")
            result = impl_mod.create_implementation_request(
                db_path,
                strategy_id="mismatch_guard",
                strategy_version="v1",
                sandbox_dir=sandbox,
                generated_files=["Test.mq5"],
                created_by="test",
                expected_inputs=[
                    {"name": "InpDoesNotExist", "type": "double", "required": True, "default": "99.0"},
                ],
            )
            impl_mod.compile_check(db_path, result["implementation_request_id"], mock=True)
            impl_mod.run_diff_review(db_path, result["implementation_request_id"])
            guard = impl_mod.require_generated_baseline_approval(db_path, "mismatch_guard", "v1")
            self.assertFalse(guard["approved"])
            self.assertTrue(any("mismatch" in e.lower() for e in guard["errors"]))

    def test_approved_can_be_used_for_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            self._full_approval(db_path, "approved_guard", "v1")
            guard = impl_mod.require_generated_baseline_approval(
                db_path, "approved_guard", "v1", allow_mock_compile=True,
            )
            self.assertTrue(guard["approved"])

    def test_production_strategy_path_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite"
            guard = impl_mod.require_generated_baseline_approval(db_path, "nonexistent_strategy", "v1")
            self.assertTrue(guard["approved"])
            self.assertIn("No implementation request found", guard["note"])
