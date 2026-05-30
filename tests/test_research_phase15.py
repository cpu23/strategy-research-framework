from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import yaml

from automated.research import (
    cli,
    generated_baseline,
    generated_candidate,
    generated_final_holdout,
    generated_robustness,
    implementation as impl_mod,
    queue,
    registry,
    runner,
)
from automated.research.contracts import ARTIFACT_TYPES
from automated.research.schemas import (
    GENERATED_SPECS_DIR,
    HYPOTHESES_DIR,
    REPO_ROOT,
    SANDBOX_ROOT,
)

# TODO: Phase 11-14 test files have similar fixture cleanup gaps (generated_specs/,
# research_runs/, and sandbox dirs persist across test classes).  Consolidate cleanup
# into a shared test base class or fixture in a later phase.


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
        "status": "idea",
        "created_at": "2026-05-12",
        "updated_at": "2026-05-12",
        "invalidation_rule": "Reject if baseline tests fail.",
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
            "generation_mode": "wrapped_existing_files",
            "files": {
                "expert_advisor": str(mq5_path),
                "config": str(REPO_ROOT / "automated" / "runs" / "example.conf"),
                "parameters": str(REPO_ROOT / "automated" / "runs" / "sets" / "example.set"),
            },
        },
        "execution_timing": {"signal_bar": "closed_bar", "entry_bar": "next_bar", "assumed_fill_price": "market"},
        "costs": {
            "assumptions_documented": True,
            "spread_source": {"type": "mt5_tester", "description": "Broker/tester spread model."},
            "slippage": {"type": "points", "value": 20, "source": "InpSlippagePoints"},
            "commission": {"type": "broker_account_or_tester_default", "value": None, "description": "No explicit commission override."},
            "stress_multiplier": None,
        },
        "validation": {"min_trades_required": 10, "min_profit_factor": 1.0},
        "research_budget": {"max_structural_variants": 3, "max_parameter_sets": 5, "max_filter_additions": 3, "max_agent_iterations": 2, "max_complexity_score": 30},
        "lifecycle": {"state": "idea", "allowed_next_states": ["hypothesis_defined"]},
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
        child_id = f"CHILD_P15_{strategy_id}_{i:03d}"
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


def _create_decision_packet(strategy_id, output_dir=None, **overrides):
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
        "proposed_next_action": "request_human_review_for_final_holdout",
        "lifecycle_proposal": "final_holdout_candidate",
        "decision_rules_applied": ["baseline_passed_robustness_low_medium_risk"],
        "created_at": registry.utc_now(),
    }
    packet.update(overrides)
    packet_path.write_text(yaml.safe_dump(packet, sort_keys=False), encoding="utf-8")
    return str(packet_path)


def _register_test_dataset(db_path):
    from automated.research.datasets import register_dataset
    bars_file = Path(db_path).parent / "test_bars.csv"
    if not bars_file.is_file():
        bars_file.write_text("time\topen\thigh\tlow\tclose\ttick_volume\n2024-01-01\t100\t110\t90\t105\t1000\n", encoding="utf-8")
    ds = register_dataset(db_path, bars_path=bars_file, symbol="XAUUSD", timeframe="H4")
    return ds["dataset_id"]


def _cleanup_path(path: Path) -> None:
    import shutil
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.is_file():
        path.unlink()


