from __future__ import annotations

import importlib
import json
import re
import tempfile
import unittest
from pathlib import Path

import yaml

from automated.research import (
    generated_baseline,
    generated_candidate,
    generated_final_holdout,
    generated_robustness,
    implementation as impl_mod,
    intake,
    queue,
    registry,
)
from automated.research.contracts import ARTIFACT_TYPES
from automated.research.schemas import GENERATED_SPECS_DIR, REPO_ROOT, SANDBOX_ROOT, SchemaValidationError
from tests.research_test_helpers import (
    SAMPLE_EXPECTED_INPUTS,
    TEST_HYPOTHESIS_ID,
    cleanup_path,
    create_baseline_approved_request,
    create_experiment_fixture,
    register_tiny_dataset,
    write_sample_mq5,
)


READINESS_TASK_NAMES = {
    "real_compile",
    "real_backtest_readiness",
    "generated_readiness_review",
    "real_toolchain_rehearsal_summary",
    "backtest_readiness",
    "readiness_review",
    "real_toolchain_rehearsal",
}


def _write_queue(path: Path, item: dict) -> Path:
    path.write_text(yaml.safe_dump({"queue_id": "PH23_Q", "items": [item]}, sort_keys=False), encoding="utf-8")
    return path


def _base_permissions(**overrides) -> dict:
    permissions = {
        "allow_runner_execution": False,
        "allow_mql5_edits": False,
        "allow_dataset_changes": False,
        "allow_validation_threshold_changes": False,
        "allow_lifecycle_apply": False,
        "allow_final_holdout": False,
        "allow_lifecycle_propose": True,
    }
    permissions.update(overrides)
    return permissions


def _generated_baseline_item(**overrides) -> dict:
    item = {
        "queue_id": "PH23_BASELINE",
        "priority": 10,
        "task_type": "generated_baseline_experiment",
        "strategy_id": "PH23_Q_STRAT",
        "strategy_version": "v1",
        "hypothesis_id": TEST_HYPOTHESIS_ID,
        "dataset_id": "DS_PH23",
        "implementation_request_id": "IMPL_REQ_PH23",
        "requested_by": "test",
        "created_at": registry.utc_now(),
        "allowed_agent_roles": [],
        "budget": {
            "max_experiments": 1,
            "max_child_experiments": 0,
            "max_runtime_minutes": 10,
            "max_parameters_changed_per_child": 1,
            "max_sweeps": 0,
            "max_failed_runs": 0,
            "max_disk_usage_mb": 512,
            "require_one_variable_at_a_time": True,
        },
        "permissions": _base_permissions(allow_runner_execution=True),
        "required_outputs": [],
        "status": "queued",
    }
    item.update(overrides)
    return item


def _final_holdout_item(strategy_id: str, impl_request_id: str, dataset_id: str, approval_id: str, **overrides) -> dict:
    item = {
        "queue_id": "PH23_FH",
        "priority": 10,
        "task_type": "generated_final_holdout_experiment",
        "strategy_id": strategy_id,
        "strategy_version": "v1",
        "hypothesis_id": TEST_HYPOTHESIS_ID,
        "dataset_id": dataset_id,
        "implementation_request_id": impl_request_id,
        "approval_id": approval_id,
        "requested_by": "test",
        "created_at": registry.utc_now(),
        "allowed_agent_roles": [],
        "budget": {
            "max_experiments": 1,
            "max_child_experiments": 0,
            "max_runtime_minutes": 10,
            "max_parameters_changed_per_child": 1,
            "max_sweeps": 0,
            "max_failed_runs": 0,
            "max_disk_usage_mb": 512,
            "require_one_variable_at_a_time": True,
        },
        "permissions": _base_permissions(allow_final_holdout=True),
        "required_outputs": [],
        "status": "queued",
    }
    item.update(overrides)
    return item


def _source(module_name: str) -> str:
    module = importlib.import_module(f"automated.research.{module_name}")
    return Path(module.__file__).read_text(encoding="utf-8")


