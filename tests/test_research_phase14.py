from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml

from automated.research import (
    cli,
    generated_candidate,
    generated_final_holdout,
    implementation as impl_mod,
    queue,
    registry,
)
from automated.research.schemas import (
    GENERATED_SPECS_DIR,
    REPO_ROOT,
    SANDBOX_ROOT,
)
from automated.research.contracts import (
    APPROVAL_SCOPES,
    ARTIFACT_TYPES,
)
from tests.research_test_helpers import cleanup_path


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


def _write_sample_generated_spec(strategy_id: str, mq5_path: Path) -> Path:
    spec_path = GENERATED_SPECS_DIR / f"{strategy_id}.yaml"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
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


def _full_approval(db_path, strategy_id):
    sandbox = SANDBOX_ROOT / strategy_id / "v1"
    _write_sample_mq5(sandbox / f"{strategy_id}.mq5")
    req = impl_mod.create_implementation_request(
        db_path,
        strategy_id=strategy_id,
        strategy_version="v1",
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
    return approve_result, sandbox


def _create_baseline_with_review(db_path, strategy_id, impl_request_id, impl_id, sandbox, mq5_path):
    _write_sample_generated_spec(strategy_id, mq5_path)
    experiment_id = f"EXP_BL_{strategy_id}_TEST"

    bars_file = Path(db_path).parent / "test_bars.csv"
    bars_file.write_text("time\topen\thigh\tlow\tclose\ttick_volume\n2024-01-01\t100\t110\t90\t105\t1000\n", encoding="utf-8")

    from automated.research.datasets import register_dataset
    ds = register_dataset(db_path, bars_path=bars_file, symbol="XAUUSD", timeframe="H4")
    ds_id = ds["dataset_id"]

    metrics = {"net_return": 500.0, "profit_factor": 1.8, "trade_count": 25, "max_drawdown": 12.0}

    exp_payload = {
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
        "change_summary": "test baseline",
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
        "headline_metrics_json": json.dumps(metrics),
    }
    registry.create_experiment(db_path, exp_payload)

    usage_id = f"USAGE_BL_{strategy_id}_TEST"
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

    review_dir = REPO_ROOT / "automated" / "research_runs" / experiment_id / "reports"
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
        "baseline_metrics": metrics,
        "red_team_results": {"warnings": [], "risk_flags": [], "overall_assessment": "low_risk"},
    }
    review_path.write_text(yaml.safe_dump(review_packet, sort_keys=False), encoding="utf-8")
    registry.attach_artifact(db_path, experiment_id, "generated_baseline_review", review_path)

    return experiment_id