class TestQueueCannotBypassMissingApproval(unittest.TestCase):
    """TG-4: queue allow_final_holdout=True cannot bypass missing final_holdout_only approval."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.strategy_id = "P15_Q_BYPASS"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"

    def tearDown(self):
        self.temp_dir.cleanup()
        _cleanup_path(self.sandbox)
        _cleanup_path(GENERATED_SPECS_DIR / f"{self.strategy_id}.yaml")
        _cleanup_path(REPO_ROOT / "automated" / "research_runs" / f"EXP_BL_{self.strategy_id}_TEST")

    def _queue_item_dict(self, **overrides) -> dict:
        item = {
            "queue_id": "P15_Q_BYPASS_001",
            "priority": 10,
            "task_type": "generated_final_holdout_experiment",
            "strategy_id": self.strategy_id,
            "strategy_version": "v1",
            "hypothesis_id": "HYP_GEN_FBR_RANGING_000",
            "implementation_request_id": "IR_BYPASS_001",
            "dataset_id": "DS_BYPASS_001",
            "approval_id": "FH_APPROVAL_NONEXISTENT",
            "requested_by": "test",
            "created_at": registry.utc_now(),
            "allowed_agent_roles": [],
            "budget": {
                "max_experiments": 1, "max_child_experiments": 0, "max_runtime_minutes": 10,
                "max_parameters_changed_per_child": 0, "max_sweeps": 0, "max_failed_runs": 0,
                "max_disk_usage_mb": 512, "require_one_variable_at_a_time": False,
            },
            "permissions": {
                "allow_runner_execution": False,
                "allow_mql5_edits": False,
                "allow_dataset_changes": False,
                "allow_validation_threshold_changes": False,
                "allow_lifecycle_apply": False,
                "allow_lifecycle_propose": False,
                "allow_final_holdout": True,
            },
            "status": "queued",
            "notes": "",
        }
        item.update(overrides)
        return item

    def test_missing_approval_rejected(self):
        item = self._queue_item_dict()
        queue_run_dir = Path(self.temp_dir.name) / "queue_run"
        queue_run_dir.mkdir(parents=True, exist_ok=True)

        result = queue._execute_generated_final_holdout_experiment(
            self.db_path,
            item,
            queue_run_dir,
            runner_script=REPO_ROOT / "automated" / "scripts" / "run_backtest.sh",
            research_output_root=Path(self.temp_dir.name) / "research_runs",
        )
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("scope approval not found" in f for f in result.get("failures", [])))
        self.assertFalse(result.get("experiments_created"))
        self.assertFalse(result.get("artifacts_created"))

    def test_approval_strategy_id_mismatch_rejected(self):
        approval, sandbox = _full_approval(self.db_path, self.strategy_id)
        impl_request_id = approval["implementation_request_id"]
        impl_id = approval["implementation_id"]
        bl_exp_id = _create_baseline_with_review(
            self.db_path, self.strategy_id, impl_request_id, impl_id, sandbox, self.mq5_path,
        )
        sweep_id = _create_sweep_with_robustness_review(self.db_path, bl_exp_id, self.strategy_id)
        dp_path = _create_decision_packet(self.strategy_id)
        fh_result = generated_final_holdout.approve_for_final_holdout(
            self.db_path, impl_request_id,
            decision_packet_path=dp_path, approved_by="test",
        )
        self.assertTrue(fh_result["approved"])

        other_id = "FH_OTHER_STRAT"
        conn = registry.connect(self.db_path)
        conn.execute(
            "UPDATE scope_approvals SET strategy_id = ? WHERE approval_id = ?",
            ("OTHER_STRATEGY", fh_result["approval_id"]),
        )
        conn.commit()
        conn.close()

        item = self._queue_item_dict(approval_id=fh_result["approval_id"])
        queue_run_dir = Path(self.temp_dir.name) / "queue_run2"
        queue_run_dir.mkdir(parents=True, exist_ok=True)

        result = queue._execute_generated_final_holdout_experiment(
            self.db_path,
            item,
            queue_run_dir,
            runner_script=REPO_ROOT / "automated" / "scripts" / "run_backtest.sh",
            research_output_root=Path(self.temp_dir.name) / "research_runs2",
        )
        self.assertEqual(result["status"], "failed")
        failures_str = "; ".join(result.get("failures", []))
        self.assertIn("strategy_id mismatch", failures_str)


class TestGeneratedBaselineRedTeamCurrentValidationSchema(unittest.TestCase):
    def test_passed_cost_gate_does_not_raise_missing_cost_flag(self):
        validation_report = {
            "sections": {
                "cost_assumption_gate": {
                    "status": "pass",
                    "reason": "cost assumptions are documented",
                }
            }
        }
        result = generated_baseline.red_team_check_generated_baseline(
            metrics={"trade_count": 30, "profit_factor": 1.5, "net_return": 0.01, "max_drawdown": 5},
            validation_report=validation_report,
            diff_review_warnings=[],
            compile_status="wrapped_existing_files",
            universe=["XAUUSD"],
            timeframes=["H4"],
            min_trades_required=30,
        )
        self.assertNotIn("missing_cost_assumptions", result["risk_flags"])


class TestFinalHoldoutEligibilityCompletedWithWarnings(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.strategy_id = "P15_FH_WARN_OK"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"

    def tearDown(self):
        self.temp_dir.cleanup()
        _cleanup_path(self.sandbox)
        _cleanup_path(GENERATED_SPECS_DIR / f"{self.strategy_id}.yaml")
        _cleanup_path(REPO_ROOT / "automated" / "research_runs" / f"EXP_BL_{self.strategy_id}_TEST")

    def test_completed_with_warnings_baseline_remains_eligible(self):
        approval, sandbox = _full_approval(self.db_path, self.strategy_id)
        impl_request_id = approval["implementation_request_id"]
        impl_id = approval["implementation_id"]
        baseline_id = _create_baseline_with_review(
            self.db_path, self.strategy_id, impl_request_id, impl_id, sandbox, self.mq5_path,
        )
        registry.update_experiment(self.db_path, baseline_id, status="completed_with_warnings")
        _create_sweep_with_robustness_review(self.db_path, baseline_id, self.strategy_id)
        packet_path = _create_decision_packet(self.strategy_id)
        fh_approval = generated_final_holdout.approve_for_final_holdout(
            self.db_path,
            impl_request_id,
            decision_packet_path=packet_path,
            approved_by="test",
        )
        self.assertTrue(fh_approval["approved"])

        result = generated_final_holdout.require_generated_final_holdout_eligibility(
            self.db_path,
            self.strategy_id,
            "v1",
            decision_packet_path=packet_path,
            allow_mock_compile=True,
        )
        self.assertTrue(result["eligible"], result.get("errors"))


class TestReviewDoesNotCreateLifecycleTransition(unittest.TestCase):
    """TG-5: generated final holdout review is evidence-only, does not mutate lifecycle."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.ds_id = _register_test_dataset(self.db_path)
        self.strategy_id = "P15_NO_LIFECYCLE"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"

    def tearDown(self):
        self.temp_dir.cleanup()
        _cleanup_path(self.sandbox)
        _cleanup_path(GENERATED_SPECS_DIR / f"{self.strategy_id}.yaml")
        _cleanup_path(REPO_ROOT / "automated" / "research_runs" / f"EXP_BL_{self.strategy_id}_TEST")

    def _full_setup(self):
        approval, sandbox = _full_approval(self.db_path, self.strategy_id)
        impl_request_id = approval["implementation_request_id"]
        impl_id = approval["implementation_id"]
        bl_exp_id = _create_baseline_with_review(
            self.db_path, self.strategy_id, impl_request_id, impl_id, sandbox, self.mq5_path,
        )
        sweep_id = _create_sweep_with_robustness_review(self.db_path, bl_exp_id, self.strategy_id)
        return impl_request_id, impl_id, bl_exp_id, sweep_id

    def test_review_does_not_apply_lifecycle(self):
        _, _, bl_exp_id, _ = self._full_setup()

        fh_exp_id = "FH_EXP_NO_LC_001"
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
            "change_summary": "Final holdout test review",
            "rationale": "test review lifecycle check",
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

        transitions_before = registry.list_lifecycle_transitions(self.db_path, self.strategy_id)
        self.assertEqual(len(transitions_before), 0)

        review_result = generated_final_holdout.build_generated_final_holdout_review_packet(
            self.db_path,
            experiment_id=fh_exp_id,
            strategy_id=self.strategy_id,
            strategy_version="v1",
        )
        self.assertIn("packet_path", review_result)
        packet = review_result["packet"]
        self.assertEqual(packet["status"], "pass")

        transitions_after = registry.list_lifecycle_transitions(self.db_path, self.strategy_id)
        self.assertEqual(len(transitions_after), 0)

        packet_str = str(packet)
        self.assertNotIn("promote", packet_str)
        self.assertNotIn("production", packet_str)
        self.assertNotIn("live_trading", packet_str)
        self.assertNotIn("lifecycle_apply", packet_str)


