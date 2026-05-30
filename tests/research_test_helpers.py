from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml

from automated.research import implementation as impl_mod, registry
from automated.research.datasets import register_dataset
from automated.research.schemas import GENERATED_SPECS_DIR, REPO_ROOT, SANDBOX_ROOT

FORBIDDEN_VALUES = {
    "promote_to_production",
    "production_candidate",
    "live_trading_candidate",
}

FORBIDDEN_AUTHORITY_FIELDS = {
    "approval_status",
    "approval_usage",
    "baseline_approval",
    "final_holdout_approval",
    "lifecycle_proposal",
}


def cleanup_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.is_file():
        path.unlink()


def write_yaml_temp(data: dict[str, Any], *, filename: str = "record.yaml") -> Path:
    import tempfile

    path = Path(tempfile.mkdtemp()) / filename
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def valid_research_source(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "source_id": "SRC_CASE_VOL_BREAKOUT_001",
        "source_type": "case_study",
        "title": "Volatility breakout case study",
        "authors": [],
        "published_date": None,
        "url_or_reference": "Internal research notebook, 2026-05-15",
        "summary": "Compression may precede expansion.",
        "markets_discussed": ["metals", "fx"],
        "timeframes_discussed": ["M30", "H1"],
        "key_claims": ["Low realized volatility can precede expansion."],
        "limitations": ["False breakouts remain common."],
        "extraction_status": "pending",
        "created_at": "2026-05-15T00:00:00Z",
    }
    data.update(overrides)
    return data


def valid_edge_thesis(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "edge_id": "EDGE_VOL_COMPRESSION_BREAKOUT_001",
        "source_ids": ["SRC_CASE_VOL_BREAKOUT_001"],
        "edge_family": "volatility_expansion",
        "mechanism": "Volatility compression may precede expansion when liquidity is coiled.",
        "testable_prediction": "Breakouts after low realized volatility should have positive forward skew.",
        "asset_classes": ["metals", "fx"],
        "candidate_symbols": ["XAUUSD", "EURUSD"],
        "candidate_timeframes": ["M15", "M30", "H1", "H4", "D1"],
        "market_regimes": ["range_compression"],
        "mutation_axes": ["compression_measure", "breakout_trigger", "exit_model"],
        "implementation_constraints": ["closed_bar_confirmation"],
        "failure_modes": ["false breakouts in mean-reverting regimes"],
        "risk_warnings": ["spread widening around session transitions"],
        "evidence_strength": "medium",
        "status": "active",
        "created_at": "2026-05-15T00:00:00Z",
    }
    data.update(overrides)
    return data


def valid_mutation_recipe(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "recipe_id": "MUT_VOL_BREAKOUT_001",
        "edge_id": "EDGE_VOL_COMPRESSION_BREAKOUT_001",
        "mutation_budget": {
            "max_hypotheses": 12,
            "max_per_similarity_cluster": 2,
            "min_family_diversity": 1,
        },
        "axes": {
            "compression_measure": {"allowed_values": ["atr_percentile", "bollinger_width"]},
            "breakout_trigger": {"allowed_values": ["close_break", "two_bar_confirm"]},
            "exit_model": {"allowed_values": ["fixed_r", "atr_trail"]},
            "stop_model": {"allowed_values": ["atr_multiple", "structure_swing"]},
        },
        "constraints": ["one_primary_entry_trigger", "one_primary_exit_model", "no_unbounded_grid_search"],
    }
    data.update(overrides)
    return data