def _create_sweep_with_robustness_review(db_path, baseline_experiment_id, strategy_id):
    sweep_id = f"SWEEP_GR_{strategy_id}_TEST"
    registry.create_sweep(
        db_path,
        {
            "sweep_id": sweep_id,
            "parent_experiment_id": baseline_experiment_id,
            "strategy_id": strategy_id,
            "hypothesis_id": "HYP_GEN_FBR_RANGING_000",
            "sweep_type": "parameter_robustness",
            "status": "completed",
            "created_by": "test",
            "created_at": registry.utc_now(),
            "completed_at": registry.utc_now(),
            "budget_json": json.dumps({"max_child_experiments": 4, "max_parameters_changed_per_child": 1}),
            "config": {},
            "summary_path": None,
            "notes": "",
        },
    )

    child_configs = [
        (2.0, 300.0, 30, "completed"),
        (1.5, 100.0, 25, "completed"),
        (0.8, -50.0, 20, "failed"),
        (1.2, 50.0, 15, "completed"),
    ]

    bars_file = Path(db_path).parent / "test_bars.csv"
    if not bars_file.is_file():
        bars_file.write_text("time\topen\thigh\tlow\tclose\ttick_volume\n2024-01-01\t100\t110\t90\t105\t1000\n", encoding="utf-8")

    from automated.research.datasets import register_dataset
    ds = register_dataset(db_path, bars_path=bars_file, symbol="XAUUSD", timeframe="H4")
    ds_id = ds["dataset_id"]

    for i, (pf, nr, tc, status) in enumerate(child_configs):
        child_id = f"CHILD_P14_{strategy_id}_{i:03d}"
        child_payload = {
            "experiment_id": child_id,
            "hypothesis_id": "HYP_GEN_FBR_RANGING_000",
            "strategy_id": strategy_id,
            "strategy_version": "v1",
            "run_reason": "test",
            "created_by": "test",
            "created_at": registry.utc_now(),
            "spec_hash": "fake",
            "parameter_set_hash": "fake",
            "dataset_id": ds_id,
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
            "parent_experiment_id": baseline_experiment_id,
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
            "status": status,
            "headline_metrics_json": json.dumps({
                "net_return": nr, "profit_factor": pf, "trade_count": tc,
            }),
        }
        registry.create_experiment(db_path, child_payload)

        registry.add_sweep_child(db_path, {
            "sweep_id": sweep_id,
            "child_experiment_id": child_id,
            "child_index": i,
            "child_role": f"param_{i}",
            "status": status,
        })

    review_dir = REPO_ROOT / "automated" / "research_runs" / baseline_experiment_id / "reports"
    review_dir.mkdir(parents=True, exist_ok=True)
    review_path = review_dir / "generated_robustness_review.yaml"
    review_packet = {
        "schema_version": "generated_robustness_review_v1",
        "strategy_id": strategy_id,
        "parent_experiment_id": baseline_experiment_id,
        "recommendation": "consider_lifecycle_candidate",
        "children_completed": 3,
        "children_failed": 1,
        "red_team_assessment": {
            "overall_assessment": "low_risk",
            "risk_flags": [],
        },
        "robustness_warnings": [],
    }
    review_path.write_text(yaml.safe_dump(review_packet, sort_keys=False), encoding="utf-8")
    registry.attach_artifact(db_path, baseline_experiment_id, "generated_robustness_review", review_path)

    return sweep_id


def _register_test_dataset(db_path: Path) -> str:
    from automated.research.datasets import register_dataset
    bars_file = db_path.parent / "test_bars.csv"
    if not bars_file.is_file():
        bars_file.write_text("time\topen\thigh\tlow\tclose\ttick_volume\n2024-01-01\t100\t110\t90\t105\t1000\n", encoding="utf-8")
    ds = register_dataset(db_path, bars_path=bars_file, symbol="XAUUSD", timeframe="H4")
    return ds["dataset_id"]


def _create_decision_packet(
    strategy_id,
    *,
    proposed_next_action="request_human_review_for_final_holdout",
    lifecycle_proposal="final_holdout_candidate",
    output_dir=None,
):
    out_dir = output_dir or Path(tempfile.mkdtemp())
    out_dir.mkdir(parents=True, exist_ok=True)
    packet_path = out_dir / "decision_packet.yaml"
    packet = {
        "schema_version": "generated_candidate_decision_packet_v1",
        "strategy_id": strategy_id,
        "strategy_version": "v1",
        "spec_summary": {"universe": ["XAUUSD"], "timeframe": "H4", "parameters_count": 5, "filters_count": 0},
        "baseline_summary": {"experiment_id": "EXP_BL_dummy", "net_return": 500.0, "profit_factor": 1.8},
        "robustness_summary": {"sweep_id": "SWEEP_GR_dummy", "children_completed": 3, "median_profit_factor": 1.35},
        "validation_summary": {"hard_failures": [], "warnings": [], "gate_status": "pass"},
        "red_team_summary": {"baseline_assessment": "low_risk", "robustness_assessment": "low_risk"},
        "unresolved_warnings": [],
        "evidence_gaps": [],
        "candidate_status": "eligible",
        "proposed_next_action": proposed_next_action,
        "lifecycle_proposal": lifecycle_proposal,
        "decision_rules_applied": ["baseline_passed_robustness_low_medium_risk"],
        "created_at": registry.utc_now(),
    }
    packet_path.write_text(yaml.safe_dump(packet, sort_keys=False), encoding="utf-8")
    return str(packet_path)


