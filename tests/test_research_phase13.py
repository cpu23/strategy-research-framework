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
    generated_candidate,
    implementation as impl_mod,
    queue,
    registry,
)
from automated.research.schemas import (
    GENERATED_SPECS_DIR,
    REPO_ROOT,
    SANDBOX_ROOT,
    STRATEGIES_ROOT,
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


def _create_baseline_with_review(
    db_path: str | Path,
    strategy_id: str,
    impl_request_id: str,
    impl_id: str,
    sandbox: Path,
    mq5_path: Path,
    *,
    skip_review: bool = False,
    baseline_status: str = "completed",
    extra_metrics: dict | None = None,
) -> str:
    _write_sample_generated_spec(strategy_id, mq5_path)
    experiment_id = f"EXP_BL_{strategy_id}_{registry.utc_now().replace(':', '')[:16]}"

    bars_file = Path(db_path).parent / "test_bars.csv"
    bars_file.write_text("time\topen\thigh\tlow\tclose\ttick_volume\n2024-01-01\t100\t110\t90\t105\t1000\n", encoding="utf-8")

    from automated.research.datasets import register_dataset
    ds = register_dataset(db_path, bars_path=bars_file, symbol="XAUUSD", timeframe="H4")
    ds_id = ds["dataset_id"]

    metrics = {
        "net_return": 500.0,
        "profit_factor": 1.8,
        "trade_count": 25,
        "max_drawdown": 12.0,
    }
    if extra_metrics:
        metrics.update(extra_metrics)

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
        "status": baseline_status,
        "headline_metrics_json": json.dumps(metrics),
    }
    registry.create_experiment(db_path, exp_payload)

    usage_id = f"USAGE_BL_{strategy_id}_{registry.utc_now().replace(':', '')[:8]}"
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

    if not skip_review:
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
            "red_team_results": {
                "warnings": [],
                "risk_flags": [],
                "overall_assessment": "low_risk",
            },
        }
        review_path.write_text(yaml.safe_dump(review_packet, sort_keys=False), encoding="utf-8")
        registry.attach_artifact(db_path, experiment_id, "generated_baseline_review", review_path)

    return experiment_id


def _create_sweep_with_robustness_review(
    db_path: str | Path,
    baseline_experiment_id: str,
    strategy_id: str,
    *,
    skip_review: bool = False,
    extra_red_team: dict | None = None,
) -> str:
    sweep_id = f"SWEEP_GR_{strategy_id}_{registry.utc_now().replace(':', '')[:8]}"
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
        child_id = f"CHILD_P13_{strategy_id}_{i:03d}"
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
            "status": "completed",
            "headline_metrics_json": json.dumps({
                "net_return": nr,
                "profit_factor": pf,
                "trade_count": tc,
            }),
        }
        registry.create_experiment(db_path, child_payload)
        registry.add_sweep_child(
            db_path,
            {
                "sweep_id": sweep_id,
                "child_experiment_id": child_id,
                "child_index": i,
                "child_role": f"param={i}",
                "parameter_diff": json.dumps({"InpAtrPeriod": {"from": 14, "to": 14 + i}}),
                "status": status,
            },
        )

    if not skip_review:
        red_team_assessment = extra_red_team or {
            "warnings": [],
            "risk_flags": [],
            "overall_assessment": "low_risk",
        }
        review_path = Path(db_path).parent / f"generated_robustness_review_{sweep_id}.yaml"
        review_packet = {
            "schema_version": "generated_robustness_review_v1",
            "strategy_id": strategy_id,
            "strategy_version": "v1",
            "sweep_id": sweep_id,
            "baseline_experiment_id": baseline_experiment_id,
            "red_team_assessment": red_team_assessment,
            "robustness_warnings": [],
            "recommendation": "consider_lifecycle_candidate",
            "children_completed": 3,
            "children_failed": 1,
            "median_profit_factor": 1.35,
            "median_net_return": 75.0,
        }
        review_path.write_text(yaml.safe_dump(review_packet, sort_keys=False), encoding="utf-8")
        registry.attach_artifact(db_path, baseline_experiment_id, "generated_robustness_review", review_path)

    return sweep_id


