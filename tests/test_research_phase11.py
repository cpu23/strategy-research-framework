from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from automated.research import (
    cli,
    generated_baseline,
    implementation as impl_mod,
    intake,
    queue,
    registry,
    runner,
)
from automated.research.schemas import (
    GENERATED_SPECS_DIR,
    HYPOTHESES_DIR,
    REPO_ROOT,
    SANDBOX_ROOT,
)
from automated.research.contracts import (
    APPROVAL_SCOPES,
    APPROVAL_USAGE_STATUSES,
)


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


def _full_approval(
    db_path: str | Path,
    strategy_id: str,
    strategy_version: str,
    sandbox_root: Path | None = None,
) -> dict:
    sandbox = sandbox_root or SANDBOX_ROOT / strategy_id / strategy_version
    _write_sample_mq5(sandbox / f"{strategy_id}.mq5")
    req = impl_mod.create_implementation_request(
        db_path,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        sandbox_dir=sandbox,
        generated_files=[f"{strategy_id}.mq5"],
        created_by="test",
        expected_inputs=[
            {"name": "InpRiskPerTrade", "type": "double", "required": True, "default": "0.01"},
            {"name": "InpAtrPeriod", "type": "int", "required": True, "default": "14"},
            {"name": "InpUseSessionFilter", "type": "bool", "required": True, "default": "true"},
            {"name": "InpMagicNumber", "type": "int", "required": True, "default": "12345"},
        ],
    )
    impl_mod.compile_check(db_path, req["implementation_request_id"], mock=True)
    impl_mod.run_diff_review(db_path, req["implementation_request_id"])
    approve_result = impl_mod.approve_for_baseline(
        db_path,
        req["implementation_request_id"],
        approved_by="test",
        require_real_compile=False,
        approval_scope="baseline_only",
    )
    return approve_result


def _write_sample_generated_spec(strategy_id: str, mq5_path: Path) -> Path:
    spec_path = GENERATED_SPECS_DIR / f"{strategy_id}.yaml"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    spec = {
        "schema_version": "strategy_spec_v1",
        "strategy_id": strategy_id,
        "strategy_version": "v1",
        "hypothesis_id": "HYP_GEN_FBR_RANGING_000",
        "universe": ["XAUUSD"],
        "timeframe": "H4",
        "entry": {"rules": [{"condition": "RSI < 30", "action": "buy"}]},
        "exit": {"rules": [{"condition": "TP or SL", "action": "close"}]},
        "risk": {"max_risk_per_trade": 0.01},
        "parameters": {"InpRiskPerTrade": 0.01, "InpAtrPeriod": 14},
        "filters": [],
        "regime_filters": [],
        "implementation": {
            "engine": "mt5",
            "generation_mode": "generated",
            "files": {
                "expert_advisor": str(mq5_path),
                "config": str(REPO_ROOT / "automated" / "runs" / "example.conf"),
                "parameters": str(REPO_ROOT / "automated" / "runs" / "sets" / "example.set"),
            },
        },
        "execution_timing": {"max_spread_pips": 2.0, "slippage_pips": 1.0, "order_type": "market"},
        "costs": {"commission_per_lot": 0.0, "commission_type": "per_lot", "assumptions_documented": True},
        "validation": {"min_trades_required": 10, "min_profit_factor": 1.0},
        "research_budget": {"max_experiments": 5, "max_cost_stress_tests": 2},
        "lifecycle": {"current_state": "baseline_testing", "allowed_next_states": ["robustness_testing"]},
    }
    with spec_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(spec, f, sort_keys=False)
    return spec_path


class ResearchPhase11ApprovalScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_approve_baseline_stores_scope(self) -> None:
        result = _full_approval(self.db_path, "test_scope", "v1")
        self.assertTrue(result["approved"])
        self.assertEqual(result["approval_scope"], "baseline_only")

    def test_approve_baseline_default_scope(self) -> None:
        sandbox = SANDBOX_ROOT / "test_default_scope" / "v1"
        sandbox.mkdir(parents=True, exist_ok=True)
        _write_sample_mq5(sandbox / "test_default_scope.mq5")
        req = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id="test_default_scope",
            strategy_version="v1",
            sandbox_dir=sandbox,
            generated_files=["test_default_scope.mq5"],
            created_by="test",
        )
        impl_mod.compile_check(self.db_path, req["implementation_request_id"], mock=True)
        impl_mod.run_diff_review(self.db_path, req["implementation_request_id"])
        result = impl_mod.approve_for_baseline(
            self.db_path,
            req["implementation_request_id"],
            approved_by="test",
            require_real_compile=False,
        )
        self.assertTrue(result["approved"])
        impls = registry.list_implementations(self.db_path, req["implementation_request_id"])
        self.assertEqual(impls[-1].get("approval_scope"), "baseline_only")

    def test_approve_baseline_stores_allow_reuse(self) -> None:
        sandbox = SANDBOX_ROOT / "test_allow_reuse" / "v1"
        sandbox.mkdir(parents=True, exist_ok=True)
        _write_sample_mq5(sandbox / "test_allow_reuse.mq5")
        req = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id="test_allow_reuse",
            strategy_version="v1",
            sandbox_dir=sandbox,
            generated_files=["test_allow_reuse.mq5"],
            created_by="test",
        )
        impl_mod.compile_check(self.db_path, req["implementation_request_id"], mock=True)
        impl_mod.run_diff_review(self.db_path, req["implementation_request_id"])
        result = impl_mod.approve_for_baseline(
            self.db_path,
            req["implementation_request_id"],
            approved_by="test",
            require_real_compile=False,
            allow_reuse=True,
        )
        self.assertTrue(result["allow_reuse"])
        impls = registry.list_implementations(self.db_path, req["implementation_request_id"])
        self.assertEqual(impls[-1].get("allow_reuse"), 1)

    def test_approve_baseline_rejects_invalid_scope(self) -> None:
        sandbox = SANDBOX_ROOT / "test_invalid_scope" / "v1"
        sandbox.mkdir(parents=True, exist_ok=True)
        _write_sample_mq5(sandbox / "test_invalid_scope.mq5")
        req = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id="test_invalid_scope",
            strategy_version="v1",
            sandbox_dir=sandbox,
            generated_files=["test_invalid_scope.mq5"],
            created_by="test",
        )
        impl_mod.compile_check(self.db_path, req["implementation_request_id"], mock=True)
        with self.assertRaises(ValueError):
            impl_mod.approve_for_baseline(
                self.db_path,
                req["implementation_request_id"],
                approved_by="test",
                require_real_compile=False,
                approval_scope="full",
            )