def valid_campaign_config(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "campaign_id": "CAMP_VOL_EXPANSION_2026_05",
        "edge_ids": ["EDGE_VOL_COMPRESSION_BREAKOUT_001"],
        "hypothesis_batch_ids": ["HBATCH_PLACEHOLDER"],
        "asset_timeframe_grid": [
            {"symbol": "XAUUSD", "timeframes": ["M30", "H1", "H4"]},
            {"symbol": "eurusd", "timeframes": ["H1", "H4", "D1"]},
        ],
        "controls": {
            "dataset_policy": "frozen_registered_only",
            "cost_model_policy": "frozen_from_strategy_spec",
            "validation_threshold_policy": "frozen",
            "runner_policy": "frozen",
        },
        "budgets": {
            "max_total_planned_specs": 20,
            "max_per_edge": 20,
            "max_per_asset_timeframe": 5,
            "max_per_similarity_cluster_per_asset_timeframe": 2,
        },
        "similarity_policy": {
            "allow_similarity": True,
            "max_per_cluster_total": 6,
            "max_per_cluster_per_asset_timeframe": 2,
        },
        "outputs": {"ranked_manual_review_queue": True},
    }
    data.update(overrides)
    return data


def generated_hypothesis_batch() -> dict[str, Any]:
    from automated.research import hypothesis_mutation

    thesis = valid_edge_thesis(
        candidate_symbols=["XAUUSD", "EURUSD"],
        candidate_timeframes=["M30", "H1", "H4", "D1"],
    )
    recipe = valid_mutation_recipe(mutation_budget={"max_hypotheses": 8, "max_per_similarity_cluster": 4})
    batch = hypothesis_mutation.build_generated_hypothesis_batch(thesis, recipe)
    batch["batch_id"] = "HBATCH_PLACEHOLDER"
    return batch


def campaign_analysis_record(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "edge_family": "volatility_expansion",
        "edge_id": "EDGE_VOL_COMPRESSION_BREAKOUT_001",
        "source_id": "SRC_CASE_VOL_BREAKOUT_001",
        "hypothesis_id": "HYP_GEN_001",
        "similarity_cluster_id": "SIM_001",
        "symbol": "XAUUSD",
        "timeframe": "H1",
        "strategy_family": "volatility_expansion",
        "entry_logic_type": "breakout",
        "exit_logic_type": "fixed_r",
        "risk_model_type": "atr_stop",
        "validation_status": "pass",
        "robustness_status": "warn",
        "final_holdout_status": "",
        "trade_count": 40,
        "min_trades_required": 20,
    }
    data.update(overrides)
    return data


def agent_workspace_bundle(
    manifest_overrides: dict[str, Any] | None = None,
    artifact_data: dict[str, Any] | None = None,
    artifact_name: str = "artifact.yaml",
) -> Path:
    import tempfile

    root = Path(tempfile.mkdtemp()) / "bundle"
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True)
    data = artifact_data or valid_research_source()
    (artifacts / artifact_name).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    manifest: dict[str, Any] = {
        "schema_version": "agent_workspace_bundle_v1",
        "bundle_id": "AWB_TEST_001",
        "agent_role": "research_librarian",
        "allowed_recommendations": ["reject", "revise_thesis", "request_manual_review", "defer"],
        "artifacts": [
            {
                "artifact_type": "research_source_record",
                "path": f"artifacts/{artifact_name}",
                "target_name": "SRC_CASE_VOL_BREAKOUT_001.yaml",
            }
        ],
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return root


TEST_HYPOTHESIS_ID = "HYP_GEN_FBR_RANGING_000"

SAMPLE_EXPECTED_INPUTS = [
    {"name": "InpRiskPerTrade", "type": "double", "required": True, "default": "0.01"},
    {"name": "InpAtrPeriod", "type": "int", "required": True, "default": "14"},
    {"name": "InpStopLossAtr", "type": "double", "required": True, "default": "2.0"},
    {"name": "InpTakeProfitAtr", "type": "double", "required": True, "default": "4.0"},
    {"name": "InpMinBreakDistanceAtr", "type": "double", "required": True, "default": "0.05"},
    {"name": "InpUseSessionFilter", "type": "bool", "required": False, "default": "true"},
    {"name": "InpMagicNumber", "type": "int", "required": False, "default": "12345"},
]


def write_sample_mq5(path: Path) -> Path:
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
        "void OnTick() {}\n"
        "void OnDeinit(const int reason) {}\n",
        encoding="utf-8",
    )
    return path