class ResearchPhase14ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.strategy_id = "FH_TEST_ST_001"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"

    def tearDown(self):
        self.temp_dir.cleanup()
        cleanup_path(self.sandbox)
        cleanup_path(GENERATED_SPECS_DIR / f"{self.strategy_id}.yaml")
        cleanup_path(REPO_ROOT / "automated" / "research_runs" / f"EXP_BL_{self.strategy_id}_TEST")

    def _full_setup(self):
        approval, sandbox = _full_approval(self.db_path, self.strategy_id)

class ResearchPhase14CrossScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.strategy_id = "FH_CROSS_001"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"

    def tearDown(self):
        self.temp_dir.cleanup()
        cleanup_path(self.sandbox)
        cleanup_path(GENERATED_SPECS_DIR / f"{self.strategy_id}.yaml")
        cleanup_path(REPO_ROOT / "automated" / "research_runs" / f"EXP_BL_{self.strategy_id}_TEST")

    def _full_setup(self):
        approval, sandbox = _full_approval(self.db_path, self.strategy_id)

class ResearchPhase14EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.strategy_id = "FH_ELIG_001"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"

    def tearDown(self):
        self.temp_dir.cleanup()
        cleanup_path(self.sandbox)
        cleanup_path(GENERATED_SPECS_DIR / f"{self.strategy_id}.yaml")
        cleanup_path(REPO_ROOT / "automated" / "research_runs" / f"EXP_BL_{self.strategy_id}_TEST")

    def _full_setup(self):
        approval, sandbox = _full_approval(self.db_path, self.strategy_id)