class ResearchPhase11GuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        self.sandbox = SANDBOX_ROOT / "gb_guard_test" / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / "gb_guard_test.mq5"
        _write_sample_mq5(self.mq5_path)
        self.spec_path = _write_sample_generated_spec("gb_guard_test", self.mq5_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        spec_p = GENERATED_SPECS_DIR / "gb_guard_test.yaml"
        if spec_p.is_file():
            spec_p.unlink()
        import shutil
        if self.sandbox.is_dir():
            shutil.rmtree(self.sandbox)

    def _create_approval(self) -> dict:
        req = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id="gb_guard_test",
            strategy_version="v1",
            sandbox_dir=self.sandbox,
            generated_files=["gb_guard_test.mq5"],
            created_by="test",
            expected_inputs=[
                {"name": "InpRiskPerTrade", "type": "double", "required": True, "default": "0.01"},
                {"name": "InpAtrPeriod", "type": "int", "required": True, "default": "14"},
                {"name": "InpUseSessionFilter", "type": "bool", "required": True, "default": "true"},
                {"name": "InpMagicNumber", "type": "int", "required": True, "default": "12345"},
            ],
        )
        impl_mod.compile_check(self.db_path, req["implementation_request_id"], mock=True)
        impl_mod.run_diff_review(self.db_path, req["implementation_request_id"])
        return impl_mod.approve_for_baseline(
            self.db_path,
            req["implementation_request_id"],
            approved_by="test",
            require_real_compile=False,
        )

    def test_guard_passes_with_full_approval(self) -> None:
        result = self._create_approval()
        self.assertTrue(result["approved"])
        guard = impl_mod.require_generated_baseline_approval(
            self.db_path, "gb_guard_test", "v1",
            allow_mock_compile=True, check_scope=True,
        )
        self.assertTrue(guard["approved"])

    def test_blocked_without_approval(self) -> None:
        sandbox = SANDBOX_ROOT / "gb_no_approve" / "v1"
        sandbox.mkdir(parents=True, exist_ok=True)
        _write_sample_mq5(sandbox / "gb_no_approve.mq5")
        req = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id="gb_no_approve",
            strategy_version="v1",
            sandbox_dir=sandbox,
            generated_files=["gb_no_approve.mq5"],
            created_by="test",
        )
        impl_mod.compile_check(self.db_path, req["implementation_request_id"], mock=True)
        impl_mod.run_diff_review(self.db_path, req["implementation_request_id"])
        guard = impl_mod.require_generated_baseline_approval(
            self.db_path, "gb_no_approve", "v1",
            allow_mock_compile=True, check_scope=True,
        )
        self.assertFalse(guard["approved"])
        self.assertTrue(any("approve" in e.lower() for e in guard["errors"]))
        import shutil
        if sandbox.is_dir():
            shutil.rmtree(sandbox)

    def test_blocked_if_compile_failed(self) -> None:
        sandbox = SANDBOX_ROOT / "gb_compile_fail" / "v1"
        sandbox.mkdir(parents=True, exist_ok=True)
        _write_sample_mq5(sandbox / "gb_compile_fail.mq5")
        req = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id="gb_compile_fail",
            strategy_version="v1",
            sandbox_dir=sandbox,
            generated_files=["gb_compile_fail.mq5"],
            created_by="test",
        )
        impl_mod.compile_check(self.db_path, req["implementation_request_id"], mock=True)
        impls = registry.list_implementations(self.db_path, req["implementation_request_id"])
        impl_id = impls[-1]["implementation_id"] if impls else None
        if impl_id:
            registry.update_implementation(self.db_path, impl_id, compile_status="failed")
        guard = impl_mod.require_generated_baseline_approval(
            self.db_path, "gb_compile_fail", "v1",
            check_scope=True,
        )
        self.assertFalse(guard["approved"])
        self.assertTrue(any("failed" in e.lower() for e in guard["errors"]))
        import shutil
        if sandbox.is_dir():
            shutil.rmtree(sandbox)

    def test_blocked_if_mock_compile_without_allow_mock(self) -> None:
        sandbox = SANDBOX_ROOT / "gb_mock_only" / "v1"
        sandbox.mkdir(parents=True, exist_ok=True)
        _write_sample_mq5(sandbox / "gb_mock_only.mq5")
        req = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id="gb_mock_only",
            strategy_version="v1",
            sandbox_dir=sandbox,
            generated_files=["gb_mock_only.mq5"],
            created_by="test",
        )
        impl_mod.compile_check(self.db_path, req["implementation_request_id"], mock=True)
        guard = impl_mod.require_generated_baseline_approval(
            self.db_path, "gb_mock_only", "v1",
            allow_mock_compile=False, check_scope=True,
        )
        self.assertFalse(guard["approved"])
        self.assertTrue(any("mock" in e.lower() for e in guard["errors"]))
        import shutil
        if sandbox.is_dir():
            shutil.rmtree(sandbox)

    def test_allowed_if_mock_compile_with_explicit_allow(self) -> None:
        sandbox = SANDBOX_ROOT / "gb_mock_allowed" / "v1"
        sandbox.mkdir(parents=True, exist_ok=True)
        _write_sample_mq5(sandbox / "gb_mock_allowed.mq5")
        req = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id="gb_mock_allowed",
            strategy_version="v1",
            sandbox_dir=sandbox,
            generated_files=["gb_mock_allowed.mq5"],
            created_by="test",
            expected_inputs=[
                {"name": "InpRiskPerTrade", "type": "double", "required": True, "default": "0.01"},
                {"name": "InpAtrPeriod", "type": "int", "required": True, "default": "14"},
                {"name": "InpUseSessionFilter", "type": "bool", "required": True, "default": "true"},
                {"name": "InpMagicNumber", "type": "int", "required": True, "default": "12345"},
            ],
        )
        impl_mod.compile_check(self.db_path, req["implementation_request_id"], mock=True)
        impl_mod.run_diff_review(self.db_path, req["implementation_request_id"])
        impl_mod.approve_for_baseline(
            self.db_path,
            req["implementation_request_id"],
            approved_by="test",
            require_real_compile=False,
        )
        guard = impl_mod.require_generated_baseline_approval(
            self.db_path, "gb_mock_allowed", "v1",
            allow_mock_compile=True, check_scope=True,
        )
        self.assertTrue(guard["approved"])
        import shutil
        if sandbox.is_dir():
            shutil.rmtree(sandbox)

    def test_blocked_if_input_mismatch(self) -> None:
        sandbox = SANDBOX_ROOT / "gb_input_mismatch" / "v1"
        sandbox.mkdir(parents=True, exist_ok=True)
        _write_sample_mq5(sandbox / "gb_input_mismatch.mq5")
        req = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id="gb_input_mismatch",
            strategy_version="v1",
            sandbox_dir=sandbox,
            generated_files=["gb_input_mismatch.mq5"],
            created_by="test",
            expected_inputs=[
                {"name": "InpDoesNotExist", "type": "double", "required": True, "default": "99.0"},
            ],
        )
        impl_mod.compile_check(self.db_path, req["implementation_request_id"], mock=True)
        impl_mod.run_diff_review(self.db_path, req["implementation_request_id"])
        guard = impl_mod.require_generated_baseline_approval(
            self.db_path, "gb_input_mismatch", "v1",
            allow_mock_compile=True, check_scope=True,
        )
        self.assertFalse(guard["approved"])
        self.assertTrue(any("mismatch" in e.lower() for e in guard["errors"]))
        import shutil
        if sandbox.is_dir():
            shutil.rmtree(sandbox)

    def test_blocked_if_diff_review_missing(self) -> None:
        sandbox = SANDBOX_ROOT / "gb_no_diff" / "v1"
        sandbox.mkdir(parents=True, exist_ok=True)
        _write_sample_mq5(sandbox / "gb_no_diff.mq5")
        req = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id="gb_no_diff",
            strategy_version="v1",
            sandbox_dir=sandbox,
            generated_files=["gb_no_diff.mq5"],
            created_by="test",
        )
        impl_mod.compile_check(self.db_path, req["implementation_request_id"], mock=True)
        guard = impl_mod.require_generated_baseline_approval(
            self.db_path, "gb_no_diff", "v1",
            allow_mock_compile=True, check_scope=True,
        )
        self.assertFalse(guard["approved"])
        self.assertTrue(any("diff" in e.lower() for e in guard["errors"]))
        import shutil
        if sandbox.is_dir():
            shutil.rmtree(sandbox)