def write_generated_strategy_spec(strategy_id: str, mq5_path: Path, *, strategy_version: str = "v1") -> Path:
    spec_path = GENERATED_SPECS_DIR / f"{strategy_id}.yaml"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec = {
        "schema_version": "strategy_spec_v1",
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "hypothesis_id": TEST_HYPOTHESIS_ID,
        "status": "idea",
        "created_at": "2026-05-13",
        "updated_at": "2026-05-13",
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
            "spread_source": {"type": "mt5_tester", "description": "test"},
            "slippage": {"type": "points", "value": 20, "source": "test"},
            "commission": {"type": "broker_account_or_tester_default", "value": None, "description": "test"},
            "stress_multiplier": None,
        },
        "validation": {"min_trades_required": 10, "warning_thresholds": {"min_profit_factor": 1.0}},
        "research_budget": {"max_structural_variants": 3, "max_parameter_sets": 5, "max_filter_additions": 3, "max_agent_iterations": 2, "max_complexity_score": 30},
        "lifecycle": {"state": "idea", "allowed_next_states": ["hypothesis_defined"]},
    }
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return spec_path


def register_tiny_dataset(db_path: Path) -> str:
    bars_file = Path(db_path).parent / "test_bars.csv"
    if not bars_file.is_file():
        bars_file.write_text("time\topen\thigh\tlow\tclose\ttick_volume\n2024-01-01\t100\t110\t90\t105\t1000\n", encoding="utf-8")
    return register_dataset(db_path, bars_path=bars_file, symbol="XAUUSD", timeframe="H4")["dataset_id"]


def create_baseline_approved_request(db_path: Path, strategy_id: str, *, strategy_version: str = "v1") -> dict[str, Any]:
    sandbox = SANDBOX_ROOT / strategy_id / strategy_version
    mq5_path = write_sample_mq5(sandbox / f"{strategy_id}.mq5")
    write_generated_strategy_spec(strategy_id, mq5_path, strategy_version=strategy_version)
    request = impl_mod.create_implementation_request(
        db_path,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        sandbox_dir=sandbox,
        generated_files=[f"{strategy_id}.mq5"],
        created_by="test",
        hypothesis_id=TEST_HYPOTHESIS_ID,
        strategy_spec_path=str(GENERATED_SPECS_DIR / f"{strategy_id}.yaml"),
        expected_inputs=SAMPLE_EXPECTED_INPUTS,
    )
    impl_mod.compile_check(db_path, request["implementation_request_id"], mock=True)
    impl_mod.run_diff_review(db_path, request["implementation_request_id"])
    approval = impl_mod.approve_for_baseline(
        db_path,
        request["implementation_request_id"],
        approved_by="test",
        require_real_compile=False,
        approval_scope="baseline_only",
    )
    return {"request": request, "approval": approval, "sandbox": sandbox, "mq5_path": mq5_path}


def create_experiment_fixture(
    db_path: Path,
    experiment_id: str,
    strategy_id: str,
    *,
    change_type: str = "baseline",
    parent_experiment_id: str | None = None,
    dataset_id: str | None = None,
    metrics: dict[str, Any] | None = None,
    status: str = "completed",
) -> str:
    ds_id = dataset_id or register_tiny_dataset(db_path)
    headline = metrics or {"net_return": 500.0, "profit_factor": 1.8, "trade_count": 25, "max_drawdown": 12.0}
    registry.create_experiment(
        db_path,
        {
            "experiment_id": experiment_id,
            "hypothesis_id": TEST_HYPOTHESIS_ID,
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
            "parent_experiment_id": parent_experiment_id,
            "rerun_of_experiment_id": None,
            "is_artifact_regeneration": False,
            "change_type": change_type,
            "change_summary": f"test {change_type}",
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
            "completed_at": registry.utc_now(),
            "status": status,
            "headline_metrics_json": json.dumps(headline),
        },
    )
    return experiment_id


def attach_yaml_artifact(db_path: Path, experiment_id: str, artifact_type: str, path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    registry.attach_artifact(db_path, experiment_id, artifact_type, path)
    return path