class TestFinalHoldoutRunDoesNotWriteToStrategies(unittest.TestCase):
    """TG-6: final holdout execution does not promote or copy code to automated/strategies/."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.ds_id = _register_test_dataset(self.db_path)
        self.strategy_id = "P15_NO_PROMOTE"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"

    def tearDown(self):
        self.temp_dir.cleanup()
        _cleanup_path(self.sandbox)
        _cleanup_path(GENERATED_SPECS_DIR / f"{self.strategy_id}.yaml")
        _cleanup_path(REPO_ROOT / "automated" / "research_runs" / f"EXP_BL_{self.strategy_id}_TEST")
        _cleanup_path(HYPOTHESES_DIR / "HYP_GEN_FBR_RANGING_000.yaml")

    def _full_setup(self):
        approval, sandbox = _full_approval(self.db_path, self.strategy_id)
        impl_request_id = approval["implementation_request_id"]
        impl_id = approval["implementation_id"]
        bl_exp_id = _create_baseline_with_review(
            self.db_path, self.strategy_id, impl_request_id, impl_id, sandbox, self.mq5_path,
        )
        sweep_id = _create_sweep_with_robustness_review(self.db_path, bl_exp_id, self.strategy_id)
        return approval, impl_request_id, impl_id, bl_exp_id, sweep_id

    def test_prepare_does_not_copy_to_strategies(self):
        approval, impl_request_id, impl_id, bl_exp_id, sweep_id = self._full_setup()

        hyp_dir = REPO_ROOT / "hypotheses"
        hyp_dir.mkdir(parents=True, exist_ok=True)
        hyp_file = hyp_dir / "HYP_GEN_FBR_RANGING_000.yaml"
        if not hyp_file.is_file():
            hyp_file.write_text(yaml.safe_dump({
                "hypothesis_id": "HYP_GEN_FBR_RANGING_000",
                "name": "Test hypothesis",
                "status": "active_research",
                "mechanism": "Test mechanism",
                "expected_edge": "Test edge",
                "initial_test": "Test initial test",
                "invalidation_rule": "Test invalidation",
                "timeframes": ["H4"],
                "markets": ["XAUUSD"],
                "predictions": ["Test prediction"],
                "failure_modes": ["Test failure mode"],
                "created_at": "2026-05-12",
                "updated_at": "2026-05-12",
            }, sort_keys=False), encoding="utf-8")

        strategies_dir = REPO_ROOT / "automated" / "strategies"
        before_mq5 = sorted(strategies_dir.rglob("*.mq5")) if strategies_dir.is_dir() else []
        before_mq5_names = {p.name for p in before_mq5}

        spec_path = GENERATED_SPECS_DIR / f"{self.strategy_id}.yaml"
        run_dir = Path(self.temp_dir.name) / "research_runs"

        config_dir = Path(self.temp_dir.name) / "test_configs"
        config_dir.mkdir(parents=True, exist_ok=True)
        example_conf = config_dir / "example.conf"
        example_conf.write_text("RUN_ID=\"test\"\n", encoding="utf-8")
        example_set = config_dir / "example.set"
        example_set.write_text("InpMagicNumber=12345\n", encoding="utf-8")

        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        spec["implementation"]["files"]["config"] = str(example_conf)
        spec["implementation"]["files"]["parameters"] = str(example_set)
        spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

        try:
            context = runner.prepare_run(
                db_path=self.db_path,
                strategy_spec_path=spec_path,
                dataset_id=self.ds_id,
                experiment_id="FH_EXP_NO_PROMOTE_001",
                output_root=run_dir,
                run_reason="test",
                created_by="test",
                change_type="final_holdout",
                change_summary=f"Final holdout test for {self.strategy_id}.",
                rationale="Test that no promotion occurs.",
            )
            self.assertIn("experiment_id", context)
        except Exception as exc:
            self.fail(f"prepare_run failed: {exc}")

        after_mq5 = sorted(strategies_dir.rglob("*.mq5")) if strategies_dir.is_dir() else []
        after_mq5_names = {p.name for p in after_mq5}
        self.assertEqual(before_mq5_names, after_mq5_names,
                         "prepare_run should not add or remove .mq5 files in strategies/")

        sandbox_mq5 = SANDBOX_ROOT / self.strategy_id / "v1" / f"{self.strategy_id}.mq5"
        self.assertTrue(sandbox_mq5.is_file())
        self.assertTrue(str(sandbox_mq5.resolve()).startswith(str(SANDBOX_ROOT.resolve())))

        strategies_mq5 = strategies_dir / f"{self.strategy_id}.mq5"
        self.assertFalse(strategies_mq5.is_file())


class TestCorruptedDecisionPacketFailsClosed(unittest.TestCase):
    """TG-7: corrupted or invalid decision packet fails closed."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.strategy_id = "P15_CORRUPT"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"

    def tearDown(self):
        self.temp_dir.cleanup()
        _cleanup_path(self.sandbox)

    def _setup_approval(self):
        approval, sandbox = _full_approval(self.db_path, self.strategy_id)
        return approval, sandbox

    def test_invalid_yaml_rejected(self):
        approval, sandbox = self._setup_approval()
        packet_dir = Path(self.temp_dir.name) / "packets"
        packet_dir.mkdir(parents=True, exist_ok=True)
        packet_path = packet_dir / "bad_yaml.yaml"
        packet_path.write_text("{this is not valid yaml: *\nbroken", encoding="utf-8")

        result = generated_final_holdout.approve_for_final_holdout(
            self.db_path, approval["implementation_request_id"],
            decision_packet_path=packet_path, approved_by="test",
        )
        self.assertFalse(result["approved"])
        self.assertTrue(any("not found or invalid" in e for e in result.get("errors", [])))

        eligibility = generated_final_holdout.require_generated_final_holdout_eligibility(
            self.db_path, self.strategy_id, "v1",
            decision_packet_path=packet_path, allow_mock_compile=True,
        )
        self.assertFalse(eligibility["eligible"])

    def test_missing_proposed_next_action_rejected(self):
        approval, sandbox = self._setup_approval()
        dp_path = _create_decision_packet(
            self.strategy_id,
            output_dir=Path(self.temp_dir.name) / "packets2",
            proposed_next_action=None,
        )

        result = generated_final_holdout.approve_for_final_holdout(
            self.db_path, approval["implementation_request_id"],
            decision_packet_path=dp_path, approved_by="test",
        )
        self.assertFalse(result["approved"])
        errors_str = "; ".join(result.get("errors", []))
        self.assertIn("proposed_next_action", errors_str)

    def test_wrong_proposed_next_action_rejected(self):
        approval, sandbox = self._setup_approval()
        dp_path = _create_decision_packet(
            self.strategy_id,
            output_dir=Path(self.temp_dir.name) / "packets3",
            proposed_next_action="reject",
        )

        result = generated_final_holdout.approve_for_final_holdout(
            self.db_path, approval["implementation_request_id"],
            decision_packet_path=dp_path, approved_by="test",
        )
        self.assertFalse(result["approved"])
        errors_str = "; ".join(result.get("errors", []))
        self.assertIn("request_human_review_for_final_holdout", errors_str)

    def test_wrong_lifecycle_proposal_rejected(self):
        approval, sandbox = self._setup_approval()
        dp_path = _create_decision_packet(
            self.strategy_id,
            output_dir=Path(self.temp_dir.name) / "packets4",
            lifecycle_proposal="robustness_candidate",
        )

        result = generated_final_holdout.approve_for_final_holdout(
            self.db_path, approval["implementation_request_id"],
            decision_packet_path=dp_path, approved_by="test",
        )
        self.assertFalse(result["approved"])
        errors_str = "; ".join(result.get("errors", []))
        self.assertIn("final_holdout_candidate", errors_str)

    def test_missing_lifecycle_proposal_rejected(self):
        approval, sandbox = self._setup_approval()
        dp_path = _create_decision_packet(
            self.strategy_id,
            output_dir=Path(self.temp_dir.name) / "packets5",
            lifecycle_proposal=None,
        )

        result = generated_final_holdout.approve_for_final_holdout(
            self.db_path, approval["implementation_request_id"],
            decision_packet_path=dp_path, approved_by="test",
        )
        self.assertFalse(result["approved"])
        errors_str = "; ".join(result.get("errors", []))
        self.assertIn("lifecycle_proposal", errors_str)

    def test_nonexistent_packet_path_rejected(self):
        approval, sandbox = self._setup_approval()
        result = generated_final_holdout.approve_for_final_holdout(
            self.db_path, approval["implementation_request_id"],
            decision_packet_path="/nonexistent/path.yaml", approved_by="test",
        )
        self.assertFalse(result["approved"])
        self.assertTrue(any("not found or invalid" in e for e in result.get("errors", [])))