class ResearchPhase14ReviewArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.ds_id = _register_test_dataset(self.db_path)
        self.strategy_id = "FH_REVIEW_001"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"

    def tearDown(self):
        self.temp_dir.cleanup()
        cleanup_path(self.sandbox)
        cleanup_path(GENERATED_SPECS_DIR / f"{self.strategy_id}.yaml")
        cleanup_path(REPO_ROOT / "automated" / "research_runs" / f"EXP_BL_{self.strategy_id}_TEST")

    def _full_setup(self):
        approval, sandbox = _full_approval(self.db_path, self.strategy_id)
        impl_request_id = approval["implementation_request_id"]
        impl_id = approval["implementation_id"]
        bl_exp_id = _create_baseline_with_review(
            self.db_path, self.strategy_id, impl_request_id, impl_id, sandbox, self.mq5_path,
        )
        sweep_id = _create_sweep_with_robustness_review(self.db_path, bl_exp_id, self.strategy_id)
        dp_path = _create_decision_packet(self.strategy_id)
        fh_result = generated_final_holdout.approve_for_final_holdout(
            self.db_path, impl_request_id, decision_packet_path=dp_path, approved_by="human_test",
        )
        return impl_request_id, impl_id, bl_exp_id, sweep_id, dp_path, fh_result

    def test_review_artifact_produced(self) -> None:
        _, _, bl_exp_id, _, dp_path, fh_result = self._full_setup()
        fh_exp_id = "FH_EXP_REVIEW_001"
        metrics = {"net_return": 600.0, "profit_factor": 2.0, "trade_count": 20, "max_drawdown": 8.0}
        exp_payload = {
            "experiment_id": fh_exp_id,
            "hypothesis_id": "HYP_GEN_FBR_RANGING_000",
            "strategy_id": self.strategy_id,
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
            "parent_experiment_id": bl_exp_id,
            "rerun_of_experiment_id": None,
            "is_artifact_regeneration": False,
            "change_type": "final_holdout",
            "change_summary": "Final holdout experiment for testing",
            "rationale": "test final holdout review",
            "research_budget_snapshot": {},
            "complexity_score": 0,
            "min_trades_required": 10,
            "cost_assumptions_documented": True,
            "dataset_metadata_present": True,
            "hypothesis_present": True,
            "validation_report_path": None,
            "started_at": None,
            "completed_at": None,
            "gate_status": "pass",
        }
        registry.create_experiment(self.db_path, exp_payload)

        review_result = generated_final_holdout.build_generated_final_holdout_review_packet(
            self.db_path,
            experiment_id=fh_exp_id,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            decision_packet_path=dp_path,
            approval_id=fh_result["approval_id"],
        )
        self.assertIn("packet_path", review_result)
        self.assertIn("packet", review_result)
        packet = review_result["packet"]
        self.assertEqual(packet["schema_version"], "generated_final_holdout_review_v1")
        self.assertEqual(packet["status"], "pass")
        self.assertEqual(packet["approval_id"], fh_result["approval_id"])

    def test_review_contains_approval_usage_evidence(self) -> None:
        _, _, bl_exp_id, _, dp_path, fh_result = self._full_setup()
        fh_exp_id = "FH_EXP_REVIEW_002"
        exp_payload = {
            "experiment_id": fh_exp_id,
            "hypothesis_id": "HYP_GEN_FBR_RANGING_000",
            "strategy_id": self.strategy_id,
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
            "parent_experiment_id": bl_exp_id,
            "rerun_of_experiment_id": None,
            "is_artifact_regeneration": False,
            "change_type": "final_holdout",
            "change_summary": "Final holdout experiment for testing usage",
            "rationale": "test",
            "research_budget_snapshot": {},
            "complexity_score": 0,
            "min_trades_required": 10,
            "cost_assumptions_documented": True,
            "dataset_metadata_present": True,
            "hypothesis_present": True,
            "validation_report_path": None,
            "gate_status": "pass",
            "started_at": None,
            "completed_at": None,
            "status": "completed",
            "headline_metrics_json": json.dumps({"net_return": 500.0, "profit_factor": 1.5, "trade_count": 15}),
        }
        registry.create_experiment(self.db_path, exp_payload)

        usage_id = f"USAGE_FH_REVIEW_{self.strategy_id}"
        usage_record = {
            "usage_id": usage_id,
            "implementation_id": fh_result.get("implementation_id", ""),
            "implementation_request_id": fh_result["implementation_request_id"],
            "experiment_id": fh_exp_id,
            "queue_run_id": None,
            "used_at": registry.utc_now(),
            "runner_mode": "final_holdout",
            "status": "completed",
            "scope_approval_id": fh_result["approval_id"],
        }
        registry.create_approval_usage(self.db_path, usage_record)

        review_result = generated_final_holdout.build_generated_final_holdout_review_packet(
            self.db_path,
            experiment_id=fh_exp_id,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            decision_packet_path=dp_path,
            approval_id=fh_result["approval_id"],
        )
        packet = review_result["packet"]
        self.assertIsNotNone(packet.get("approval_usage"))
        self.assertEqual(packet["approval_usage"]["scope_approval_id"], fh_result["approval_id"])
        self.assertEqual(packet["approval_usage"]["runner_mode"], "final_holdout")

    def test_review_artifact_type_registered(self) -> None:
        self.assertIn("generated_final_holdout_review", ARTIFACT_TYPES)