class ResearchPhase11ApprovalConsumptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        self.sandbox = SANDBOX_ROOT / "gb_consume_test" / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / "gb_consume_test.mq5"
        _write_sample_mq5(self.mq5_path)
        self.spec_path = _write_sample_generated_spec("gb_consume_test", self.mq5_path)
        self.approval = _full_approval(self.db_path, "gb_consume_test", "v1", self.sandbox)
        self.impl_id = self.approval["implementation_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        spec_p = GENERATED_SPECS_DIR / "gb_consume_test.yaml"
        if spec_p.is_file():
            spec_p.unlink()
        import shutil
        if self.sandbox.is_dir():
            shutil.rmtree(self.sandbox)

    def test_approval_usage_record_created(self) -> None:
        usage_id = f"USAGE_test_{registry.utc_now()}"
        registry.create_approval_usage(self.db_path, {
            "usage_id": usage_id,
            "implementation_id": self.impl_id,
            "implementation_request_id": self.approval["implementation_request_id"],
            "experiment_id": "EXP_test_001",
            "queue_run_id": None,
            "used_at": registry.utc_now(),
            "runner_mode": "test",
            "status": "pending",
        })
        record = registry.get_approval_usage(self.db_path, usage_id)
        self.assertIsNotNone(record)
        self.assertEqual(record["implementation_id"], self.impl_id)
        self.assertEqual(record["status"], "pending")

    def test_approval_usage_update_status(self) -> None:
        usage_id = f"USAGE_test_complete_{registry.utc_now()}"
        registry.create_approval_usage(self.db_path, {
            "usage_id": usage_id,
            "implementation_id": self.impl_id,
            "implementation_request_id": self.approval["implementation_request_id"],
            "experiment_id": "EXP_test_002",
            "queue_run_id": None,
            "used_at": registry.utc_now(),
            "runner_mode": "test",
            "status": "pending",
        })
        registry.update_approval_usage(self.db_path, usage_id, status="completed")
        record = registry.get_approval_usage(self.db_path, usage_id)
        self.assertEqual(record["status"], "completed")

    def test_approval_usage_failed_status(self) -> None:
        usage_id = f"USAGE_test_fail_{registry.utc_now()}"
        registry.create_approval_usage(self.db_path, {
            "usage_id": usage_id,
            "implementation_id": self.impl_id,
            "implementation_request_id": self.approval["implementation_request_id"],
            "experiment_id": "EXP_test_003",
            "queue_run_id": None,
            "used_at": registry.utc_now(),
            "runner_mode": "test",
            "status": "pending",
        })
        registry.update_approval_usage(self.db_path, usage_id, status="failed")
        record = registry.get_approval_usage(self.db_path, usage_id)
        self.assertEqual(record["status"], "failed")

    def test_same_approval_cannot_be_reused_by_default(self) -> None:
        usage_id = f"USAGE_test_reuse_{registry.utc_now()}"
        registry.create_approval_usage(self.db_path, {
            "usage_id": usage_id,
            "implementation_id": self.impl_id,
            "implementation_request_id": self.approval["implementation_request_id"],
            "experiment_id": "EXP_test_004",
            "queue_run_id": None,
            "used_at": registry.utc_now(),
            "runner_mode": "test",
            "status": "completed",
        })
        count = registry.count_approval_usage_for_implementation(self.db_path, self.impl_id)
        self.assertGreater(count, 0)
        guard = impl_mod.require_generated_baseline_approval(
            self.db_path, "gb_consume_test", "v1",
            allow_mock_compile=True, check_scope=True, check_consumed=True,
        )
        self.assertFalse(guard["approved"])
        self.assertTrue(any("consumed" in e.lower() for e in guard["errors"]))

    def test_reuse_allowed_with_explicit_flag(self) -> None:
        impls = registry.list_implementations(self.db_path, self.approval["implementation_request_id"])
        current_impl = impls[-1]
        registry.update_implementation(self.db_path, current_impl["implementation_id"], allow_reuse=1)
        usage_id = f"USAGE_test_reuse2_{registry.utc_now()}"
        registry.create_approval_usage(self.db_path, {
            "usage_id": usage_id,
            "implementation_id": self.impl_id,
            "implementation_request_id": self.approval["implementation_request_id"],
            "experiment_id": "EXP_test_005",
            "queue_run_id": None,
            "used_at": registry.utc_now(),
            "runner_mode": "test",
            "status": "completed",
        })
        guard = impl_mod.require_generated_baseline_approval(
            self.db_path, "gb_consume_test", "v1",
            allow_mock_compile=True, check_scope=True, check_consumed=True,
        )
        self.assertTrue(guard["approved"])

    def test_usage_count_function(self) -> None:
        self.assertEqual(
            registry.count_approval_usage_for_implementation(self.db_path, self.impl_id), 0
        )
        registry.create_approval_usage(self.db_path, {
            "usage_id": "USAGE_cnt_1",
            "implementation_id": self.impl_id,
            "implementation_request_id": self.approval["implementation_request_id"],
            "experiment_id": "EXP_cnt_1",
            "queue_run_id": None,
            "used_at": registry.utc_now(),
            "runner_mode": "test",
            "status": "completed",
        })
        self.assertEqual(
            registry.count_approval_usage_for_implementation(self.db_path, self.impl_id), 1
        )


class ResearchPhase11GeneratedBaselineRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        self.sandbox = SANDBOX_ROOT / "gb_run_test" / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / "gb_run_test.mq5"
        _write_sample_mq5(self.mq5_path)
        self.spec_path = _write_sample_generated_spec("gb_run_test", self.mq5_path)
        self.approval = _full_approval(self.db_path, "gb_run_test", "v1", self.sandbox)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        spec_p = GENERATED_SPECS_DIR / "gb_run_test.yaml"
        if spec_p.is_file():
            spec_p.unlink()
        import shutil
        if self.sandbox.is_dir():
            shutil.rmtree(self.sandbox)

    def test_guard_blocks_missing_approval(self) -> None:
        guard = impl_mod.require_generated_baseline_approval(
            self.db_path, "nonexistent_gb", "v1",
            check_scope=True,
        )
        self.assertTrue(guard["approved"])
        self.assertIn("No implementation request found", guard["note"])

    def test_guard_blocks_wrong_scope(self) -> None:
        impls = registry.list_implementations(self.db_path, self.approval["implementation_request_id"])
        current_impl = impls[-1]
        registry.update_implementation(
            self.db_path, current_impl["implementation_id"],
            approval_scope="full",
        )
        guard = impl_mod.require_generated_baseline_approval(
            self.db_path, "gb_run_test", "v1",
            allow_mock_compile=True, check_scope=True,
        )
        self.assertFalse(guard["approved"])
        self.assertTrue(any("scope" in e.lower() for e in guard["errors"]))

    def test_approved_reaches_prepare_run(self) -> None:
        guard = impl_mod.require_generated_baseline_approval(
            self.db_path, "gb_run_test", "v1",
            allow_mock_compile=True, check_scope=True,
        )
        self.assertTrue(guard["approved"])

    def test_approval_usage_created_on_prepare(self) -> None:
        usage_id = f"USAGE_run_test_{registry.utc_now()}"
        registry.create_approval_usage(self.db_path, {
            "usage_id": usage_id,
            "implementation_id": self.approval["implementation_id"],
            "implementation_request_id": self.approval["implementation_request_id"],
            "experiment_id": "EXP_gb_run_001",
            "queue_run_id": None,
            "used_at": registry.utc_now(),
            "runner_mode": "test",
            "status": "pending",
        })
        record = registry.get_approval_usage(self.db_path, usage_id)
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "pending")
        registry.update_approval_usage(self.db_path, usage_id, status="completed")
        record = registry.get_approval_usage(self.db_path, usage_id)
        self.assertEqual(record["status"], "completed")

    def test_mq5_not_copied_to_strategies(self) -> None:
        strategies_root = REPO_ROOT / "automated" / "strategies"
        if strategies_root.is_dir():
            gb_files = list(strategies_root.rglob("gb_run_test*"))
            self.assertEqual(len(gb_files), 0, f"Found generated files in strategies/: {gb_files}")

    def test_generated_baseline_experiment_in_task_types(self) -> None:
        self.assertIn("generated_baseline_experiment", queue.TASK_TYPES)
        self.assertIn("generated_baseline_review", queue.TASK_TYPES)