class TestRegistryMigration(unittest.TestCase):
    """TG-9: registry migration works from an older DB without scope_approvals."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_old_schema_db(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version (
                component TEXT PRIMARY KEY,
                version INTEGER NOT NULL,
                applied_at TEXT NOT NULL
            );
            INSERT OR REPLACE INTO schema_version (component, version, applied_at)
            VALUES ('research_registry', 5, '2026-01-01T00:00:00');

            CREATE TABLE IF NOT EXISTS implementations (
                implementation_id TEXT PRIMARY KEY,
                implementation_request_id TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                generated_mq5_path TEXT NOT NULL,
                code_sha256 TEXT,
                compile_status TEXT,
                diff_review_status TEXT,
                input_match_status TEXT,
                approved_for_baseline INTEGER NOT NULL DEFAULT 0,
                approved_by TEXT,
                approved_at TEXT,
                baseline_experiment_id TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS implementation_requests (
                implementation_request_id TEXT PRIMARY KEY,
                hypothesis_id TEXT,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                strategy_spec_path TEXT,
                request_artifact_path TEXT NOT NULL,
                sandbox_dir TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'proposed',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS approval_usage_records (
                usage_id TEXT PRIMARY KEY,
                implementation_id TEXT NOT NULL,
                implementation_request_id TEXT NOT NULL,
                experiment_id TEXT NOT NULL,
                queue_run_id TEXT,
                used_at TEXT NOT NULL,
                runner_mode TEXT NOT NULL DEFAULT 'baseline',
                status TEXT NOT NULL DEFAULT 'pending'
            );

            INSERT INTO implementation_requests (
                implementation_request_id, strategy_id, strategy_version,
                sandbox_dir, status, created_by, created_at, updated_at,
                request_artifact_path
            ) VALUES (
                'IR_OLD_001', 'OLD_TEST', 'v1',
                '/tmp/sandbox', 'proposed', 'test',
                '2026-01-01T00:00:00', '2026-01-01T00:00:00',
                '/tmp/request.yaml'
            );

            INSERT INTO implementations (
                implementation_id, implementation_request_id,
                strategy_id, strategy_version, generated_mq5_path,
                compile_status, approved_for_baseline, created_at
            ) VALUES (
                'IMPL_OLD_001', 'IR_OLD_001',
                'OLD_TEST', 'v1', '/tmp/test.mq5',
                'mock_checked', 0, '2026-01-01T00:00:00'
            );

            INSERT INTO approval_usage_records (
                usage_id, implementation_id, implementation_request_id,
                experiment_id, used_at, runner_mode, status
            ) VALUES (
                'USAGE_OLD_001', 'IMPL_OLD_001', 'IR_OLD_001',
                'EXP_OLD_001', '2026-01-01T00:00:00', 'baseline', 'completed'
            );
        """)
        old_version = conn.execute(
            "SELECT version FROM schema_version WHERE component = 'research_registry'"
        ).fetchone()[0]
        conn.close()
        return old_version

    def test_migration_adds_scope_approvals_table(self):
        old_version = self._create_old_schema_db()
        self.assertEqual(old_version, 5)

        registry.init_db(self.db_path)

        conn = registry.connect(self.db_path)
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()

        self.assertIn("scope_approvals", tables)

    def test_migration_adds_scope_approval_id_column(self):
        self._create_old_schema_db()
        registry.init_db(self.db_path)

        conn = registry.connect(self.db_path)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(approval_usage_records)").fetchall()}
        conn.close()

        self.assertIn("scope_approval_id", columns)

    def test_old_records_readable_after_migration(self):
        self._create_old_schema_db()
        registry.init_db(self.db_path)

        impl = registry.get_implementation(self.db_path, "IMPL_OLD_001")
        self.assertIsNotNone(impl)
        self.assertEqual(impl["strategy_id"], "OLD_TEST")

        usage = registry.get_approval_usage(self.db_path, "USAGE_OLD_001")
        self.assertIsNotNone(usage)
        self.assertEqual(usage["runner_mode"], "baseline")

    def test_init_db_idempotent(self):
        self._create_old_schema_db()
        registry.init_db(self.db_path)
        registry.init_db(self.db_path)
        registry.init_db(self.db_path)

        conn = registry.connect(self.db_path)
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(approval_usage_records)").fetchall()}
        conn.close()

        self.assertIn("scope_approvals", tables)
        self.assertIn("scope_approval_id", columns)