class ResearchPhase14DecisionPacketIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.ds_id = _register_test_dataset(self.db_path)
        self.strategy_id = "FH_DEC_001"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"

    def tearDown(self):
        self.temp_dir.cleanup()
        cleanup_path(self.sandbox)
        cleanup_path(GENERATED_SPECS_DIR / f"{self.strategy_id}.yaml")
        cleanup_path(REPO_ROOT / "automated" / "research_runs" / f"EXP_BL_{self.strategy_id}_TEST")

    def _full_setup(self):
        approval, sandbox = _full_approval(self.db_path, self.strategy_id)
        impl_request_id = approval["implementation_request_id"]
        impl_id = approval["implementation_id"]
        bl_exp_id = _create_baseline_with_review(
            self.db_path, self.strategy_id, impl_request_id, impl_id, sandbox, self.mq5_path,
        )
        sweep_id = _create_sweep_with_robustness_review(self.db_path, bl_exp_id, self.strategy_id)
        return impl_request_id, impl_id, bl_exp_id, sweep_id

    def test_decision_packet_aggregates_final_holdout_evidence(self) -> None:
        impl_request_id, impl_id, bl_exp_id, sweep_id = self._full_setup()
        dp_path = _create_decision_packet(self.strategy_id)
        fh_result = generated_final_holdout.approve_for_final_holdout(
            self.db_path, impl_request_id, decision_packet_path=dp_path, approved_by="human_test",
        )

        fh_exp_id = "FH_EXP_DEC_001"
        metrics = {"net_return": 600.0, "profit_factor": 2.0, "trade_count": 22, "max_drawdown": 7.0}
        exp_payload = {
            "experiment_id": fh_exp_id,
            "hypothesis_id": "HYP_GEN_FBR_RANGING_000",
            "strategy_id": self.strategy_id,
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
            "parent_experiment_id": bl_exp_id,
            "rerun_of_experiment_id": None,
            "is_artifact_regeneration": False,
            "change_type": "final_holdout",
            "change_summary": "Final holdout for decision packet test",
            "rationale": "test",
            "research_budget_snapshot": {},
            "complexity_score": 0,
            "min_trades_required": 10,
            "cost_assumptions_documented": True,
            "dataset_metadata_present": True,
            "hypothesis_present": True,
            "validation_report_path": None,
            "gate_status": "pass",
            "started_at": None,
            "completed_at": None,
            "status": "completed",
            "headline_metrics_json": json.dumps(metrics),
        }
        registry.create_experiment(self.db_path, exp_payload)

        review_result = generated_final_holdout.build_generated_final_holdout_review_packet(
            self.db_path,
            experiment_id=fh_exp_id,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            decision_packet_path=dp_path,
            approval_id=fh_result["approval_id"],
        )

        result = generated_candidate.build_generated_candidate_decision_packet(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            implementation_request_id=impl_request_id,
            baseline_experiment_id=bl_exp_id,
            robustness_sweep_id=sweep_id,
        )
        packet = result["packet"]
        self.assertIn("final_holdout_summary", packet)
        fh_summary = packet["final_holdout_summary"]
        self.assertIn("experiment_id", fh_summary)
        self.assertEqual(fh_summary["status"], "pass")

    def test_decision_packet_no_production_promotion(self) -> None:
        impl_request_id, impl_id, bl_exp_id, sweep_id = self._full_setup()
        dp_path = _create_decision_packet(self.strategy_id)
        result = generated_candidate.build_generated_candidate_decision_packet(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            implementation_request_id=impl_request_id,
            baseline_experiment_id=bl_exp_id,
            robustness_sweep_id=sweep_id,
        )
        packet = result["packet"]
        na = packet["proposed_next_action"]
        lp = packet["lifecycle_proposal"]
        forbidden_promotions = {"promote_to_production", "production_candidate", "live_trading_candidate"}
        self.assertNotIn(na, forbidden_promotions)
        self.assertNotIn(lp, forbidden_promotions)
        packet_str = json.dumps(packet)
        self.assertNotIn("promote_to_production", packet_str)
        self.assertNotIn("production_candidate", packet_str)
        self.assertNotIn("live_trading_candidate", packet_str)

    def test_decision_packet_proposes_final_holdout_candidate(self) -> None:
        impl_request_id, impl_id, bl_exp_id, sweep_id = self._full_setup()
        result = generated_candidate.build_generated_candidate_decision_packet(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            implementation_request_id=impl_request_id,
            baseline_experiment_id=bl_exp_id,
            robustness_sweep_id=sweep_id,
        )
        packet = result["packet"]
        self.assertIn(packet["lifecycle_proposal"], {"none", "final_holdout_candidate", "robustness_candidate", "research_candidate"})


