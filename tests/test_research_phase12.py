from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import yaml

from automated.research import (
    cli,
    datasets,
    generated_robustness,
    implementation as impl_mod,
    queue,
    registry,
    sweeps,
)
from automated.research.schemas import (
    GENERATED_SPECS_DIR,
    REPO_ROOT,
    SANDBOX_ROOT,
    SchemaValidationError,
)
from automated.research.contracts import ARTIFACT_TYPES


def _write_sample_mq5(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "//+------------------------------------------------------------------+\n"
        "//| TestStrategy.mq5                                                |\n"
        "//+------------------------------------------------------------------+\n"
        "#property version   \"1.00\"\n"
        "input double InpRiskPerTrade = 0.01;\n"
        "input int    InpAtrPeriod = 14;\n"
        "input double InpStopLossAtr = 2.0;\n"
        "input double InpTakeProfitAtr = 4.0;\n"
        "input double InpMinBreakDistanceAtr = 0.05;\n"
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


def _write_sample_generated_spec(
    strategy_id: str,
    mq5_path: Path,
    strategies_ref: str | None = None,
) -> Path:
    spec_path = GENERATED_SPECS_DIR / f"{strategy_id}.yaml"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    impl_files = {
        "expert_advisor": str(mq5_path),
        "config": str(REPO_ROOT / "automated" / "runs" / "example.conf"),
        "parameters": str(REPO_ROOT / "automated" / "runs" / "sets" / "example.set"),
    }
    if strategies_ref:
        impl_files["expert_advisor"] = str(REPO_ROOT / "automated" / strategies_ref)
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
        "parameters": {
            "InpRiskPerTrade": 0.01,
            "InpAtrPeriod": 14,
            "InpStopLossAtr": 2.0,
            "InpTakeProfitAtr": 4.0,
            "InpMinBreakDistanceAtr": 0.05,
        },
        "filters": [],
        "regime_filters": [],
        "implementation": {
            "engine": "mt5",
            "generation_mode": "generated",
            "files": impl_files,
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
            {"name": "InpStopLossAtr", "type": "double", "required": True, "default": "2.0"},
            {"name": "InpTakeProfitAtr", "type": "double", "required": True, "default": "4.0"},
            {"name": "InpMinBreakDistanceAtr", "type": "double", "required": True, "default": "0.05"},
            {"name": "InpUseSessionFilter", "type": "bool", "required": False, "default": "true"},
            {"name": "InpMagicNumber", "type": "int", "required": False, "default": "12345"},
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


def _full_baseline(
    db_path: str | Path,
    strategy_id: str,
    sandbox: Path,
    mq5_path: Path,
) -> tuple[str, dict]:
    approval = _full_approval(db_path, strategy_id, "v1", sandbox)
    impl_id = approval["implementation_id"]
    impl_request_id = approval["implementation_request_id"]

    spec_path = _write_sample_generated_spec(strategy_id, mq5_path)

    experiment_id = f"EXP_BASELINE_{strategy_id}_TEST"
    registry.init_db(db_path)
    bars_file = Path(db_path).parent / "test_bars.csv"
    bars_file.write_text("time\topen\thigh\tlow\tclose\ttick_volume\n2024-01-01\t100\t110\t90\t105\t1000\n", encoding="utf-8")
    ds = datasets.register_dataset(
        db_path,
        bars_path=bars_file,
        symbol="XAUUSD",
        timeframe="H4",
    )
    ds_id = ds["dataset_id"]
    payload = {
        "experiment_id": experiment_id,
        "hypothesis_id": "HYP_GEN_FBR_RANGING_000",
        "strategy_id": strategy_id,
        "strategy_version": "v1",
        "run_reason": "test",
        "created_by": "test",
        "created_at": registry.utc_now(),
        "spec_hash": "fake_hash",
        "parameter_set_hash": "fake_hash",
        "dataset_id": ds_id,
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
        "headline_metrics_json": json.dumps({
            "net_return": 500.0,
            "profit_factor": 1.8,
            "trade_count": 25,
        }),
    }
    registry.create_experiment(db_path, payload)

    usage_id = f"USAGE_BASELINE_{strategy_id}"
    usage_record = {
        "usage_id": usage_id,
        "implementation_id": impl_id,
        "implementation_request_id": impl_request_id,
        "experiment_id": experiment_id,
        "queue_run_id": None,
        "used_at": registry.utc_now(),
        "runner_mode": "test",
        "status": "completed",
    }
    registry.create_approval_usage(db_path, usage_record)

    review_dir = (
        REPO_ROOT / "automated" / "research_runs" / experiment_id / "reports"
    )
    review_dir.mkdir(parents=True, exist_ok=True)
    review_path = review_dir / "generated_baseline_review.yaml"
    review_packet = {
        "schema_version": "generated_baseline_review_v1",
        "strategy_id": strategy_id,
        "strategy_version": "v1",
        "experiment_id": experiment_id,
        "recommendation": "run_robustness_sweep_next",
        "approval_status": "approved_for_baseline",
        "runner_mode": "test",
    }
    review_path.write_text(yaml.safe_dump(review_packet, sort_keys=False), encoding="utf-8")
    registry.attach_artifact(db_path, experiment_id, "generated_baseline_review", review_path)

    return experiment_id, approval


class ResearchPhase12EligibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.sandbox = SANDBOX_ROOT / "gr_elig_test" / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / "gr_elig_test.mq5"
        _write_sample_mq5(self.mq5_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        spec_p = GENERATED_SPECS_DIR / "gr_elig_test.yaml"
        if spec_p.is_file():
            spec_p.unlink()
        import shutil
        if self.sandbox.is_dir():
            shutil.rmtree(self.sandbox)
        rev_dir = REPO_ROOT / "automated" / "research_runs" / "EXP_BASELINE_gr_elig_test_TEST" / "reports"
        if rev_dir.is_dir():
            shutil.rmtree(rev_dir)

    def test_blocked_when_no_implementation_request(self) -> None:
        result = generated_robustness.require_generated_robustness_eligibility(
            self.db_path, "nonexistent", "v1",
        )
        self.assertFalse(result["eligible"])
        self.assertTrue(any("No implementation request" in e for e in result.get("errors", [])))

    def test_blocked_when_not_approved_for_baseline(self) -> None:
        req = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id="gr_elig_test",
            strategy_version="v1",
            sandbox_dir=self.sandbox,
            generated_files=["gr_elig_test.mq5"],
            created_by="test",
        )
        impl_mod.compile_check(self.db_path, req["implementation_request_id"], mock=True)
        impl_mod.run_diff_review(self.db_path, req["implementation_request_id"])
        result = generated_robustness.require_generated_robustness_eligibility(
            self.db_path, "gr_elig_test", "v1",
        )
        self.assertFalse(result["eligible"])
        self.assertTrue(any("Not approved for baseline" in e for e in result["errors"]))

    def test_blocked_when_no_completed_approval_usage(self) -> None:
        approval = _full_approval(self.db_path, "gr_elig_test", "v1", self.sandbox)
        result = generated_robustness.require_generated_robustness_eligibility(
            self.db_path, "gr_elig_test", "v1",
        )
        self.assertFalse(result["eligible"])
        self.assertTrue(
            any("No completed approval usage" in e for e in result.get("errors", []))
        )

    def test_blocked_when_baseline_review_missing(self) -> None:
        approval = _full_approval(self.db_path, "gr_elig_test", "v1", self.sandbox)
        impl_id = approval["implementation_id"]
        impl_req_id = approval["implementation_request_id"]
        experiment_id = "EXP_MISSING_REVIEW"
        usage_id = "USAGE_MISSING_REVIEW"
        bars_file = Path(self.temp_dir.name) / "test_bars.csv"
        bars_file.write_text("time\topen\thigh\tlow\tclose\ttick_volume\n2024-01-01\t100\t110\t90\t105\t1000\n", encoding="utf-8")
        ds = datasets.register_dataset(
            self.db_path,
            bars_path=bars_file,
            symbol="XAUUSD",
            timeframe="H4",
        )
        payload = {
            "experiment_id": experiment_id,
            "hypothesis_id": "HYP_GEN_FBR_RANGING_000",
            "strategy_id": "gr_elig_test",
            "strategy_version": "v1",
            "run_reason": "test",
            "created_by": "test",
            "created_at": registry.utc_now(),
            "spec_hash": "fake_hash",
            "parameter_set_hash": "fake_hash",
            "dataset_id": ds["dataset_id"],
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
        registry.create_approval_usage(
            self.db_path,
            {
                "usage_id": usage_id,
                "implementation_id": impl_id,
                "implementation_request_id": impl_req_id,
                "experiment_id": experiment_id,
                "queue_run_id": None,
                "used_at": registry.utc_now(),
                "runner_mode": "test",
                "status": "completed",
            },
        )
        result = generated_robustness.require_generated_robustness_eligibility(
            self.db_path, "gr_elig_test", "v1",
        )
        self.assertFalse(result["eligible"])
        self.assertTrue(
            any("generated_baseline_review artifact not found" in e for e in result.get("errors", []))
        )

    def test_allowed_when_all_conditions_met(self) -> None:
        _write_sample_generated_spec("gr_elig_test", self.mq5_path)
        _full_baseline(self.db_path, "gr_elig_test", self.sandbox, self.mq5_path)
        result = generated_robustness.require_generated_robustness_eligibility(
            self.db_path, "gr_elig_test", "v1",
            allow_mock_compile=True,
        )
        if not result.get("eligible"):
            self.fail(f"Not eligible: {result.get('errors', [])}")
        self.assertTrue(result["eligible"])


class ResearchPhase12SandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.sandbox = SANDBOX_ROOT / "gr_sandbox_test" / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / "gr_sandbox_test.mq5"
        _write_sample_mq5(self.mq5_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        spec_p = GENERATED_SPECS_DIR / "gr_sandbox_test.yaml"
        if spec_p.is_file():
            spec_p.unlink()
        import shutil
        if self.sandbox.is_dir():
            shutil.rmtree(self.sandbox)

    def test_unrelated_production_strategies_do_not_block(self) -> None:
        prod_dir = REPO_ROOT / "automated" / "strategies"
        prod_dir.mkdir(parents=True, exist_ok=True)
        prod_file = prod_dir / "unrelated_prod_strategy.mq5"
        if not prod_file.is_file():
            prod_file.write_text("// existing unrelated\n", encoding="utf-8")
        try:
            _write_sample_generated_spec("gr_sandbox_test", self.mq5_path)
            _full_baseline(self.db_path, "gr_sandbox_test", self.sandbox, self.mq5_path)
            result = generated_robustness.require_generated_robustness_eligibility(
                self.db_path, "gr_sandbox_test", "v1",
                allow_mock_compile=True,
            )
            self.assertTrue(result["eligible"])
        finally:
            if prod_file.is_file():
                prod_file.unlink()

    def test_blocked_when_mq5_outside_sandbox(self) -> None:
        approval = _full_approval(self.db_path, "gr_sandbox_test", "v1", self.sandbox)
        impls = registry.list_implementations(self.db_path, approval["implementation_request_id"])
        impl = impls[-1]
        outside_path = Path(self.temp_dir.name) / "outside.mq5"
        outside_path.write_text("// test\n", encoding="utf-8")
        registry.update_implementation(self.db_path, impl["implementation_id"], generated_mq5_path=str(outside_path))
        _write_sample_generated_spec("gr_sandbox_test", outside_path)
        bars_file = Path(self.temp_dir.name) / "test_bars.csv"
        bars_file.write_text("time\topen\thigh\tlow\tclose\ttick_volume\n2024-01-01\t100\t110\t90\t105\t1000\n", encoding="utf-8")
        ds = datasets.register_dataset(
            self.db_path,
            bars_path=bars_file,
            symbol="XAUUSD",
            timeframe="H4",
        )
        experiment_id = "EXP_SANDBOX_OUTSIDE"
        usage_id = "USAGE_SANDBOX_OUTSIDE"
        payload = {
            "experiment_id": experiment_id,
            "hypothesis_id": "HYP_GEN_FBR_RANGING_000",
            "strategy_id": "gr_sandbox_test",
            "strategy_version": "v1",
            "run_reason": "test",
            "created_by": "test",
            "created_at": registry.utc_now(),
            "spec_hash": "fake",
            "parameter_set_hash": "fake",
            "dataset_id": ds["dataset_id"],
            "dataset_bundle_id": None,
            "dataset_hash": "fake",
            "dataset_bundle_hash": None,
            "code_version": "test",
            "execution_config_hash": "fake",
            "cost_config_hash": "fake",
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
        registry.create_approval_usage(
            self.db_path,
            {
                "usage_id": usage_id,
                "implementation_id": impl["implementation_id"],
                "implementation_request_id": approval["implementation_request_id"],
                "experiment_id": experiment_id,
                "queue_run_id": None,
                "used_at": registry.utc_now(),
                "runner_mode": "test",
                "status": "completed",
            },
        )
        review_dir = REPO_ROOT / "automated" / "research_runs" / experiment_id / "reports"
        review_dir.mkdir(parents=True, exist_ok=True)
        (review_dir / "generated_baseline_review.yaml").write_text(
            yaml.safe_dump({"schema_version": "generated_baseline_review_v1"}, sort_keys=False),
            encoding="utf-8",
        )
        result = generated_robustness.require_generated_robustness_eligibility(
            self.db_path, "gr_sandbox_test", "v1",
            allow_mock_compile=True,
        )
        self.assertFalse(result["eligible"])
        self.assertTrue(
            any("outside sandbox" in e for e in result.get("errors", []))
        )

    def test_blocked_when_spec_points_to_automated_strategies(self) -> None:
        _full_baseline(self.db_path, "gr_sandbox_test", self.sandbox, self.mq5_path)
        _write_sample_generated_spec(
            "gr_sandbox_test", self.mq5_path,
            strategies_ref="automated/strategies/prod_strategy.mq5",
        )
        result = generated_robustness.require_generated_robustness_eligibility(
            self.db_path, "gr_sandbox_test", "v1",
            allow_mock_compile=True,
        )
        self.assertFalse(result["eligible"])
        self.assertTrue(
            any("automated/strategies/" in e for e in result.get("errors", []))
        )


class ResearchPhase12ParameterValidationTests(unittest.TestCase):
    def test_allowed_parameters_pass(self) -> None:
        params = {
            "InpAtrPeriod": [10, 14, 20],
            "InpRiskPerTrade": [0.005, 0.01, 0.02],
        }
        warnings = generated_robustness.validate_sweep_parameters(params)
        self.assertEqual(warnings, [])

    def test_blocked_parameters_reported(self) -> None:
        params = {
            "InpAtrPeriod": [10, 20],
            "InpBadParam": [1, 2],
        }
        warnings = generated_robustness.validate_sweep_parameters(params)
        self.assertTrue(any("InpBadParam" in w for w in warnings))
        self.assertFalse(any("InpAtrPeriod" in w for w in warnings))

    def test_blocked_mutations_detected(self) -> None:
        config = {"symbol": "XAUUSD"}
        blocked = generated_robustness.has_blocked_mutations(config)
        self.assertTrue(any("symbol" in b for b in blocked))

    def test_blocked_timeframe_mutation(self) -> None:
        blocked = generated_robustness.has_blocked_mutations({"timeframe": "H1"})
        self.assertTrue(any("timeframe" in b for b in blocked))

    def test_blocked_dataset_mutation(self) -> None:
        blocked = generated_robustness.has_blocked_mutations({"dataset_id": "DS_OTHER"})
        self.assertTrue(any("dataset" in b for b in blocked))

    def test_blocked_cost_mutation(self) -> None:
        blocked = generated_robustness.has_blocked_mutations({"cost_multipliers": [1.0, 2.0]})
        self.assertTrue(any("cost" in b for b in blocked))

    def test_blocked_validation_mutation(self) -> None:
        blocked = generated_robustness.has_blocked_mutations(
            {"allow_validation_threshold_changes": True}
        )
        self.assertTrue(any("validation" in b for b in blocked))


class ResearchPhase12RedTeamTests(unittest.TestCase):
    def test_warning_for_isolated_good_child(self) -> None:
        children_metrics = {
            "c1": {"net_return": -100.0, "profit_factor": 0.5, "trade_count": 20},
            "c2": {"net_return": -50.0, "profit_factor": 0.8, "trade_count": 15},
            "c3": {"net_return": 300.0, "profit_factor": 2.0, "trade_count": 25},
            "c4": {"net_return": -20.0, "profit_factor": 0.9, "trade_count": 18},
        }
        result = generated_robustness.red_team_robustness_check(
            sweep_summary={},
            children_metrics=children_metrics,
            child_count=4,
            children_failed=0,
        )
        self.assertIn("isolated_good_child", result.get("risk_flags", []))

    def test_warning_for_high_dispersion(self) -> None:
        children_metrics = {
            "c1": {"net_return": 500.0, "profit_factor": 3.0, "trade_count": 30},
            "c2": {"net_return": -400.0, "profit_factor": 0.3, "trade_count": 20},
            "c3": {"net_return": 200.0, "profit_factor": 2.5, "trade_count": 25},
        }
        result = generated_robustness.red_team_robustness_check(
            sweep_summary={},
            children_metrics=children_metrics,
            child_count=3,
            children_failed=0,
        )
        self.assertIn("high_dispersion", result.get("risk_flags", []))

    def test_warning_for_most_children_fail(self) -> None:
        children_metrics = {
            "c1": {"net_return": -50.0, "profit_factor": 0.6, "trade_count": 10},
            "c2": {"net_return": -30.0, "profit_factor": 0.7, "trade_count": 12},
            "c3": {"net_return": 10.0, "profit_factor": 1.1, "trade_count": 8},
            "c4": {"net_return": -100.0, "profit_factor": 0.4, "trade_count": 15},
        }
        result = generated_robustness.red_team_robustness_check(
            sweep_summary={},
            children_metrics=children_metrics,
            child_count=4,
            children_failed=0,
        )
        self.assertIn("most_children_fail", result.get("risk_flags", []))

    def test_warning_for_excessive_failures(self) -> None:
        children_metrics = {
            "c1": {"net_return": 100.0, "profit_factor": 1.5, "trade_count": 20},
        }
        result = generated_robustness.red_team_robustness_check(
            sweep_summary={},
            children_metrics=children_metrics,
            child_count=5,
            children_failed=3,
        )
        self.assertIn("excessive_failures", result.get("risk_flags", []))

    def test_warning_for_sweep_too_small(self) -> None:
        result = generated_robustness.red_team_robustness_check(
            sweep_summary={},
            children_metrics={},
            child_count=2,
            children_failed=0,
        )
        self.assertIn("sweep_too_small", result.get("risk_flags", []))


class ResearchPhase12ReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        bars_file = Path(self.temp_dir.name) / "test_bars.csv"
        bars_file.write_text("time\topen\thigh\tlow\tclose\ttick_volume\n2024-01-01\t100\t110\t90\t105\t1000\n", encoding="utf-8")
        ds = datasets.register_dataset(
            self.db_path,
            bars_path=bars_file,
            symbol="XAUUSD",
            timeframe="H4",
        )
        self.ds_id = ds["dataset_id"]
        self.sandbox = SANDBOX_ROOT / "gr_review_test" / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / "gr_review_test.mq5"
        _write_sample_mq5(self.mq5_path)
        _write_sample_generated_spec("gr_review_test", self.mq5_path)
        self.baseline_exp_id, self.approval = _full_baseline(
            self.db_path, "gr_review_test", self.sandbox, self.mq5_path,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        spec_p = GENERATED_SPECS_DIR / "gr_review_test.yaml"
        if spec_p.is_file():
            spec_p.unlink()
        import shutil
        if self.sandbox.is_dir():
            shutil.rmtree(self.sandbox)

    def _create_sweep_with_children(self) -> str:
        sweep_id = "SWEEP_TEST_REVIEW"
        registry.create_sweep(
            self.db_path,
            {
                "sweep_id": sweep_id,
                "parent_experiment_id": self.baseline_exp_id,
                "strategy_id": "gr_review_test",
                "hypothesis_id": "HYP_GEN_FBR_RANGING_000",
                "sweep_type": "parameter_robustness",
                "status": "completed",
                "created_by": "test",
                "created_at": registry.utc_now(),
                "completed_at": registry.utc_now(),
                "budget": {"max_child_experiments": 5, "max_parameters_changed_per_child": 1},
                "config": {},
                "summary_path": None,
                "notes": "",
            },
        )
        for i, (pf, nr, tc) in enumerate([
            (2.0, 300.0, 30),
            (1.5, 100.0, 25),
            (0.8, -50.0, 20),
            (1.2, 50.0, 15),
            (1.8, 200.0, 28),
        ]):
            child_id = f"CHILD_REV_{i:03d}"
            child_status = "completed" if pf >= 1.0 else "failed"
            exp_payload = {
                "experiment_id": child_id,
                "hypothesis_id": "HYP_GEN_FBR_RANGING_000",
                "strategy_id": "gr_review_test",
                "strategy_version": "v1",
                "run_reason": "test",
                "created_by": "test",
                "created_at": registry.utc_now(),
                "spec_hash": "fake",
                "parameter_set_hash": "fake",
                "dataset_id": self.ds_id,
                "dataset_bundle_id": None,
                "dataset_hash": "fake",
                "dataset_bundle_hash": None,
                "code_version": "test",
                "execution_config_hash": "fake",
                "cost_config_hash": "fake",
                "engine": "mt5",
                "implementation_files": {},
                "implementation_mode": "generated",
                "execution_timing": {},
                "timeframe": "H4",
                "universe": ["XAUUSD"],
                "parent_experiment_id": self.baseline_exp_id,
                "rerun_of_experiment_id": None,
                "is_artifact_regeneration": False,
                "change_type": "parameter_diff",
                "change_summary": "test sweep child",
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
                "headline_metrics_json": json.dumps({
                    "net_return": nr,
                    "profit_factor": pf,
                    "trade_count": tc,
                }),
            }
            registry.create_experiment(self.db_path, exp_payload)
            registry.upsert_experiment_metrics(self.db_path, child_id, {
                "period_type": "full",
                "net_return": nr,
                "profit_factor": pf,
                "cagr": None,
                "sharpe": None,
                "sortino": None,
                "max_drawdown": None,
                "calmar": None,
                "win_rate": None,
                "avg_trade": None,
                "median_trade": None,
                "exposure_time": None,
                "turnover": None,
                "trade_count": tc,
                "best_trade_pct_of_total": None,
                "cost_sensitivity_score": None,
                "parameter_stability_score": None,
                "correlation_to_portfolio": None,
                "notes": None,
            })
            registry.add_sweep_child(
                self.db_path,
                {
                    "sweep_id": sweep_id,
                    "child_experiment_id": child_id,
                    "child_index": i,
                    "child_role": f"param={i}",
                    "parameter_diff": {"InpAtrPeriod": {"from": 14, "to": 14 + i}},
                    "status": child_status,
                },
            )
        return sweep_id

    def test_review_artifact_created(self) -> None:
        sweep_id = self._create_sweep_with_children()
        output_dir = Path(self.temp_dir.name) / "review_output"
        result = generated_robustness.build_generated_robustness_review_packet(
            self.db_path,
            sweep_id=sweep_id,
            baseline_experiment_id=self.baseline_exp_id,
            strategy_id="gr_review_test",
            strategy_version="v1",
            implementation_request_id=self.approval["implementation_request_id"],
            implementation_id=self.approval["implementation_id"],
            output_dir=output_dir,
        )
        self.assertIn("packet_path", result)
        packet_path = Path(result["packet_path"])
        self.assertTrue(packet_path.is_file())
        packet = yaml.safe_load(packet_path.read_text(encoding="utf-8"))
        self.assertEqual(packet["schema_version"], "generated_robustness_review_v1")
        self.assertIn("children_completed", packet)
        self.assertIn("recommendation", packet)
        self.assertIn("red_team_assessment", packet)

    def test_review_recommendation_never_promote_to_production(self) -> None:
        sweep_id = self._create_sweep_with_children()
        output_dir = Path(self.temp_dir.name) / "review_output2"
        result = generated_robustness.build_generated_robustness_review_packet(
            self.db_path,
            sweep_id=sweep_id,
            baseline_experiment_id=self.baseline_exp_id,
            strategy_id="gr_review_test",
            strategy_version="v1",
            implementation_request_id=self.approval["implementation_request_id"],
            implementation_id=self.approval["implementation_id"],
            output_dir=output_dir,
        )
        packet = yaml.safe_load(Path(result["packet_path"]).read_text(encoding="utf-8"))
        self.assertNotEqual(packet["recommendation"], "promote_to_production")
        self.assertIn(packet["recommendation"], generated_robustness.ALLOWED_ROBUSTNESS_RECOMMENDATIONS)

    def test_review_has_required_fields(self) -> None:
        sweep_id = self._create_sweep_with_children()
        output_dir = Path(self.temp_dir.name) / "review_output3"
        result = generated_robustness.build_generated_robustness_review_packet(
            self.db_path,
            sweep_id=sweep_id,
            baseline_experiment_id=self.baseline_exp_id,
            strategy_id="gr_review_test",
            strategy_version="v1",
            implementation_request_id=self.approval["implementation_request_id"],
            implementation_id=self.approval["implementation_id"],
            output_dir=output_dir,
        )
        packet = yaml.safe_load(Path(result["packet_path"]).read_text(encoding="utf-8"))
        for field in [
            "schema_version", "strategy_id", "strategy_version",
            "implementation_request_id", "implementation_id",
            "baseline_experiment_id", "child_experiment_ids",
            "children_completed", "children_failed",
            "best_profit_factor", "worst_profit_factor", "median_profit_factor",
            "best_net_return", "worst_net_return", "median_net_return",
            "recommendation", "red_team_assessment",
        ]:
            self.assertIn(field, packet, f"Missing field: {field}")


class ResearchPhase12QueueIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_generated_robustness_task_types_in_queue(self) -> None:
        self.assertIn("generated_robustness_sweep", queue.TASK_TYPES)
        self.assertIn("generated_robustness_review", queue.TASK_TYPES)

    def test_blocked_if_runner_execution_false(self) -> None:
        queue_path = Path(self.temp_dir.name) / "gr_block_re.yaml"
        queue_data = {
            "queue_id": "gr_block_re",
            "items": [{
                "queue_id": "gr_re_001",
                "priority": 10,
                "task_type": "generated_robustness_sweep",
                "strategy_id": "gr_queue_test",
                "strategy_version": "v1",
                "hypothesis_id": "HYP_TEST",
                "implementation_request_id": "IMPL_TEST",
                "baseline_experiment_id": "EXP_TEST",
                "sweep_config": {
                    "parameters": {"InpAtrPeriod": [10, 14]},
                },
                "requested_by": "test",
                "created_at": "2026-05-12T00:00:00+00:00",
                "allowed_agent_roles": ["backtest_runner"],
                "budget": {"max_child_experiments": 5, "max_sweeps": 1},
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
            }],
        }
        queue_path.write_text(yaml.safe_dump(queue_data, sort_keys=False), encoding="utf-8")
        with self.assertRaises(SchemaValidationError):
            queue.validate_queue(self.db_path, queue_path)

    def test_blocked_if_lifecycle_apply_true(self) -> None:
        queue_path = Path(self.temp_dir.name) / "gr_block_la.yaml"
        queue_data = {
            "queue_id": "gr_block_la",
            "items": [{
                "queue_id": "gr_la_001",
                "priority": 10,
                "task_type": "generated_robustness_sweep",
                "strategy_id": "gr_queue_test",
                "strategy_version": "v1",
                "hypothesis_id": "HYP_TEST",
                "implementation_request_id": "IMPL_TEST",
                "baseline_experiment_id": "EXP_TEST",
                "sweep_config": {
                    "parameters": {"InpAtrPeriod": [10, 14]},
                },
                "requested_by": "test",
                "created_at": "2026-05-12T00:00:00+00:00",
                "allowed_agent_roles": ["backtest_runner"],
                "budget": {"max_child_experiments": 5, "max_sweeps": 1},
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
            }],
        }
        queue_path.write_text(yaml.safe_dump(queue_data, sort_keys=False), encoding="utf-8")
        with self.assertRaises(SchemaValidationError):
            queue.validate_queue(self.db_path, queue_path)

    def test_blocked_if_final_holdout_true(self) -> None:
        queue_path = Path(self.temp_dir.name) / "gr_block_fh.yaml"
        queue_data = {
            "queue_id": "gr_block_fh",
            "items": [{
                "queue_id": "gr_fh_001",
                "priority": 10,
                "task_type": "generated_robustness_sweep",
                "strategy_id": "gr_queue_test",
                "strategy_version": "v1",
                "hypothesis_id": "HYP_TEST",
                "implementation_request_id": "IMPL_TEST",
                "baseline_experiment_id": "EXP_TEST",
                "sweep_config": {
                    "parameters": {"InpAtrPeriod": [10, 14]},
                },
                "requested_by": "test",
                "created_at": "2026-05-12T00:00:00+00:00",
                "allowed_agent_roles": ["backtest_runner"],
                "budget": {"max_child_experiments": 5, "max_sweeps": 1},
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
            }],
        }
        queue_path.write_text(yaml.safe_dump(queue_data, sort_keys=False), encoding="utf-8")
        with self.assertRaises(SchemaValidationError):
            queue.validate_queue(self.db_path, queue_path)

    def test_blocked_if_child_experiment_cap_exceeded(self) -> None:
        queue_path = Path(self.temp_dir.name) / "gr_block_cap.yaml"
        queue_data = {
            "queue_id": "gr_block_cap",
            "items": [{
                "queue_id": "gr_cap_001",
                "priority": 10,
                "task_type": "generated_robustness_sweep",
                "strategy_id": "gr_queue_test",
                "strategy_version": "v1",
                "hypothesis_id": "HYP_TEST",
                "implementation_request_id": "IMPL_TEST",
                "baseline_experiment_id": "EXP_TEST",
                "sweep_config": {
                    "parameters": {"InpAtrPeriod": [10, 14]},
                },
                "requested_by": "test",
                "created_at": "2026-05-12T00:00:00+00:00",
                "allowed_agent_roles": ["backtest_runner"],
                "budget": {"max_child_experiments": 13, "max_sweeps": 1},
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
            }],
        }
        queue_path.write_text(yaml.safe_dump(queue_data, sort_keys=False), encoding="utf-8")
        with self.assertRaises(SchemaValidationError):
            queue.validate_queue(self.db_path, queue_path)

    def test_blocked_if_child_experiment_cap_zero(self) -> None:
        queue_path = Path(self.temp_dir.name) / "gr_block_zero.yaml"
        queue_data = {
            "queue_id": "gr_block_zero",
            "items": [{
                "queue_id": "gr_zero_001",
                "priority": 10,
                "task_type": "generated_robustness_sweep",
                "strategy_id": "gr_queue_test",
                "strategy_version": "v1",
                "hypothesis_id": "HYP_TEST",
                "implementation_request_id": "IMPL_TEST",
                "baseline_experiment_id": "EXP_TEST",
                "sweep_config": {
                    "parameters": {"InpAtrPeriod": [10, 14]},
                },
                "requested_by": "test",
                "created_at": "2026-05-12T00:00:00+00:00",
                "allowed_agent_roles": ["backtest_runner"],
                "budget": {"max_child_experiments": 0, "max_sweeps": 1},
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
            }],
        }
        queue_path.write_text(yaml.safe_dump(queue_data, sort_keys=False), encoding="utf-8")
        with self.assertRaises(SchemaValidationError):
            queue.validate_queue(self.db_path, queue_path)

    def test_blocked_if_sweep_attempts_blocked_parameter(self) -> None:
        queue_path = Path(self.temp_dir.name) / "gr_block_param.yaml"
        queue_data = {
            "queue_id": "gr_block_param",
            "items": [{
                "queue_id": "gr_param_001",
                "priority": 10,
                "task_type": "generated_robustness_sweep",
                "strategy_id": "gr_queue_test",
                "strategy_version": "v1",
                "hypothesis_id": "HYP_TEST",
                "implementation_request_id": "IMPL_TEST",
                "baseline_experiment_id": "EXP_TEST",
                "sweep_config": {
                    "parameters": {"InpAtrPeriod": [10, 14], "InpBadParam": [1]},
                },
                "requested_by": "test",
                "created_at": "2026-05-12T00:00:00+00:00",
                "allowed_agent_roles": ["backtest_runner"],
                "budget": {"max_child_experiments": 5, "max_sweeps": 1},
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
            }],
        }
        queue_path.write_text(yaml.safe_dump(queue_data, sort_keys=False), encoding="utf-8")
        with self.assertRaises(SchemaValidationError):
            queue.validate_queue(self.db_path, queue_path)

    def test_generated_robustness_review_task_validates(self) -> None:
        queue_path = Path(self.temp_dir.name) / "gr_review_queue.yaml"
        queue_data = {
            "queue_id": "gr_review_queue",
            "items": [{
                "queue_id": "gr_review_001",
                "priority": 20,
                "task_type": "generated_robustness_review",
                "strategy_id": "gr_queue_test",
                "strategy_version": "v1",
                "parent_experiment_id": "SWEEP_test",
                "baseline_experiment_id": "EXP_test",
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
            }],
        }
        queue_path.write_text(yaml.safe_dump(queue_data, sort_keys=False), encoding="utf-8")
        result = queue.validate_queue(self.db_path, queue_path)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["items"][0]["task_type"], "generated_robustness_review")


class ResearchPhase12CLISmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cli_help_includes_generated_robustness(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            try:
                cli.main(["--db", str(self.db_path), "--help"])
            except SystemExit:
                pass
        output = stdout.getvalue()
        self.assertIn("generated-robustness", output)

    def test_cli_run_sweep_help(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            try:
                cli.main(["--db", str(self.db_path), "generated-robustness", "run-sweep", "--help"])
            except SystemExit:
                pass
        output = stdout.getvalue()
        self.assertIn("strategy-id", output)
        self.assertIn("implementation-request-id", output)
        self.assertIn("baseline-experiment-id", output)
        self.assertIn("params", output)
        self.assertIn("child-cap", output)

    def test_cli_review_help(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            try:
                cli.main(["--db", str(self.db_path), "generated-robustness", "review", "--help"])
            except SystemExit:
                pass
        output = stdout.getvalue()
        self.assertIn("sweep-id", output)
        self.assertIn("baseline-experiment-id", output)
        self.assertIn("strategy-id", output)


class ResearchPhase12ConstantsTests(unittest.TestCase):
    def test_generated_robustness_review_artifact_type_registered(self) -> None:
        self.assertIn("generated_robustness_review", ARTIFACT_TYPES)

    def test_allowed_recommendations(self) -> None:
        allowed = generated_robustness.ALLOWED_ROBUSTNESS_RECOMMENDATIONS
        self.assertIn("reject", allowed)
        self.assertIn("revise_strategy_spec", allowed)
        self.assertIn("revise_implementation", allowed)
        self.assertIn("run_additional_bounded_sweep", allowed)
        self.assertIn("consider_lifecycle_candidate", allowed)
        self.assertIn("defer", allowed)
        self.assertNotIn("promote_to_production", allowed)
        self.assertEqual(len(allowed), 6)

    def test_allowed_sweep_parameters(self) -> None:
        allowed = generated_robustness.ALLOWED_SWEEP_PARAMETERS
        self.assertIn("InpAtrPeriod", allowed)
        self.assertIn("InpStopLossAtr", allowed)
        self.assertIn("InpTakeProfitAtr", allowed)
        self.assertIn("InpRiskPerTrade", allowed)
        self.assertIn("InpMinBreakDistanceAtr", allowed)
        self.assertEqual(len(allowed), 5)

    def test_schema_version_defined(self) -> None:
        self.assertEqual(
            generated_robustness.GENERATED_ROBUSTNESS_REVIEW_SCHEMA,
            "generated_robustness_review_v1",
        )


if __name__ == "__main__":
    unittest.main()