class TestMissingArtifactAtQueueTime(unittest.TestCase):
    """TG-12: missing artifact file at queue execution time fails closed."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.ds_id = _register_test_dataset(self.db_path)
        self.strategy_id = "P15_MISS_ART"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"

    def tearDown(self):
        self.temp_dir.cleanup()
        _cleanup_path(self.sandbox)
        _cleanup_path(GENERATED_SPECS_DIR / f"{self.strategy_id}.yaml")
        _cleanup_path(REPO_ROOT / "automated" / "research_runs" / f"EXP_BL_{self.strategy_id}_TEST")

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
            self.db_path, impl_request_id,
            decision_packet_path=dp_path, approved_by="test",
        )
        return approval, impl_request_id, impl_id, bl_exp_id, sweep_id, dp_path, fh_result

    def test_generated_mq5_outside_sandbox_fails_closed(self):
        approval_result, _, impl_id, _, _, dp_path, _ = self._full_setup()

        outside_path = Path(self.temp_dir.name) / "outside_sandbox.mq5"
        outside_path.write_text("// test\n", encoding="utf-8")
        registry.update_implementation(
            self.db_path, impl_id,
            generated_mq5_path=str(outside_path),
        )

        eligibility = generated_final_holdout.require_generated_final_holdout_eligibility(
            self.db_path, self.strategy_id, "v1",
            decision_packet_path=dp_path, allow_mock_compile=True,
        )
        self.assertFalse(eligibility["eligible"])
        errors_str = "; ".join(eligibility["errors"])
        self.assertIn("outside sandbox", errors_str)

    def test_missing_diff_review_artifact_fails_closed(self):
        approval_result, impl_request_id, impl_id, _, _, dp_path, _ = self._full_setup()

        impl_req_path = impl_mod.IMPL_REQUESTS_DIR / impl_request_id
        review_path = impl_req_path / "diff_review.yaml"

        if not review_path.is_file():
            impl_mod.run_diff_review(self.db_path, impl_request_id)
        self.assertTrue(review_path.is_file(), f"diff_review not at {review_path}")

        review_path.unlink()
        self.assertFalse(review_path.is_file())

        eligibility = generated_final_holdout.require_generated_final_holdout_eligibility(
            self.db_path, self.strategy_id, "v1",
            decision_packet_path=dp_path, allow_mock_compile=True,
        )
        self.assertFalse(eligibility["eligible"])
        errors_str = "; ".join(eligibility["errors"])
        self.assertIn("Diff review artifact not found", errors_str)

    def test_edited_decision_packet_digest_mismatch_rejected(self):
        _, impl_request_id, _, _, _, dp_path, _ = self._full_setup()
        dp_obj = Path(dp_path)
        dp_obj.write_text(dp_obj.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")

        eligibility = generated_final_holdout.require_generated_final_holdout_eligibility(
            self.db_path, self.strategy_id, "v1",
            decision_packet_path=dp_path, allow_mock_compile=True,
        )
        self.assertFalse(eligibility["eligible"])
        errors_str = "; ".join(eligibility["errors"])
        self.assertIn("digest mismatch", errors_str)


class TestApprovalScopeConsistency(unittest.TestCase):
    """TG-13: baseline and final-holdout approval remain independent and consistent."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.ds_id = _register_test_dataset(self.db_path)
        self.strategy_id = "P15_SCOPE_CONS"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / f"{self.strategy_id}.mq5"

    def tearDown(self):
        self.temp_dir.cleanup()
        _cleanup_path(self.sandbox)
        _cleanup_path(GENERATED_SPECS_DIR / f"{self.strategy_id}.yaml")
        _cleanup_path(REPO_ROOT / "automated" / "research_runs" / f"EXP_BL_{self.strategy_id}_TEST")

    def _full_setup(self):
        approval, sandbox = _full_approval(self.db_path, self.strategy_id)
        impl_request_id = approval["implementation_request_id"]
        impl_id = approval["implementation_id"]
        bl_exp_id = _create_baseline_with_review(
            self.db_path, self.strategy_id, impl_request_id, impl_id, sandbox, self.mq5_path,
        )
        sweep_id = _create_sweep_with_robustness_review(self.db_path, bl_exp_id, self.strategy_id)
        return approval, impl_request_id, impl_id, bl_exp_id, sweep_id

    def test_baseline_approval_sets_implementations_scope(self):
        approval, _, _, _, _ = self._full_setup()
        impl = registry.get_implementation(self.db_path, approval["implementation_id"])
        self.assertEqual(impl["approval_scope"], "baseline_only")

    def test_final_holdout_approval_creates_separate_record(self):
        approval, impl_request_id, impl_id, bl_exp_id, sweep_id = self._full_setup()
        dp_path = _create_decision_packet(self.strategy_id)

        fh_result = generated_final_holdout.approve_for_final_holdout(
            self.db_path, impl_request_id,
            decision_packet_path=dp_path, approved_by="test",
        )
        self.assertTrue(fh_result["approved"])

        impl = registry.get_implementation(self.db_path, impl_id)
        self.assertEqual(impl["approval_scope"], "baseline_only")

        scopes = registry.list_scope_approvals(self.db_path, impl_id)
        fh_scopes = [s for s in scopes if s["approval_scope"] == "final_holdout_only"]
        self.assertEqual(len(fh_scopes), 1)
        self.assertEqual(fh_scopes[0]["approval_scope"], "final_holdout_only")

    def test_final_holdout_approval_does_not_modify_implementations_scope(self):
        approval, impl_request_id, impl_id, bl_exp_id, sweep_id = self._full_setup()
        original_scope = registry.get_implementation(self.db_path, impl_id)["approval_scope"]

        dp_path = _create_decision_packet(self.strategy_id)
        fh_result = generated_final_holdout.approve_for_final_holdout(
            self.db_path, impl_request_id,
            decision_packet_path=dp_path, approved_by="test",
        )
        self.assertTrue(fh_result["approved"])

        impl = registry.get_implementation(self.db_path, impl_id)
        self.assertEqual(impl["approval_scope"], original_scope)

    def test_final_holdout_approval_cannot_satisfy_baseline_checks(self):
        approval, impl_request_id, impl_id, bl_exp_id, sweep_id = self._full_setup()

        guard = impl_mod.require_generated_baseline_approval(
            self.db_path, self.strategy_id, "v1",
            allow_mock_compile=True, check_scope=True, check_consumed=False,
        )
        self.assertTrue(guard["approved"])

        dp_path = _create_decision_packet(self.strategy_id)
        fh_result = generated_final_holdout.approve_for_final_holdout(
            self.db_path, impl_request_id,
            decision_packet_path=dp_path, approved_by="test",
        )
        self.assertTrue(fh_result["approved"])

        guard_after = impl_mod.require_generated_baseline_approval(
            self.db_path, self.strategy_id, "v1",
            allow_mock_compile=True, check_scope=True, check_consumed=False,
        )
        self.assertTrue(guard_after["approved"])

    def test_baseline_only_approval_cannot_satisfy_final_holdout(self):
        approval, impl_request_id, impl_id, bl_exp_id, sweep_id = self._full_setup()

        eligibility = generated_final_holdout.require_generated_final_holdout_eligibility(
            self.db_path, self.strategy_id, "v1",
            allow_mock_compile=True,
        )
        self.assertFalse(eligibility["eligible"])
        errors_str = "; ".join(eligibility["errors"])
        self.assertIn("approval", errors_str)