class ResearchPhase11RedTeamTests(unittest.TestCase):
    def test_low_trade_count_warning(self) -> None:
        result = generated_baseline.red_team_check_generated_baseline(
            metrics={"trade_count": 3, "profit_factor": 2.0, "net_return": 0.05, "max_drawdown": 10},
            validation_report=None,
            diff_review_warnings=None,
            compile_status="passed",
            universe=["XAUUSD"],
            timeframes=["H4"],
            min_trades_required=10,
        )
        self.assertTrue(any("low trade count" in w.lower() for w in result["warnings"]))
        self.assertIn("low_trade_count", result["risk_flags"])

    def test_profit_factor_below_breakeven(self) -> None:
        result = generated_baseline.red_team_check_generated_baseline(
            metrics={"trade_count": 50, "profit_factor": 0.8, "net_return": -0.02, "max_drawdown": 10},
            validation_report=None,
            diff_review_warnings=None,
            compile_status="passed",
            universe=["XAUUSD"],
            timeframes=["H4"],
            min_trades_required=10,
        )
        self.assertTrue(any("below breakeven" in w.lower() for w in result["warnings"]))
        self.assertIn("profit_factor_below_breakeven", result["risk_flags"])

    def test_profit_factor_marginal(self) -> None:
        result = generated_baseline.red_team_check_generated_baseline(
            metrics={"trade_count": 50, "profit_factor": 1.2, "net_return": 0.03, "max_drawdown": 10},
            validation_report=None,
            diff_review_warnings=None,
            compile_status="passed",
            universe=["XAUUSD"],
            timeframes=["H4"],
            min_trades_required=10,
        )
        self.assertTrue(any("marginal" in w.lower() for w in result["warnings"]))
        self.assertIn("profit_factor_marginal", result["risk_flags"])

    def test_negative_net_return_flagged(self) -> None:
        result = generated_baseline.red_team_check_generated_baseline(
            metrics={"trade_count": 50, "profit_factor": 0.9, "net_return": -0.05, "max_drawdown": 10},
            validation_report=None,
            diff_review_warnings=None,
            compile_status="passed",
            universe=["XAUUSD"],
            timeframes=["H4"],
            min_trades_required=10,
        )
        self.assertTrue(any("negative" in w.lower() for w in result["warnings"]))
        self.assertIn("negative_net_return", result["risk_flags"])

    def test_high_drawdown_flagged(self) -> None:
        result = generated_baseline.red_team_check_generated_baseline(
            metrics={"trade_count": 50, "profit_factor": 1.8, "net_return": 0.10, "max_drawdown": 35},
            validation_report=None,
            diff_review_warnings=None,
            compile_status="passed",
            universe=["XAUUSD"],
            timeframes=["H4"],
            min_trades_required=10,
        )
        self.assertTrue(any("drawdown" in w.lower() for w in result["warnings"]))
        self.assertIn("high_drawdown", result["risk_flags"])

    def test_single_symbol_flagged(self) -> None:
        result = generated_baseline.red_team_check_generated_baseline(
            metrics={"trade_count": 50, "profit_factor": 2.0, "net_return": 0.08, "max_drawdown": 10},
            validation_report=None,
            diff_review_warnings=None,
            compile_status="passed",
            universe=["XAUUSD"],
            timeframes=["H4"],
            min_trades_required=10,
        )
        self.assertTrue(any("single-symbol" in w.lower() for w in result["warnings"]))

    def test_single_timeframe_flagged(self) -> None:
        result = generated_baseline.red_team_check_generated_baseline(
            metrics={"trade_count": 50, "profit_factor": 2.0, "net_return": 0.08, "max_drawdown": 10},
            validation_report=None,
            diff_review_warnings=None,
            compile_status="passed",
            universe=["XAUUSD", "EURUSD"],
            timeframes=["H4"],
            min_trades_required=10,
        )
        self.assertTrue(any("single-timeframe" in w.lower() for w in result["warnings"]))

    def test_mock_compile_flagged(self) -> None:
        result = generated_baseline.red_team_check_generated_baseline(
            metrics={"trade_count": 50, "profit_factor": 2.0, "net_return": 0.08, "max_drawdown": 10},
            validation_report=None,
            diff_review_warnings=None,
            compile_status="mock_checked",
            universe=["XAUUSD"],
            timeframes=["H4"],
            min_trades_required=10,
        )
        self.assertTrue(any("mock" in w.lower() for w in result["warnings"]))

    def test_no_warnings_for_good_metrics(self) -> None:
        result = generated_baseline.red_team_check_generated_baseline(
            metrics={"trade_count": 100, "profit_factor": 2.5, "net_return": 0.15, "max_drawdown": 15},
            validation_report=None,
            diff_review_warnings=None,
            compile_status="passed",
            universe=["XAUUSD", "EURUSD"],
            timeframes=["H4", "D1"],
            min_trades_required=10,
        )
        self.assertEqual(len(result["warnings"]), 0)
        self.assertEqual(result["overall_assessment"], "low_risk")

    def test_diff_review_warnings_passed_through(self) -> None:
        result = generated_baseline.red_team_check_generated_baseline(
            metrics={"trade_count": 100, "profit_factor": 2.0, "net_return": 0.10, "max_drawdown": 10},
            validation_report=None,
            diff_review_warnings=["[medium] Hardcoded lot size detected"],
            compile_status="passed",
            universe=["XAUUSD"],
            timeframes=["H4"],
            min_trades_required=10,
        )
        self.assertTrue(any("lot size" in w.lower() for w in result["warnings"]))
        self.assertIn("diff_review_warnings", result["risk_flags"])