class ResearchPhase14NoProductionPathTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.ds_id = _register_test_dataset(self.db_path)
        self.strategy_id = "FH_NOPROD_001"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"

    def tearDown(self):
        self.temp_dir.cleanup()
        cleanup_path(self.sandbox)
        cleanup_path(GENERATED_SPECS_DIR / f"{self.strategy_id}.yaml")
        cleanup_path(REPO_ROOT / "automated" / "research_runs" / f"EXP_BL_{self.strategy_id}_TEST")

    def _full_setup(self):
        approval, sandbox = _full_approval(self.db_path, self.strategy_id)
        impl_request_id = approval["implementation_request_id"]
        sandbox2 = SANDBOX_ROOT / self.strategy_id / "v1"
        mq5 = sandbox2 / f"{self.strategy_id}.mq5"
        bl_exp_id = _create_baseline_with_review(
            self.db_path, self.strategy_id, impl_request_id, approval["implementation_id"], sandbox2, mq5,
        )
        sweep_id = _create_sweep_with_robustness_review(self.db_path, bl_exp_id, self.strategy_id)
        dp_path = _create_decision_packet(self.strategy_id)
        result = generated_final_holdout.approve_for_final_holdout(
            self.db_path, impl_request_id, decision_packet_path=dp_path, approved_by="human_test",
        )
        strategies_dir = REPO_ROOT / "automated" / "strategies"
        mq5_files_in_strategies = list(strategies_dir.rglob("*.mq5"))
        fh_mq5_refs = [s for s in mq5_files_in_strategies if self.strategy_id in s.name]
        self.assertEqual(len(fh_mq5_refs), 0)

    def test_review_does_not_apply_lifecycle(self) -> None:
        approval, sandbox = _full_approval(self.db_path, self.strategy_id)
        impl_request_id = approval["implementation_request_id"]
        sandbox2 = SANDBOX_ROOT / self.strategy_id / "v1"
        mq5 = sandbox2 / f"{self.strategy_id}.mq5"
        bl_exp_id = _create_baseline_with_review(
            self.db_path, self.strategy_id, impl_request_id, approval["implementation_id"], sandbox2, mq5,
        )
        sweep_id = _create_sweep_with_robustness_review(self.db_path, bl_exp_id, self.strategy_id)
        dp_path = _create_decision_packet(self.strategy_id)

        fh_exp_id = "FH_EXP_NOPROD_001"
        exp_payload = {
            "experiment_id": fh_exp_id,
            "hypothesis_id": "HYP_GEN_FBR_RANGING_000",
            "strategy_id": self.strategy_id,
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
            "parent_experiment_id": bl_exp_id,
            "rerun_of_experiment_id": None,
            "is_artifact_regeneration": False,
            "change_type": "final_holdout",
            "change_summary": "Final holdout test for lifecycle",
            "rationale": "test",
            "research_budget_snapshot": {},
            "complexity_score": 0,
            "min_trades_required": 10,
            "cost_assumptions_documented": True,
            "dataset_metadata_present": True,
            "hypothesis_present": True,
            "validation_report_path": None,
            "gate_status": "pass",
            "started_at": None,
            "completed_at": None,
            "status": "completed",
            "headline_metrics_json": json.dumps({"net_return": 500.0, "profit_factor": 1.5, "trade_count": 15}),
        }
        registry.create_experiment(self.db_path, exp_payload)

        review_result = generated_final_holdout.build_generated_final_holdout_review_packet(
            self.db_path,
            experiment_id=fh_exp_id,
            strategy_id=self.strategy_id,
            strategy_version="v1",
        )
        packet = review_result["packet"]

        self.assertNotIn("lifecycle", packet)
        self.assertNotIn("lifecycle_proposal", packet)
        self.assertNotIn("promote", str(packet))
        self.assertNotIn("production", str(packet))


class ResearchPhase14CLISmokeTests(unittest.TestCase):
    def test_cli_help(self) -> None:
        from io import StringIO
        from contextlib import redirect_stdout
        parser = cli.build_parser()
        for cmd in ["generated-final-holdout"]:
            sub = parser._subparsers._group_actions[0].choices.get(cmd)
            self.assertIsNotNone(sub, f"CLI group '{cmd}' not found")

    def test_cli_subcommand_help(self) -> None:
        parser = cli.build_parser()
        gfh = parser._subparsers._group_actions[0].choices.get("generated-final-holdout")
        self.assertIsNotNone(gfh)
        for sub_cmd in ["approve", "run", "review"]:
            sub = gfh._subparsers._group_actions[0].choices.get(sub_cmd)
            self.assertIsNotNone(sub, f"CLI subcommand '{sub_cmd}' not found")