class ResearchPhase13EligibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.sid = "p13_elig"
        self.sandbox = SANDBOX_ROOT / self.sid / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / f"{self.sid}.mq5"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        spec_p = GENERATED_SPECS_DIR / f"{self.sid}.yaml"
        if spec_p.is_file():
            spec_p.unlink()
        import shutil
        if self.sandbox.is_dir():
            shutil.rmtree(self.sandbox)

    def _full_chain_setup(self, *, skip_baseline_review=False, skip_robustness_review=False, extra_robustness_red_team=None) -> tuple[str, str, str]:
        _write_sample_mq5(self.mq5_path)
        approval = _full_approval(self.db_path, self.sid, "v1", self.sandbox)
        impl_req_id = approval["implementation_request_id"]
        impl_id = approval["implementation_id"]
        bl_id = _create_baseline_with_review(
            self.db_path, self.sid, impl_req_id, impl_id,
            self.sandbox, self.mq5_path,
            skip_review=skip_baseline_review,
        )
        sw_id = _create_sweep_with_robustness_review(
            self.db_path, bl_id, self.sid,
            skip_review=skip_robustness_review,
            extra_red_team=extra_robustness_red_team,
        )
        return impl_req_id, bl_id, sw_id

    def test_blocked_before_baseline_review(self) -> None:
        _write_sample_mq5(self.mq5_path)
        approval = _full_approval(self.db_path, self.sid, "v1", self.sandbox)
        bl_id = _create_baseline_with_review(
            self.db_path, self.sid, approval["implementation_request_id"],
            approval["implementation_id"], self.sandbox, self.mq5_path,
            skip_review=True,
        )
        result = generated_candidate.require_generated_candidate_decision_eligibility(
            self.db_path, self.sid, "v1",
            baseline_experiment_id=bl_id,
        )
        self.assertFalse(result["eligible"])
        self.assertTrue(
            any("generated_baseline_review" in e for e in result["errors"])
        )

    def test_blocked_before_robustness_review(self) -> None:
        _write_sample_mq5(self.mq5_path)
        approval = _full_approval(self.db_path, self.sid, "v1", self.sandbox)
        bl_id = _create_baseline_with_review(
            self.db_path, self.sid, approval["implementation_request_id"],
            approval["implementation_id"], self.sandbox, self.mq5_path,
        )
        sw_id = _create_sweep_with_robustness_review(
            self.db_path, bl_id, self.sid, skip_review=True,
        )
        result = generated_candidate.require_generated_candidate_decision_eligibility(
            self.db_path, self.sid, "v1",
            baseline_experiment_id=bl_id,
            robustness_sweep_id=sw_id,
        )
        self.assertFalse(result["eligible"])
        self.assertTrue(
            any("generated_robustness_review" in e for e in result["errors"])
        )

    def test_blocked_if_final_holdout_ran(self) -> None:
        _write_sample_mq5(self.mq5_path)
        approval = _full_approval(self.db_path, self.sid, "v1", self.sandbox)
        bl_id = _create_baseline_with_review(
            self.db_path, self.sid, approval["implementation_request_id"],
            approval["implementation_id"], self.sandbox, self.mq5_path,
        )
        sw_id = _create_sweep_with_robustness_review(
            self.db_path, bl_id, self.sid,
        )
        bars_file = Path(self.db_path).parent / "fh_bars.csv"
        bars_file.write_text("time\topen\thigh\tlow\tclose\ttick_volume\n2024-01-01\t100\t110\t90\t105\t1000\n", encoding="utf-8")
        from automated.research.datasets import register_dataset
        ds = register_dataset(self.db_path, bars_path=bars_file, symbol="XAUUSD", timeframe="H4")
        fh_payload = {
            "experiment_id": "EXP_FH_test",
            "hypothesis_id": "HYP_GEN_FBR_RANGING_000",
            "strategy_id": self.sid,
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
            "change_summary": "final holdout experiment",
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
        registry.create_experiment(self.db_path, fh_payload)

        result = generated_candidate.require_generated_candidate_decision_eligibility(
            self.db_path, self.sid, "v1",
            baseline_experiment_id=bl_id,
            robustness_sweep_id=sw_id,
        )
        self.assertTrue(result["eligible"])
        self.assertTrue(
            any("final holdout" in str(w).lower() for w in result.get("warnings", []))
        )

    def test_blocked_if_lifecycle_applied(self) -> None:
        _write_sample_mq5(self.mq5_path)
        approval = _full_approval(self.db_path, self.sid, "v1", self.sandbox)
        bl_id = _create_baseline_with_review(
            self.db_path, self.sid, approval["implementation_request_id"],
            approval["implementation_id"], self.sandbox, self.mq5_path,
        )
        sw_id = _create_sweep_with_robustness_review(
            self.db_path, bl_id, self.sid,
        )

        registry.create_lifecycle_transition(
            self.db_path,
            {
                "transition_id": "TRANSITION_P13_APPLIED",
                "strategy_id": self.sid,
                "strategy_spec_path": str(Path(self.temp_dir.name) / "dummy.yaml"),
                "from_state": "baseline_testing",
                "to_state": "robustness_testing",
                "experiment_id": bl_id,
                "requested_by": "test",
                "approved_by": "test",
                "reason": "test",
                "gate_snapshot_path": str(Path(self.temp_dir.name) / "snap.json"),
                "created_at": registry.utc_now(),
                "status": "applied",
                "notes": "",
                "override": False,
            },
        )

        result = generated_candidate.require_generated_candidate_decision_eligibility(
            self.db_path, self.sid, "v1",
            baseline_experiment_id=bl_id,
            robustness_sweep_id=sw_id,
        )
        self.assertFalse(result["eligible"])
        self.assertTrue(
            any("Lifecycle transition already applied" in e for e in result["errors"])
        )

    def test_allowed_when_all_conditions_met(self) -> None:
        _write_sample_mq5(self.mq5_path)
        approval = _full_approval(self.db_path, self.sid, "v1", self.sandbox)
        bl_id = _create_baseline_with_review(
            self.db_path, self.sid, approval["implementation_request_id"],
            approval["implementation_id"], self.sandbox, self.mq5_path,
        )
        sw_id = _create_sweep_with_robustness_review(
            self.db_path, bl_id, self.sid,
        )
        result = generated_candidate.require_generated_candidate_decision_eligibility(
            self.db_path, self.sid, "v1",
            baseline_experiment_id=bl_id,
            robustness_sweep_id=sw_id,
        )
        self.assertTrue(result["eligible"])
        self.assertEqual(len(result["errors"]), 0)

    def test_unrelated_production_strategies_do_not_block(self) -> None:
        unrelated = STRATEGIES_ROOT / "unrelated_prod.mq5"
        unrelated.parent.mkdir(parents=True, exist_ok=True)
        unrelated.write_text("// unrelated\n", encoding="utf-8")
        try:
            _write_sample_mq5(self.mq5_path)
            approval = _full_approval(self.db_path, self.sid, "v1", self.sandbox)
            bl_id = _create_baseline_with_review(
                self.db_path, self.sid, approval["implementation_request_id"],
                approval["implementation_id"], self.sandbox, self.mq5_path,
            )
            sw_id = _create_sweep_with_robustness_review(
                self.db_path, bl_id, self.sid,
            )
            result = generated_candidate.require_generated_candidate_decision_eligibility(
                self.db_path, self.sid, "v1",
                baseline_experiment_id=bl_id,
                robustness_sweep_id=sw_id,
            )
            self.assertTrue(result["eligible"])
        finally:
            if unrelated.is_file():
                unrelated.unlink()


class ResearchPhase13DecisionPacketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.sid = "p13_packet"
        self.sandbox = SANDBOX_ROOT / self.sid / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / f"{self.sid}.mq5"
        _write_sample_mq5(self.mq5_path)
        self.approval = _full_approval(self.db_path, self.sid, "v1", self.sandbox)
        self.bl_id = _create_baseline_with_review(
            self.db_path, self.sid, self.approval["implementation_request_id"],
            self.approval["implementation_id"], self.sandbox, self.mq5_path,
        )
        self.sw_id = _create_sweep_with_robustness_review(
            self.db_path, self.bl_id, self.sid,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        spec_p = GENERATED_SPECS_DIR / f"{self.sid}.yaml"
        if spec_p.is_file():
            spec_p.unlink()
        import shutil
        if self.sandbox.is_dir():
            shutil.rmtree(self.sandbox)

    def test_packet_created_after_reviews_exist(self) -> None:
        output_dir = Path(self.temp_dir.name) / "packet_output"
        result = generated_candidate.build_generated_candidate_decision_packet(
            self.db_path,
            strategy_id=self.sid,
            strategy_version="v1",
            implementation_request_id=self.approval["implementation_request_id"],
            baseline_experiment_id=self.bl_id,
            robustness_sweep_id=self.sw_id,
            output_dir=output_dir,
        )
        self.assertIn("packet_path", result)
        packet_path = Path(result["packet_path"])
        self.assertTrue(packet_path.is_file())

    def test_packet_contains_all_required_sections(self) -> None:
        output_dir = Path(self.temp_dir.name) / "packet_output2"
        result = generated_candidate.build_generated_candidate_decision_packet(
            self.db_path,
            strategy_id=self.sid,
            strategy_version="v1",
            implementation_request_id=self.approval["implementation_request_id"],
            baseline_experiment_id=self.bl_id,
            robustness_sweep_id=self.sw_id,
            output_dir=output_dir,
        )
        packet = yaml.safe_load(Path(result["packet_path"]).read_text(encoding="utf-8"))
        required_fields = [
            "schema_version", "strategy_id", "strategy_version",
            "hypothesis_summary", "spec_summary", "implementation_summary",
            "baseline_summary", "robustness_summary", "validation_summary",
            "red_team_summary", "unresolved_warnings", "evidence_gaps",
            "artifact_paths", "candidate_status", "proposed_next_action",
            "lifecycle_proposal", "decision_rules_applied",
        ]
        for field in required_fields:
            self.assertIn(field, packet, f"Missing required field: {field}")

    def test_decision_rules_applied_present(self) -> None:
        output_dir = Path(self.temp_dir.name) / "packet_output3"
        result = generated_candidate.build_generated_candidate_decision_packet(
            self.db_path,
            strategy_id=self.sid,
            strategy_version="v1",
            implementation_request_id=self.approval["implementation_request_id"],
            baseline_experiment_id=self.bl_id,
            robustness_sweep_id=self.sw_id,
            output_dir=output_dir,
        )
        packet = yaml.safe_load(Path(result["packet_path"]).read_text(encoding="utf-8"))
        rules = packet["decision_rules_applied"]
        self.assertIsInstance(rules, list)
        self.assertGreater(len(rules), 0)
        self.assertTrue(any("robustness" in r.lower() for r in rules))

    def test_proposed_next_action_from_allowed_set(self) -> None:
        output_dir = Path(self.temp_dir.name) / "packet_output4"
        result = generated_candidate.build_generated_candidate_decision_packet(
            self.db_path,
            strategy_id=self.sid,
            strategy_version="v1",
            implementation_request_id=self.approval["implementation_request_id"],
            baseline_experiment_id=self.bl_id,
            robustness_sweep_id=self.sw_id,
            output_dir=output_dir,
        )
        packet = yaml.safe_load(Path(result["packet_path"]).read_text(encoding="utf-8"))
        self.assertIn(
            packet["proposed_next_action"],
            generated_candidate.ALLOWED_PROPOSED_NEXT_ACTIONS,
        )

    def test_lifecycle_proposal_from_allowed_set(self) -> None:
        output_dir = Path(self.temp_dir.name) / "packet_output5"
        result = generated_candidate.build_generated_candidate_decision_packet(
            self.db_path,
            strategy_id=self.sid,
            strategy_version="v1",
            implementation_request_id=self.approval["implementation_request_id"],
            baseline_experiment_id=self.bl_id,
            robustness_sweep_id=self.sw_id,
            output_dir=output_dir,
        )
        packet = yaml.safe_load(Path(result["packet_path"]).read_text(encoding="utf-8"))
        self.assertIn(
            packet["lifecycle_proposal"],
            generated_candidate.ALLOWED_LIFECYCLE_PROPOSALS,
        )

    def test_packet_never_contains_promote_to_production(self) -> None:
        output_dir = Path(self.temp_dir.name) / "packet_output6"
        result = generated_candidate.build_generated_candidate_decision_packet(
            self.db_path,
            strategy_id=self.sid,
            strategy_version="v1",
            implementation_request_id=self.approval["implementation_request_id"],
            baseline_experiment_id=self.bl_id,
            robustness_sweep_id=self.sw_id,
            output_dir=output_dir,
        )
        packet = yaml.safe_load(Path(result["packet_path"]).read_text(encoding="utf-8"))
        packet_str = yaml.safe_dump(packet)
        self.assertNotIn("promote_to_production", packet_str)

    def test_packet_never_contains_live_trading_candidate(self) -> None:
        output_dir = Path(self.temp_dir.name) / "packet_output7"
        result = generated_candidate.build_generated_candidate_decision_packet(
            self.db_path,
            strategy_id=self.sid,
            strategy_version="v1",
            implementation_request_id=self.approval["implementation_request_id"],
            baseline_experiment_id=self.bl_id,
            robustness_sweep_id=self.sw_id,
            output_dir=output_dir,
        )
        packet = yaml.safe_load(Path(result["packet_path"]).read_text(encoding="utf-8"))
        packet_str = yaml.safe_dump(packet)
        self.assertNotIn("live_trading_candidate", packet_str)

    def test_packet_never_contains_production_candidate(self) -> None:
        output_dir = Path(self.temp_dir.name) / "packet_output8"
        result = generated_candidate.build_generated_candidate_decision_packet(
            self.db_path,
            strategy_id=self.sid,
            strategy_version="v1",
            implementation_request_id=self.approval["implementation_request_id"],
            baseline_experiment_id=self.bl_id,
            robustness_sweep_id=self.sw_id,
            output_dir=output_dir,
        )
        packet = yaml.safe_load(Path(result["packet_path"]).read_text(encoding="utf-8"))
        packet_str = yaml.safe_dump(packet)
        self.assertNotIn("production_candidate", packet_str)

    def test_packet_proposes_next_action_without_applying_it(self) -> None:
        output_dir = Path(self.temp_dir.name) / "packet_output9"
        result = generated_candidate.build_generated_candidate_decision_packet(
            self.db_path,
            strategy_id=self.sid,
            strategy_version="v1",
            implementation_request_id=self.approval["implementation_request_id"],
            baseline_experiment_id=self.bl_id,
            robustness_sweep_id=self.sw_id,
            output_dir=output_dir,
        )
        packet = yaml.safe_load(Path(result["packet_path"]).read_text(encoding="utf-8"))
        self.assertNotEqual(packet["proposed_next_action"], "")
        self.assertNotEqual(packet["lifecycle_proposal"], "")

    def test_packet_production_file_conflict_is_warning_not_block(self) -> None:
        prod_file = STRATEGIES_ROOT / f"{self.sid}.mq5"
        prod_file.parent.mkdir(parents=True, exist_ok=True)
        prod_file.write_text("// conflict test\n", encoding="utf-8")
        try:
            output_dir = Path(self.temp_dir.name) / "packet_output10"
            result = generated_candidate.build_generated_candidate_decision_packet(
                self.db_path,
                strategy_id=self.sid,
                strategy_version="v1",
                implementation_request_id=self.approval["implementation_request_id"],
                baseline_experiment_id=self.bl_id,
                robustness_sweep_id=self.sw_id,
                output_dir=output_dir,
            )
            packet = yaml.safe_load(Path(result["packet_path"]).read_text(encoding="utf-8"))
            warnings = packet.get("unresolved_warnings", [])
            self.assertTrue(
                any("Production strategy file" in w for w in warnings),
                f"Expected production file warning, got: {warnings}",
            )
        finally:
            if prod_file.is_file():
                prod_file.unlink()

    def test_packet_has_candidate_status_eligible(self) -> None:
        output_dir = Path(self.temp_dir.name) / "packet_output11"
        result = generated_candidate.build_generated_candidate_decision_packet(
            self.db_path,
            strategy_id=self.sid,
            strategy_version="v1",
            implementation_request_id=self.approval["implementation_request_id"],
            baseline_experiment_id=self.bl_id,
            robustness_sweep_id=self.sw_id,
            output_dir=output_dir,
        )
        packet = yaml.safe_load(Path(result["packet_path"]).read_text(encoding="utf-8"))
        self.assertEqual(packet["candidate_status"], "eligible")


class ResearchPhase13QueueIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)
        self.sid = "p13_queue"
        self.sandbox = SANDBOX_ROOT / self.sid / "v1"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.mq5_path = self.sandbox / f"{self.sid}.mq5"
        _write_sample_mq5(self.mq5_path)
        self.approval = _full_approval(self.db_path, self.sid, "v1", self.sandbox)
        self.bl_id = _create_baseline_with_review(
            self.db_path, self.sid, self.approval["implementation_request_id"],
            self.approval["implementation_id"], self.sandbox, self.mq5_path,
        )
        self.sw_id = _create_sweep_with_robustness_review(
            self.db_path, self.bl_id, self.sid,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        spec_p = GENERATED_SPECS_DIR / f"{self.sid}.yaml"
        if spec_p.is_file():
            spec_p.unlink()
        import shutil
        if self.sandbox.is_dir():
            shutil.rmtree(self.sandbox)

    def test_queue_task_creates_packet_only(self) -> None:
        self.assertIn("generated_candidate_decision_packet", queue.TASK_TYPES)

    def test_queue_validation_requires_fields(self) -> None:
        queue_path = Path(self.temp_dir.name) / "gc_queue.yaml"
        queue_data = {
            "queue_id": "gc_queue_test",
            "items": [{
                "queue_id": "gc_item_001",
                "priority": 10,
                "task_type": "generated_candidate_decision_packet",
                "strategy_id": self.sid,
                "strategy_version": "v1",
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
        self.assertEqual(result["items"][0]["task_type"], "generated_candidate_decision_packet")

    def test_blocked_if_runner_execution_true(self) -> None:
        queue_path = Path(self.temp_dir.name) / "gc_block_re.yaml"
        queue_data = {
            "queue_id": "gc_block_re",
            "items": [{
                "queue_id": "gc_re_001",
                "priority": 10,
                "task_type": "generated_candidate_decision_packet",
                "strategy_id": self.sid,
                "strategy_version": "v1",
                "requested_by": "test",
                "created_at": "2026-05-12T00:00:00+00:00",
                "allowed_agent_roles": ["statistical_reviewer"],
                "budget": {"max_experiments": 0, "max_runtime_minutes": 10},
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

    def test_blocked_if_lifecycle_apply_true(self) -> None:
        queue_path = Path(self.temp_dir.name) / "gc_block_la.yaml"
        queue_data = {
            "queue_id": "gc_block_la",
            "items": [{
                "queue_id": "gc_la_001",
                "priority": 10,
                "task_type": "generated_candidate_decision_packet",
                "strategy_id": self.sid,
                "strategy_version": "v1",
                "requested_by": "test",
                "created_at": "2026-05-12T00:00:00+00:00",
                "allowed_agent_roles": ["statistical_reviewer"],
                "budget": {"max_experiments": 0, "max_runtime_minutes": 10},
                "permissions": {
                    "allow_runner_execution": False,
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
        queue_path = Path(self.temp_dir.name) / "gc_block_fh.yaml"
        queue_data = {
            "queue_id": "gc_block_fh",
            "items": [{
                "queue_id": "gc_fh_001",
                "priority": 10,
                "task_type": "generated_candidate_decision_packet",
                "strategy_id": self.sid,
                "strategy_version": "v1",
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
                    "allow_final_holdout": True,
                },
                "status": "queued",
            }],
        }
        queue_path.write_text(yaml.safe_dump(queue_data, sort_keys=False), encoding="utf-8")
        with self.assertRaises(SchemaValidationError):
            queue.validate_queue(self.db_path, queue_path)


class ResearchPhase13CLISmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.sqlite"
        registry.init_db(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cli_help_includes_generated_candidate(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            try:
                cli.main(["--db", str(self.db_path), "--help"])
            except SystemExit:
                pass
        output = stdout.getvalue()
        self.assertIn("generated-candidate", output)

    def test_cli_decision_packet_help(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            try:
                cli.main(["--db", str(self.db_path), "generated-candidate", "decision-packet", "--help"])
            except SystemExit:
                pass
        output = stdout.getvalue()
        self.assertIn("strategy-id", output)
        self.assertIn("strategy-version", output)

    def test_cli_decision_packet_smoke(self) -> None:
        sid = "p13_cli_smoke"
        sandbox = SANDBOX_ROOT / sid / "v1"
        sandbox.mkdir(parents=True, exist_ok=True)
        mq5_path = sandbox / f"{sid}.mq5"
        _write_sample_mq5(mq5_path)
        approval = _full_approval(self.db_path, sid, "v1", sandbox)
        bl_id = _create_baseline_with_review(
            self.db_path, sid, approval["implementation_request_id"],
            approval["implementation_id"], sandbox, mq5_path,
        )
        sw_id = _create_sweep_with_robustness_review(
            self.db_path, bl_id, sid,
        )

        stdout = StringIO()
        with redirect_stdout(stdout):
            rc = cli.main([
                "--db", str(self.db_path),
                "generated-candidate", "decision-packet",
                "--strategy-id", sid,
                "--strategy-version", "v1",
                "--baseline-experiment-id", bl_id,
                "--robustness-sweep-id", sw_id,
                "--output", str(Path(self.temp_dir.name) / "cli_output"),
            ])
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertIn("packet_path", data)
        self.assertIn("proposed_next_action", data)
        self.assertIn("lifecycle_proposal", data)
        self.assertIn(data["proposed_next_action"], generated_candidate.ALLOWED_PROPOSED_NEXT_ACTIONS)
        self.assertIn(data["lifecycle_proposal"], generated_candidate.ALLOWED_LIFECYCLE_PROPOSALS)

        import shutil
        if sandbox.is_dir():
            shutil.rmtree(sandbox)
        spec_p = GENERATED_SPECS_DIR / f"{sid}.yaml"
        if spec_p.is_file():
            spec_p.unlink()


class ResearchPhase13ConstantsTests(unittest.TestCase):
    def test_artifact_type_registered(self) -> None:
        self.assertIn("generated_candidate_decision_packet", ARTIFACT_TYPES)

    def test_allowed_proposed_next_actions(self) -> None:
        allowed = generated_candidate.ALLOWED_PROPOSED_NEXT_ACTIONS
        self.assertIn("reject", allowed)
        self.assertIn("revise_strategy_spec", allowed)
        self.assertIn("revise_implementation", allowed)
        self.assertIn("run_additional_bounded_sweep", allowed)
        self.assertIn("request_human_review_for_final_holdout", allowed)
        self.assertIn("defer", allowed)
        self.assertNotIn("promote_to_production", allowed)
        self.assertEqual(len(allowed), 6)

    def test_allowed_lifecycle_proposals(self) -> None:
        allowed = generated_candidate.ALLOWED_LIFECYCLE_PROPOSALS
        self.assertIn("none", allowed)
        self.assertIn("research_candidate", allowed)
        self.assertIn("robustness_candidate", allowed)
        self.assertIn("final_holdout_candidate", allowed)
        self.assertNotIn("promote_to_production", allowed)
        self.assertNotIn("live_trading_candidate", allowed)
        self.assertNotIn("production_candidate", allowed)
        self.assertEqual(len(allowed), 4)

    def test_schema_version_defined(self) -> None:
        self.assertEqual(
            generated_candidate.GENERATED_CANDIDATE_DECISION_PACKET_SCHEMA,
            "generated_candidate_decision_packet_v1",
        )

    def test_has_final_holdout_helper_exists(self) -> None:
        self.assertTrue(callable(generated_candidate.has_final_holdout_run))


if __name__ == "__main__":
    unittest.main()