class ResearchPhase11ReviewPacketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        self.sandbox = SANDBOX_ROOT / "gb_review_test" / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / "gb_review_test.mq5"
        _write_sample_mq5(self.mq5_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        import shutil
        if self.sandbox.is_dir():
            shutil.rmtree(self.sandbox)

    def _create_approval_and_experiment(self) -> tuple[str, dict]:
        approval = _full_approval(self.db_path, "gb_review_test", "v1", self.sandbox)
        experiment_id = "EXP_review_test_001"
        registry.init_db(self.db_path)
        registry.insert_dataset(self.db_path, {
            "dataset_id": "DS_test",
            "source_type": "csv",
            "source_name": "test",
            "broker": "test",
            "server": "test",
            "symbol": "XAUUSD",
            "timeframe": "H4",
            "start_ts": "2020-01-01T00:00:00Z",
            "end_ts": "2020-12-31T00:00:00Z",
            "row_count": 1000,
            "file_path": str(Path(self.temp_dir.name) / "test_bars.csv"),
            "file_hash": "fake_hash",
            "exported_at": "2026-01-01T00:00:00Z",
            "timezone": "UTC",
            "missing_data_policy": "ignore",
            "cleaning_rules": "none",
            "created_at": registry.utc_now(),
            "metadata_json": "{}",
        })
        self._create_experiment_record(experiment_id)
        return experiment_id, approval

    def _create_experiment_record(self, experiment_id: str) -> None:
        registry.init_db(self.db_path)
        payload = {
            "experiment_id": experiment_id,
            "hypothesis_id": "HYP_GEN_FBR_RANGING_000",
            "strategy_id": "gb_review_test",
            "strategy_version": "v1",
            "run_reason": "test",
            "created_by": "test",
            "created_at": registry.utc_now(),
            "spec_hash": "fake_hash",
            "parameter_set_hash": "fake_hash",
            "dataset_id": "DS_test",
            "dataset_bundle_id": None,
            "dataset_hash": "fake_hash",
            "dataset_bundle_hash": None,
            "code_version": "test",
            "execution_config_hash": "fake_hash",
            "cost_config_hash": "fake_hash",
            "engine": "mt5",
            "implementation_files": {},
            "implementation_mode": "generated",
            "execution_timing": {},
            "timeframe": "H4",
            "universe": ["XAUUSD"],
            "parent_experiment_id": None,
            "rerun_of_experiment_id": None,
            "is_artifact_regeneration": False,
            "change_type": "baseline",
            "change_summary": "test",
            "rationale": "test",
            "research_budget_snapshot": {},
            "complexity_score": 0,
            "min_trades_required": 10,
            "cost_assumptions_documented": True,
            "dataset_metadata_present": True,
            "hypothesis_present": True,
            "validation_report_path": None,
            "gate_status": "incomplete",
            "started_at": None,
            "completed_at": None,
            "status": "completed",
        }
        registry.create_experiment(self.db_path, payload)

    def test_review_artifact_created(self) -> None:
        experiment_id, approval = self._create_approval_and_experiment()
        output_dir = Path(self.temp_dir.name) / "review_output"
        review = generated_baseline.build_generated_baseline_review_packet(
            self.db_path,
            experiment_id=experiment_id,
            strategy_id="gb_review_test",
            strategy_version="v1",
            implementation_request_id=approval["implementation_request_id"],
            implementation_id=approval["implementation_id"],
            approval_status="approved_for_baseline",
            approval_usage=None,
            runner_mode="test",
            output_dir=output_dir,
        )
        self.assertIn("packet_path", review)
        packet_path = Path(review["packet_path"])
        self.assertTrue(packet_path.is_file())
        import yaml
        packet = yaml.safe_load(packet_path.read_text(encoding="utf-8"))
        self.assertEqual(packet["schema_version"], "generated_baseline_review_v1")
        self.assertEqual(packet["experiment_id"], experiment_id)

    def test_review_recommendation_never_promote_to_production(self) -> None:
        experiment_id, approval = self._create_approval_and_experiment()
        output_dir = Path(self.temp_dir.name) / "review_output2"
        review = generated_baseline.build_generated_baseline_review_packet(
            self.db_path,
            experiment_id=experiment_id,
            strategy_id="gb_review_test",
            strategy_version="v1",
            implementation_request_id=approval["implementation_request_id"],
            implementation_id=approval["implementation_id"],
            approval_status="approved_for_baseline",
            approval_usage=None,
            runner_mode="test",
            output_dir=output_dir,
        )
        import yaml
        packet = yaml.safe_load(Path(review["packet_path"]).read_text(encoding="utf-8"))
        self.assertNotEqual(packet["recommendation"], "promote_to_production")
        self.assertIn(packet["recommendation"], generated_baseline.ALLOWED_RECOMMENDATIONS)

    def test_review_has_required_fields(self) -> None:
        experiment_id, approval = self._create_approval_and_experiment()
        output_dir = Path(self.temp_dir.name) / "review_output3"
        review = generated_baseline.build_generated_baseline_review_packet(
            self.db_path,
            experiment_id=experiment_id,
            strategy_id="gb_review_test",
            strategy_version="v1",
            implementation_request_id=approval["implementation_request_id"],
            implementation_id=approval["implementation_id"],
            approval_status="approved_for_baseline",
            approval_usage=None,
            runner_mode="test",
            output_dir=output_dir,
        )
        import yaml
        packet = yaml.safe_load(Path(review["packet_path"]).read_text(encoding="utf-8"))
        for field in [
            "schema_version", "strategy_id", "strategy_version",
            "implementation_request_id", "implementation_id",
            "approval_status", "experiment_id", "runner_mode",
            "baseline_metrics", "validation_gate_status",
            "recommendation", "red_team_results",
        ]:
            self.assertIn(field, packet, f"Missing field: {field}")

    def test_review_red_team_appears(self) -> None:
        experiment_id, approval = self._create_approval_and_experiment()
        output_dir = Path(self.temp_dir.name) / "review_output4"
        review = generated_baseline.build_generated_baseline_review_packet(
            self.db_path,
            experiment_id=experiment_id,
            strategy_id="gb_review_test",
            strategy_version="v1",
            implementation_request_id=approval["implementation_request_id"],
            implementation_id=approval["implementation_id"],
            approval_status="approved_for_baseline",
            approval_usage=None,
            runner_mode="test",
            output_dir=output_dir,
        )
        import yaml
        packet = yaml.safe_load(Path(review["packet_path"]).read_text(encoding="utf-8"))
        self.assertIn("red_team_results", packet)
        self.assertIn("warnings", packet["red_team_results"])
        self.assertIn("overall_assessment", packet["red_team_results"])
        self.assertIn("risk_flags", packet["red_team_results"])


class ResearchPhase11QueueIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        self.sandbox = SANDBOX_ROOT / "gb_queue_test" / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / "gb_queue_test.mq5"
        _write_sample_mq5(self.mq5_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        import shutil
        if self.sandbox.is_dir():
            shutil.rmtree(self.sandbox)

    def test_generated_baseline_experiment_task_in_queue_types(self) -> None:
        self.assertIn("generated_baseline_experiment", queue.TASK_TYPES)
        self.assertIn("generated_baseline_review", queue.TASK_TYPES)

    def test_queue_validation_requires_gb_fields(self) -> None:
        import yaml
        queue_path = Path(self.temp_dir.name) / "test_gb_queue.yaml"
        queue_data = {
            "queue_id": "test_gb_queue",
            "items": [
                {
                    "queue_id": "gb_item_001",
                    "priority": 10,
                    "task_type": "generated_baseline_experiment",
                    "hypothesis_id": "HYP_TEST",
                    "strategy_id": "gb_queue_test",
                    "strategy_version": "v1",
                    "implementation_request_id": "IMPL_REQ_GB_001",
                    "dataset_id": "DS_TEST",
                    "requested_by": "test",
                    "created_at": "2026-05-12T00:00:00+00:00",
                    "allowed_agent_roles": ["backtest_runner"],
                    "budget": {"max_experiments": 1, "max_runtime_minutes": 10},
                    "permissions": {
                        "allow_runner_execution": True,
                        "allow_mql5_edits": False,
                        "allow_dataset_changes": False,
                        "allow_validation_threshold_changes": False,
                        "allow_lifecycle_apply": False,
                        "allow_lifecycle_propose": False,
                        "allow_final_holdout": False,
                    },
                    "status": "queued",
                }
            ],
        }
        queue_path.write_text(yaml.safe_dump(queue_data, sort_keys=False), encoding="utf-8")
        result = queue.validate_queue(self.db_path, queue_path)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["task_type"], "generated_baseline_experiment")

    def test_requires_runner_execution_true(self) -> None:
        import yaml
        queue_path = Path(self.temp_dir.name) / "test_gb_queue_block.yaml"
        queue_data = {
            "queue_id": "test_gb_block",
            "items": [
                {
                    "queue_id": "gb_block_001",
                    "priority": 10,
                    "task_type": "generated_baseline_experiment",
                    "hypothesis_id": "HYP_TEST",
                    "strategy_id": "gb_queue_test",
                    "strategy_version": "v1",
                    "implementation_request_id": "IMPL_TEST",
                    "dataset_id": "DS_TEST",
                    "requested_by": "test",
                    "created_at": "2026-05-12T00:00:00+00:00",
                    "allowed_agent_roles": ["backtest_runner"],
                    "budget": {"max_experiments": 1, "max_runtime_minutes": 10},
                    "permissions": {
                        "allow_runner_execution": True,
                        "allow_mql5_edits": False,
                        "allow_dataset_changes": False,
                        "allow_validation_threshold_changes": False,
                        "allow_lifecycle_apply": False,
                        "allow_lifecycle_propose": False,
                        "allow_final_holdout": False,
                    },
                    "status": "queued",
                }
            ],
        }
        queue_path.write_text(yaml.safe_dump(queue_data, sort_keys=False), encoding="utf-8")
        result = queue.validate_queue(self.db_path, queue_path)
        self.assertEqual(result["status"], "valid")

    def test_blocked_if_runner_execution_false(self) -> None:
        import yaml
        queue_path = Path(self.temp_dir.name) / "test_gb_block_re.yaml"
        queue_data = {
            "queue_id": "test_gb_block_re",
            "items": [
                {
                    "queue_id": "gb_block_re_001",
                    "priority": 10,
                    "task_type": "generated_baseline_experiment",
                    "hypothesis_id": "HYP_TEST",
                    "strategy_id": "gb_queue_test",
                    "strategy_version": "v1",
                    "implementation_request_id": "IMPL_TEST",
                    "dataset_id": "DS_TEST",
                    "requested_by": "test",
                    "created_at": "2026-05-12T00:00:00+00:00",
                    "allowed_agent_roles": ["backtest_runner"],
                    "budget": {"max_experiments": 1, "max_runtime_minutes": 10},
                    "permissions": {
                        "allow_runner_execution": False,
                        "allow_mql5_edits": False,
                        "allow_dataset_changes": False,
                        "allow_validation_threshold_changes": False,
                        "allow_lifecycle_apply": False,
                        "allow_lifecycle_propose": False,
                        "allow_final_holdout": False,
                    },
                    "status": "queued",
                }
            ],
        }
        queue_path.write_text(yaml.safe_dump(queue_data, sort_keys=False), encoding="utf-8")
        with self.assertRaises(queue.SchemaValidationError):
            queue.validate_queue(self.db_path, queue_path)

    def test_blocked_if_final_holdout_true(self) -> None:
        import yaml
        queue_path = Path(self.temp_dir.name) / "test_gb_block_fh.yaml"
        queue_data = {
            "queue_id": "test_gb_block_fh",
            "items": [
                {
                    "queue_id": "gb_block_fh_001",
                    "priority": 10,
                    "task_type": "generated_baseline_experiment",
                    "hypothesis_id": "HYP_TEST",
                    "strategy_id": "gb_queue_test",
                    "strategy_version": "v1",
                    "implementation_request_id": "IMPL_TEST",
                    "dataset_id": "DS_TEST",
                    "requested_by": "test",
                    "created_at": "2026-05-12T00:00:00+00:00",
                    "allowed_agent_roles": ["backtest_runner"],
                    "budget": {"max_experiments": 1, "max_runtime_minutes": 10},
                    "permissions": {
                        "allow_runner_execution": True,
                        "allow_mql5_edits": False,
                        "allow_dataset_changes": False,
                        "allow_validation_threshold_changes": False,
                        "allow_lifecycle_apply": False,
                        "allow_lifecycle_propose": False,
                        "allow_final_holdout": True,
                    },
                    "status": "queued",
                }
            ],
        }
        queue_path.write_text(yaml.safe_dump(queue_data, sort_keys=False), encoding="utf-8")
        with self.assertRaises(queue.SchemaValidationError):
            queue.validate_queue(self.db_path, queue_path)

    def test_blocked_if_lifecycle_apply_true(self) -> None:
        import yaml
        queue_path = Path(self.temp_dir.name) / "test_gb_block_la.yaml"
        queue_data = {
            "queue_id": "test_gb_block_la",
            "items": [
                {
                    "queue_id": "gb_block_la_001",
                    "priority": 10,
                    "task_type": "generated_baseline_experiment",
                    "hypothesis_id": "HYP_TEST",
                    "strategy_id": "gb_queue_test",
                    "strategy_version": "v1",
                    "implementation_request_id": "IMPL_TEST",
                    "dataset_id": "DS_TEST",
                    "requested_by": "test",
                    "created_at": "2026-05-12T00:00:00+00:00",
                    "allowed_agent_roles": ["backtest_runner"],
                    "budget": {"max_experiments": 1, "max_runtime_minutes": 10},
                    "permissions": {
                        "allow_runner_execution": True,
                        "allow_mql5_edits": False,
                        "allow_dataset_changes": False,
                        "allow_validation_threshold_changes": False,
                        "allow_lifecycle_apply": True,
                        "allow_lifecycle_propose": False,
                        "allow_final_holdout": False,
                    },
                    "status": "queued",
                }
            ],
        }
        queue_path.write_text(yaml.safe_dump(queue_data, sort_keys=False), encoding="utf-8")
        with self.assertRaises(queue.SchemaValidationError):
            queue.validate_queue(self.db_path, queue_path)

    def test_generated_baseline_review_task_in_queue_validates(self) -> None:
        import yaml
        queue_path = Path(self.temp_dir.name) / "test_gb_review_queue.yaml"
        queue_data = {
            "queue_id": "test_gb_review_queue",
            "items": [
                {
                    "queue_id": "gb_review_item_001",
                    "priority": 20,
                    "task_type": "generated_baseline_review",
                    "strategy_id": "gb_queue_test",
                    "strategy_version": "v1",
                    "parent_experiment_id": "EXP_some_experiment",
                    "requested_by": "test",
                    "created_at": "2026-05-12T00:00:00+00:00",
                    "allowed_agent_roles": ["statistical_reviewer"],
                    "budget": {"max_experiments": 0, "max_runtime_minutes": 10},
                    "permissions": {
                        "allow_runner_execution": False,
                        "allow_mql5_edits": False,
                        "allow_dataset_changes": False,
                        "allow_validation_threshold_changes": False,
                        "allow_lifecycle_apply": False,
                        "allow_lifecycle_propose": False,
                        "allow_final_holdout": False,
                    },
                    "status": "queued",
                }
            ],
        }
        queue_path.write_text(yaml.safe_dump(queue_data, sort_keys=False), encoding="utf-8")
        result = queue.validate_queue(self.db_path, queue_path)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["items"][0]["task_type"], "generated_baseline_review")