class ResearchPhase14ConstantsTests(unittest.TestCase):
    def test_final_holdout_scope_registered(self) -> None:
        self.assertIn("final_holdout_only", APPROVAL_SCOPES)

    def test_final_holdout_review_artifact_type_registered(self) -> None:
        self.assertIn("generated_final_holdout_review", ARTIFACT_TYPES)

    def test_no_forbidden_lifecycle_proposals(self) -> None:
        from automated.research.generated_candidate import ALLOWED_LIFECYCLE_PROPOSALS
        forbidden = {"promote_to_production", "production_candidate", "live_trading_candidate"}
        for f in forbidden:
            self.assertNotIn(f, ALLOWED_LIFECYCLE_PROPOSALS)

    def test_no_forbidden_next_actions(self) -> None:
        from automated.research.generated_candidate import ALLOWED_PROPOSED_NEXT_ACTIONS
        forbidden = {"promote_to_production", "live_trading"}
        for f in forbidden:
            self.assertNotIn(f, ALLOWED_PROPOSED_NEXT_ACTIONS)

    def test_final_holdout_queue_task_types_registered(self) -> None:
        self.assertIn("generated_final_holdout_experiment", queue.TASK_TYPES)
        self.assertIn("generated_final_holdout_review", queue.TASK_TYPES)


class ResearchPhase14QueuePermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_queue_yaml(self, task_type, overrides=None):
        item = {
            "queue_id": "Q_FH_TEST",
            "priority": 1,
            "hypothesis_id": "HYP_001",
            "strategy_id": "FH_Q_001",
            "strategy_version": "v1",
            "task_type": task_type,
            "requested_by": "test",
            "created_at": "2026-05-12T00:00:00+00:00",
            "allowed_agent_roles": [],
            "budget": {"max_experiments": 1, "max_child_experiments": 0, "max_runtime_minutes": 60,
                       "max_parameters_changed_per_child": 0, "max_sweeps": 0, "max_failed_runs": 0,
                       "max_disk_usage_mb": 512, "require_one_variable_at_a_time": False},
            "permissions": {"allow_runner_execution": False, "allow_lifecycle_apply": False,
                            "allow_final_holdout": False, "allow_mql5_edits": False,
                            "allow_dataset_changes": False, "allow_validation_threshold_changes": False,
                            "allow_lifecycle_propose": True},
        }
        if task_type == "generated_final_holdout_experiment":
            item["implementation_request_id"] = "IR_FH_001"
            item["dataset_id"] = "DS_FH_001"
            item["approval_id"] = "FH_APPROVAL_001"
        if task_type == "generated_final_holdout_review":
            item["parent_experiment_id"] = "EXP_FH_001"
        if overrides:
            item.update(overrides)
        queue_path = Path(self.temp_dir.name) / f"queue_{task_type}.yaml"
        queue_data = {"queue_id": f"Q_{task_type}", "items": [item]}
        queue_path.write_text(yaml.safe_dump(queue_data, sort_keys=False), encoding="utf-8")
        return queue_path

    def test_experiment_requires_allow_final_holdout_true(self) -> None:
        queue_path = self._make_queue_yaml("generated_final_holdout_experiment")
        with self.assertRaises(Exception):
            queue.validate_queue(self.db_path, queue_path, persist=False)

    def test_review_validates(self) -> None:
        queue_path = self._make_queue_yaml("generated_final_holdout_review")
        result = queue.validate_queue(self.db_path, queue_path, persist=False)
        item = result["items"][0]
        self.assertEqual(item["task_type"], "generated_final_holdout_review")


if __name__ == "__main__":
    unittest.main()