def _create_robustness_fixture(db_path: Path, strategy_id: str, baseline_id: str) -> str:
    sweep_id = f"SWEEP_PH23_{strategy_id}"
    registry.create_sweep(
        db_path,
        {
            "sweep_id": sweep_id,
            "parent_experiment_id": baseline_id,
            "strategy_id": strategy_id,
            "hypothesis_id": TEST_HYPOTHESIS_ID,
            "sweep_type": "parameter_robustness",
            "status": "completed",
            "created_by": "test",
            "created_at": registry.utc_now(),
            "completed_at": registry.utc_now(),
            "budget_json": json.dumps({"max_child_experiments": 2, "max_parameters_changed_per_child": 1}),
            "config": {},
            "summary_path": None,
            "notes": "",
        },
    )
    for index, pf in enumerate((1.6, 1.3)):
        child_id = f"EXP_PH23_CHILD_{index}"
        create_experiment_fixture(
            db_path,
            child_id,
            strategy_id,
            parent_experiment_id=baseline_id,
            change_type="parameter_diff",
            metrics={"net_return": 100.0 * (index + 1), "profit_factor": pf, "trade_count": 20},
        )
        registry.add_sweep_child(
            db_path,
            {
                "sweep_id": sweep_id,
                "child_experiment_id": child_id,
                "child_index": index,
                "child_role": f"InpMinBreakDistanceAtr={index}",
                "parameter_diff": {"InpMinBreakDistanceAtr": {"from": 0.05, "to": 0.05 + index * 0.05}},
                "status": "completed",
            },
        )
    return sweep_id