class TestQueueReviewTasksNoSideEffects(unittest.TestCase):
    """Queue review/decision tasks must not create experiments or sweeps."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.strategy_id = "P15_REV_NOSIDE"
        self.sandbox = SANDBOX_ROOT / self.strategy_id / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.runner_script = REPO_ROOT / "automated" / "scripts" / "run_backtest.sh"

    def tearDown(self):
        self.temp_dir.cleanup()
        _cleanup_path(self.sandbox)
        _cleanup_path(GENERATED_SPECS_DIR / f"{self.strategy_id}.yaml")

    def _min_item(self, task_type, **overrides):
        item = {
            "queue_id": f"Q_REV_{task_type}",
            "priority": 1,
            "task_type": task_type,
            "strategy_id": self.strategy_id,
            "strategy_version": "v1",
            "hypothesis_id": "HYP_TEST_001",
            "requested_by": "test",
            "created_at": registry.utc_now(),
            "allowed_agent_roles": [],
            "budget": {
                "max_experiments": 1, "max_child_experiments": 0,
                "max_runtime_minutes": 10, "max_parameters_changed_per_child": 0,
                "max_sweeps": 0, "max_failed_runs": 0,
                "max_disk_usage_mb": 512, "require_one_variable_at_a_time": False,
            },
            "permissions": {
                "allow_runner_execution": False,
                "allow_mql5_edits": False,
                "allow_dataset_changes": False,
                "allow_validation_threshold_changes": False,
                "allow_lifecycle_apply": False,
                "allow_lifecycle_propose": False,
                "allow_final_holdout": False,
            },
        }
        item.update(overrides)
        return item

    def _run_item(self, item):
        run_dir = Path(self.temp_dir.name) / f"run_{item['queue_id']}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return queue._execute_item(
            self.db_path, item, run_dir,
            runner_script=self.runner_script,
            research_output_root=Path(self.temp_dir.name) / "research_runs",
        )

    def _assert_no_side_effects(self, result, task_type):
        self.assertEqual(
            result["experiments_created"], [],
            f"{task_type} must not create experiments",
        )
        self.assertEqual(
            result["sweeps_created"], [],
            f"{task_type} must not create sweeps",
        )
        fh_prefixes = [e for e in result["experiments_created"] if e.startswith("FH_")]
        self.assertEqual(
            fh_prefixes, [],
            f"{task_type} must not create FH experiments",
        )

    def test_generated_baseline_review_no_side_effects(self):
        item = self._min_item(
            "generated_baseline_review",
            parent_experiment_id="EXP_NONEXISTENT_REVIEW",
        )
        result = self._run_item(item)
        self._assert_no_side_effects(result, "generated_baseline_review")

    def test_generated_robustness_review_no_side_effects(self):
        item = self._min_item(
            "generated_robustness_review",
            parent_experiment_id="SWEEP_NONEXISTENT",
        )
        result = self._run_item(item)
        self._assert_no_side_effects(result, "generated_robustness_review")

    def test_generated_final_holdout_review_no_side_effects(self):
        item = self._min_item(
            "generated_final_holdout_review",
            parent_experiment_id="FH_EXP_NONEXISTENT",
        )
        result = self._run_item(item)
        self._assert_no_side_effects(result, "generated_final_holdout_review")

    def test_generated_candidate_decision_packet_no_side_effects(self):
        item = self._min_item(
            "generated_candidate_decision_packet",
        )
        result = self._run_item(item)
        self._assert_no_side_effects(result, "generated_candidate_decision_packet")


if __name__ == "__main__":
    unittest.main()