class ResearchPhase11CLISmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cli_help_includes_generated_baseline(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            try:
                cli.main(["--db", str(self.db_path), "--help"])
            except SystemExit:
                pass
        output = stdout.getvalue()
        self.assertIn("generated-baseline", output)

    def test_cli_generated_baseline_run_help(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            try:
                cli.main(["--db", str(self.db_path), "generated-baseline", "run", "--help"])
            except SystemExit:
                pass
        output = stdout.getvalue()
        self.assertIn("implementation-request-id", output)
        self.assertIn("strategy-id", output)
        self.assertIn("dataset-id", output)

    def test_cli_generated_baseline_review_help(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            try:
                cli.main(["--db", str(self.db_path), "generated-baseline", "review", "--help"])
            except SystemExit:
                pass
        output = stdout.getvalue()
        self.assertIn("experiment-id", output)
        self.assertIn("strategy-id", output)


class ResearchPhase11ConstantsTests(unittest.TestCase):
    def test_approval_scopes_contains_baseline_only(self) -> None:
        self.assertIn("baseline_only", APPROVAL_SCOPES)
        self.assertIn("final_holdout_only", APPROVAL_SCOPES)
        self.assertEqual(len(APPROVAL_SCOPES), 2)

    def test_approval_usage_statuses_defined(self) -> None:
        self.assertIn("pending", APPROVAL_USAGE_STATUSES)
        self.assertIn("completed", APPROVAL_USAGE_STATUSES)
        self.assertIn("failed", APPROVAL_USAGE_STATUSES)
        self.assertEqual(len(APPROVAL_USAGE_STATUSES), 3)

    def test_generated_baseline_review_artifact_type(self) -> None:
        from automated.research.contracts import ARTIFACT_TYPES
        self.assertIn("generated_baseline_review", ARTIFACT_TYPES)

    def test_allowed_recommendations(self) -> None:
        self.assertIn("reject", generated_baseline.ALLOWED_RECOMMENDATIONS)
        self.assertIn("revise_strategy_spec", generated_baseline.ALLOWED_RECOMMENDATIONS)
        self.assertIn("revise_implementation", generated_baseline.ALLOWED_RECOMMENDATIONS)
        self.assertIn("run_robustness_sweep_next", generated_baseline.ALLOWED_RECOMMENDATIONS)
        self.assertIn("defer", generated_baseline.ALLOWED_RECOMMENDATIONS)
        self.assertNotIn("promote_to_production", generated_baseline.ALLOWED_RECOMMENDATIONS)
        self.assertEqual(len(generated_baseline.ALLOWED_RECOMMENDATIONS), 5)

    def test_schema_version_is_6(self) -> None:
        self.assertEqual(registry.SCHEMA_VERSION, 6)


if __name__ == "__main__":
    unittest.main()