class ResearchPhase23QueueValidationConsolidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "registry.sqlite"
        registry.init_db(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_queue_still_rejects_missing_required_fields(self) -> None:
        item = _generated_baseline_item()
        del item["implementation_request_id"]
        with self.assertRaisesRegex(SchemaValidationError, "implementation_request_id"):
            queue.validate_queue(self.db_path, _write_queue(self.root / "missing.yaml", item))

    def test_queue_still_rejects_permission_mismatches(self) -> None:
        item = _generated_baseline_item(permissions=_base_permissions(allow_runner_execution=False))
        with self.assertRaisesRegex(SchemaValidationError, "allow_runner_execution=true"):
            queue.validate_queue(self.db_path, _write_queue(self.root / "permissions.yaml", item))

    def test_queue_still_rejects_unsupported_task_types(self) -> None:
        item = _generated_baseline_item(task_type="live_trading_candidate")
        with self.assertRaisesRegex(SchemaValidationError, "queue.task_type"):
            queue.validate_queue(self.db_path, _write_queue(self.root / "unsupported.yaml", item))

    def test_queue_permission_alone_cannot_substitute_for_human_approval(self) -> None:
        item = _final_holdout_item("PH23_Q_STRAT", "IMPL_REQ_PH23", "DS_PH23", "FH_APPROVAL_MISSING")
        with self.assertRaisesRegex(SchemaValidationError, "allow_final_holdout is forbidden"):
            queue.validate_queue(self.db_path, _write_queue(self.root / "fh_permission.yaml", item))

    def test_generated_final_holdout_still_requires_final_holdout_only_approval(self) -> None:
        strategy_id = "PH23_FH_SCOPE"
        setup = create_baseline_approved_request(self.db_path, strategy_id)
        impl_request_id = setup["request"]["implementation_request_id"]
        impl_id = setup["approval"]["implementation_id"]
        dataset_id = register_tiny_dataset(self.db_path)
        approval = registry.create_scope_approval(
            self.db_path,
            {
                "approval_id": "PH23_WRONG_SCOPE",
                "implementation_id": impl_id,
                "implementation_request_id": impl_request_id,
                "strategy_id": strategy_id,
                "strategy_version": "v1",
                "approval_scope": "baseline_only",
                "approved_by": "test",
                "approved_at": registry.utc_now(),
                "allow_reuse": 0,
                "scope_metadata_json": "{}",
                "created_at": registry.utc_now(),
            },
        )
        result = queue._execute_generated_final_holdout_experiment(
            self.db_path,
            _final_holdout_item(strategy_id, impl_request_id, dataset_id, approval["approval_id"]),
            self.root / "queue_run",
            runner_script=REPO_ROOT / "automated" / "scripts" / "run_backtest.sh",
            research_output_root=self.root / "research_runs",
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("final_holdout_only", "; ".join(result["failures"]))
        cleanup_path(SANDBOX_ROOT / strategy_id)
        cleanup_path(GENERATED_SPECS_DIR / f"{strategy_id}.yaml")

    def test_generated_baseline_still_requires_explicit_baseline_approval(self) -> None:
        strategy_id = "PH23_BASELINE_APPROVAL"
        sandbox = SANDBOX_ROOT / strategy_id / "v1"
        write_sample_mq5(sandbox / f"{strategy_id}.mq5")
        request = impl_mod.create_implementation_request(
            self.db_path,
            strategy_id=strategy_id,
            strategy_version="v1",
            sandbox_dir=sandbox,
            generated_files=[f"{strategy_id}.mq5"],
            created_by="test",
            expected_inputs=SAMPLE_EXPECTED_INPUTS,
        )
        impl_mod.compile_check(self.db_path, request["implementation_request_id"], mock=True)
        impl_mod.run_diff_review(self.db_path, request["implementation_request_id"])
        before = impl_mod.require_generated_baseline_approval(self.db_path, strategy_id, "v1")
        self.assertFalse(before["approved"])
        impl_mod.approve_for_baseline(
            self.db_path,
            request["implementation_request_id"],
            approved_by="test",
            require_real_compile=False,
            approval_scope="baseline_only",
        )
        after = impl_mod.require_generated_baseline_approval(self.db_path, strategy_id, "v1", allow_mock_compile=True)
        self.assertTrue(after["approved"], after)
        cleanup_path(sandbox.parent)

    def test_readiness_rehearsal_artifacts_are_not_queue_task_evidence(self) -> None:
        self.assertTrue(READINESS_TASK_NAMES.isdisjoint(queue.TASK_TYPES))
        self.assertNotIn("generated_readiness_review", ARTIFACT_TYPES)
        self.assertNotIn("real_toolchain_rehearsal_summary", ARTIFACT_TYPES)


class ResearchPhase23FixtureHelperRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "registry.sqlite"
        registry.init_db(self.db_path)
        self.strategy_id = "PH23_FIXTURES"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        cleanup_path(SANDBOX_ROOT / self.strategy_id)
        cleanup_path(GENERATED_SPECS_DIR / f"{self.strategy_id}.yaml")
        cleanup_path(REPO_ROOT / "automated" / "research_runs" / f"EXP_PH23_BASELINE")

    def test_generated_strategy_fixture_chain_produces_expected_artifacts(self) -> None:
        hypothesis_evidence = {
            "hypothesis_id": TEST_HYPOTHESIS_ID,
            "symbol": "XAUUSD",
            "timeframe": "H4",
            "market_regime": "ranging",
        }
        setup = create_baseline_approved_request(self.db_path, self.strategy_id)
        impl_request_id = setup["request"]["implementation_request_id"]
        impl_id = setup["approval"]["implementation_id"]
        spec_path = GENERATED_SPECS_DIR / f"{self.strategy_id}.yaml"

        review_packet = intake.build_review_packet(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            output_dir=self.root / "review_packet",
        )
        baseline_id = create_experiment_fixture(self.db_path, "EXP_PH23_BASELINE", self.strategy_id)
        usage = registry.create_approval_usage(
            self.db_path,
            {
                "usage_id": "USAGE_PH23_BASELINE",
                "implementation_id": impl_id,
                "implementation_request_id": impl_request_id,
                "experiment_id": baseline_id,
                "queue_run_id": None,
                "used_at": registry.utc_now(),
                "runner_mode": "test",
                "status": "completed",
            },
        )
        baseline_review = generated_baseline.build_generated_baseline_review_packet(
            self.db_path,
            experiment_id=baseline_id,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            implementation_request_id=impl_request_id,
            implementation_id=impl_id,
            approval_status="approved_for_baseline",
            approval_usage=usage,
            runner_mode="test",
            output_dir=self.root / "baseline_review",
        )
        sweep_id = _create_robustness_fixture(self.db_path, self.strategy_id, baseline_id)
        robustness_review = generated_robustness.build_generated_robustness_review_packet(
            self.db_path,
            sweep_id=sweep_id,
            baseline_experiment_id=baseline_id,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            implementation_request_id=impl_request_id,
            implementation_id=impl_id,
            output_dir=self.root / "robustness_review",
        )
        candidate_packet = generated_candidate.build_generated_candidate_decision_packet(
            self.db_path,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            implementation_request_id=impl_request_id,
            baseline_experiment_id=baseline_id,
            robustness_sweep_id=sweep_id,
            output_dir=self.root / "candidate_packet",
        )
        final_decision_path = self.root / "final_holdout_decision.yaml"
        final_decision_path.write_text(
            yaml.safe_dump(
                {
                    "schema_version": "generated_candidate_decision_packet_v1",
                    "strategy_id": self.strategy_id,
                    "strategy_version": "v1",
                    "candidate_status": "eligible",
                    "proposed_next_action": "request_human_review_for_final_holdout",
                    "lifecycle_proposal": "final_holdout_candidate",
                    "created_at": registry.utc_now(),
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        final_approval = generated_final_holdout.approve_for_final_holdout(
            self.db_path,
            impl_request_id,
            decision_packet_path=final_decision_path,
            approved_by="test",
        )
        self.assertTrue(final_approval["approved"], final_approval)
        final_id = create_experiment_fixture(
            self.db_path,
            "EXP_PH23_FINAL",
            self.strategy_id,
            change_type="final_holdout",
            parent_experiment_id=baseline_id,
        )
        final_review = generated_final_holdout.build_generated_final_holdout_review_packet(
            self.db_path,
            experiment_id=final_id,
            strategy_id=self.strategy_id,
            strategy_version="v1",
            decision_packet_path=final_decision_path,
            approval_id=final_approval["approval_id"],
            output_dir=self.root / "final_review",
        )

        self.assertEqual(hypothesis_evidence["hypothesis_id"], TEST_HYPOTHESIS_ID)
        self.assertTrue(spec_path.is_file())
        self.assertIsNotNone(registry.get_implementation_request(self.db_path, impl_request_id))
        self.assertEqual(registry.list_implementations(self.db_path, impl_request_id)[-1]["compile_status"], "mock_checked")
        self.assertTrue((REPO_ROOT / "automated" / "implementation_requests" / impl_request_id / "diff_review.yaml").is_file())
        self.assertTrue(Path(review_packet["packet_path"]).is_file())
        self.assertEqual(baseline_review["packet"]["schema_version"], "generated_baseline_review_v1")
        self.assertEqual(robustness_review["packet"]["schema_version"], "generated_robustness_review_v1")
        self.assertEqual(candidate_packet["packet"]["schema_version"], "generated_candidate_decision_packet_v1")
        self.assertEqual(final_review["packet"]["schema_version"], "generated_final_holdout_review_v1")


class ResearchPhase23NoAuthorityChangeTests(unittest.TestCase):
    def test_no_new_cli_command_names_related_to_production_live_or_promotion(self) -> None:
        cli_source = _source("cli")
        parser_names = re.findall(r"add_parser\(\s*['\"]([^'\"]+)['\"]", cli_source)
        forbidden = [name for name in parser_names if re.search(r"production|live|promot", name)]
        self.assertEqual(forbidden, [])

    def test_no_new_queue_task_types_for_readiness_or_rehearsal(self) -> None:
        self.assertTrue(READINESS_TASK_NAMES.isdisjoint(queue.TASK_TYPES))

    def test_no_new_imports_from_readiness_rehearsal_into_authority_modules(self) -> None:
        readiness_modules = {"backtest_readiness", "readiness_review", "toolchain_rehearsal"}
        authority_modules = ["queue", "generated_candidate", "generated_baseline", "generated_robustness", "generated_final_holdout", "lifecycle"]
        for module_name in authority_modules:
            source = _source(module_name)
            code_lines = "\n".join(line for line in source.splitlines() if not line.strip().startswith("#"))
            for readiness_module in readiness_modules:
                pattern = rf"(from\s+\.?\s*{readiness_module}\b|from\s+\.?\s*import\s+.*\b{readiness_module}\b|import\s+{readiness_module}\b)"
                self.assertIsNone(re.search(pattern, code_lines), f"{module_name} imports {readiness_module}")

    def test_no_writes_or_copies_into_automated_strategies_from_cleanup_scope(self) -> None:
        cleanup_sources = {
            "queue": _source("queue"),
            "generated_candidate": _source("generated_candidate"),
            "generated_baseline": _source("generated_baseline"),
            "generated_robustness": _source("generated_robustness"),
            "generated_final_holdout": _source("generated_final_holdout"),
        }
        for module_name, source in cleanup_sources.items():
            self.assertNotRegex(source, r"shutil\.copy(?:2)?\([^)]*automated/strategies", module_name)
            self.assertNotRegex(source, r"write_text\([^)]*automated/strategies", module_name)

    def test_forbidden_values_are_not_in_allowed_collections(self) -> None:
        forbidden = {"promote_to_production", "production_candidate", "live_trading_candidate"}
        modules = [
            "contracts",
            "generated_candidate",
            "generated_baseline",
            "generated_robustness",
            "generated_final_holdout",
            "queue",
        ]
        for module_name in modules:
            module = importlib.import_module(f"automated.research.{module_name}")
            for attr in dir(module):
                if not attr.startswith("ALLOWED_"):
                    continue
                value = getattr(module, attr)
                if isinstance(value, (set, list, tuple)):
                    self.assertTrue(forbidden.isdisjoint(set(value)), f"{module_name}.{attr}")
