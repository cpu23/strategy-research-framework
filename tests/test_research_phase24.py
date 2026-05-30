from __future__ import annotations

import importlib
import json
import re
import tempfile
import unittest
from pathlib import Path

import yaml

from automated.research import (
    backtest_readiness,
    generated_candidate,
    generated_final_holdout,
    generated_robustness,
    implementation as impl_mod,
    queue,
    readiness_review,
    registry,
    toolchain_rehearsal,
)
from automated.research.contracts import ARTIFACT_TYPES
from automated.research.schemas import (
    GENERATED_SPECS_DIR,
    REPO_ROOT,
    SANDBOX_ROOT,
    STRATEGIES_ROOT,
    SchemaValidationError,
)
from tests.research_test_helpers import (
    SAMPLE_EXPECTED_INPUTS,
    TEST_HYPOTHESIS_ID,
    cleanup_path,
    create_baseline_approved_request,
    write_sample_mq5,
)


READINESS_ARTIFACT_TYPES = {
    "real_compile",
    "real_backtest_readiness",
    "generated_readiness_review",
    "real_toolchain_rehearsal_summary",
}

AUTHORITY_EVIDENCE_TYPES = {
    "generated_baseline_review",
    "generated_robustness_review",
    "generated_candidate_decision_packet",
    "generated_final_holdout_review",
}

FORBIDDEN_VALUES = {
    "promote_to_production",
    "production_candidate",
    "live_trading_candidate",
}

AUTHORITY_MODULES = {
    "queue",
    "generated_baseline",
    "generated_robustness",
    "generated_candidate",
    "generated_final_holdout",
    "lifecycle",
}

READINESS_MODULES = {
    "backtest_readiness",
    "readiness_review",
    "toolchain_rehearsal",
}

EVIDENCE_PATH = [
    "hypothesis_generation",
    "strategy_spec_generation",
    "implementation_materialization",
    "compile-check",
    "diff-review",
    "research_review_packet",
    "manual approve-for-baseline",
    "generated_baseline_experiment",
    "generated_baseline_review",
    "generated_robustness_sweep",
    "generated_robustness_review",
    "generated_candidate_decision_packet",
    "manual approve-final-holdout",
    "generated_final_holdout_experiment",
    "generated_final_holdout_review",
    "updated/generated candidate decision packet",
]


def _source(module_name: str) -> str:
    module = importlib.import_module(f"automated.research.{module_name}")
    return Path(module.__file__).read_text(encoding="utf-8")


def _imports_or_references_module(source: str, module_name: str) -> bool:
    esc = re.escape(module_name)
    import_re = re.compile(
        rf"^(?:"
        rf"import\s+{esc}\b"
        rf"|from\s+\.\s*import\s+.*\b{esc}\b"
        rf"|from\s+\.{esc}\b"
        rf"|from\s+automated\.research\s+import\s+.*\b{esc}\b"
        rf")"
    )
    usage_re = re.compile(rf"\b{esc}\.\s*\w")
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if import_re.match(stripped) or usage_re.search(stripped):
            return True
    return False


def _base_permissions(**overrides: bool) -> dict:
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


def _write_queue(path: Path, item: dict) -> Path:
    path.write_text(yaml.safe_dump({"queue_id": "PH24_Q", "items": [item]}, sort_keys=False), encoding="utf-8")
    return path


def _candidate_packet(path: Path, *, action: str = "request_human_review_for_final_holdout") -> Path:
    packet = {
        "schema_version": generated_candidate.GENERATED_CANDIDATE_DECISION_PACKET_SCHEMA,
        "strategy_id": "PH24_APPROVAL",
        "strategy_version": "v1",
        "proposed_next_action": action,
        "lifecycle_proposal": "final_holdout_candidate",
    }
    path.write_text(yaml.safe_dump(packet, sort_keys=False), encoding="utf-8")
    return path


def _minimal_generated_baseline_item(**overrides: object) -> dict:
    item = {
        "queue_id": "PH24_BASELINE",
        "priority": 10,
        "task_type": "generated_baseline_experiment",
        "strategy_id": "PH24_STRAT",
        "strategy_version": "v1",
        "hypothesis_id": TEST_HYPOTHESIS_ID,
        "dataset_id": "DS_PH24",
        "implementation_request_id": "IMPL_REQ_PH24",
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


class ResearchPhase24FilesystemInvariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "registry.sqlite"
        registry.init_db(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_sandbox_path_assertion_accepts_generated_strategy_version_dir(self) -> None:
        impl_mod.assert_sandbox_path(SANDBOX_ROOT / "PH24_FS" / "v1")

    def test_sandbox_path_assertion_rejects_automated_strategies(self) -> None:
        with self.assertRaises(SchemaValidationError):
            impl_mod.assert_sandbox_path(STRATEGIES_ROOT / "PH24_FS" / "v1")

    def test_generated_mq5_path_shape_is_strategy_id_then_version(self) -> None:
        path = impl_mod.sandbox_path("PH24_FS", "v1") / "PH24_FS.mq5"
        self.assertEqual(path.parent, SANDBOX_ROOT / "PH24_FS" / "v1")
        self.assertEqual(path.suffix, ".mq5")

    def test_implementation_request_forbids_automated_strategies(self) -> None:
        with self.assertRaises(SchemaValidationError):
            impl_mod.create_implementation_request(
                self.db_path,
                strategy_id="PH24_FS_BAD",
                strategy_version="v1",
                sandbox_dir=STRATEGIES_ROOT / "PH24_FS_BAD" / "v1",
                generated_files=["PH24_FS_BAD.mq5"],
                created_by="test",
            )

    def test_materialization_request_does_not_create_production_strategy_files(self) -> None:
        strategy_id = "PH24_FS_NO_PROD"
        prod_path = STRATEGIES_ROOT / strategy_id / "v1" / f"{strategy_id}.mq5"
        cleanup_path(SANDBOX_ROOT / strategy_id)
        cleanup_path(STRATEGIES_ROOT / strategy_id)
        impl_mod.create_implementation_request(
            self.db_path,
            strategy_id=strategy_id,
            strategy_version="v1",
            sandbox_dir=SANDBOX_ROOT / strategy_id / "v1",
            generated_files=[f"{strategy_id}.mq5"],
            created_by="test",
        )
        self.assertFalse(prod_path.exists())
        cleanup_path(SANDBOX_ROOT / strategy_id)

    def test_backtest_readiness_output_rejects_automated_strategies(self) -> None:
        raw = {
            "mode": "real_backtest_readiness",
            "expected_symbol": "XAUUSD",
            "expected_timeframe": "H4",
            "runner_conf_path": str(self.root / "runner.conf"),
            "set_file_path": str(self.root / "params.set"),
            "output_dir": str(STRATEGIES_ROOT / "PH24_READINESS_OUT"),
        }
        backtest_readiness.validate_real_backtest_readiness_config(raw)
        config = backtest_readiness.RealBacktestReadinessConfig(**raw)
        result = backtest_readiness.run_real_backtest_readiness(
            self.db_path,
            "IMPL_REQ_MISSING",
            config,
        )
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("approved", json.dumps(result).lower())

    def test_readiness_review_output_rejects_automated_strategies(self) -> None:
        compile_ev = {"mode": "real_compile", "status": "passed"}
        bt_ev = {"mode": "real_backtest_readiness", "status": "passed"}
        with self.assertRaisesRegex(ValueError, "automated/strategies"):
            readiness_review.build_readiness_review_packet(
                compile_evidence=compile_ev,
                backtest_readiness_evidence=bt_ev,
                output_path=STRATEGIES_ROOT / "PH24_READINESS" / "packet.json",
            )

    def test_rehearsal_output_rejects_automated_strategies(self) -> None:
        with self.assertRaisesRegex(ValueError, "automated/strategies"):
            toolchain_rehearsal._check_out_dir(STRATEGIES_ROOT / "PH24_REHEARSAL")


class ResearchPhase24ApprovalInvariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "registry.sqlite"
        registry.init_db(self.db_path)

    def tearDown(self) -> None:
        for strategy_id in ("PH24_APPROVAL", "PH24_BASELINE_PENDING"):
            cleanup_path(SANDBOX_ROOT / strategy_id)
            cleanup_path(GENERATED_SPECS_DIR / f"{strategy_id}.yaml")
        self.temp_dir.cleanup()

    def test_baseline_run_rejected_before_manual_approval(self) -> None:
        strategy_id = "PH24_BASELINE_PENDING"
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
        approval = impl_mod.require_generated_baseline_approval(
            self.db_path,
            strategy_id,
            "v1",
            allow_mock_compile=True,
        )
        self.assertFalse(approval["approved"])
        self.assertIn("Not approved for baseline", "; ".join(approval["errors"]))

    def test_final_holdout_rejected_before_final_holdout_only_approval(self) -> None:
        setup = create_baseline_approved_request(self.db_path, "PH24_APPROVAL")
        result = generated_final_holdout.require_generated_final_holdout_eligibility(
            self.db_path,
            "PH24_APPROVAL",
            "v1",
            allow_mock_compile=True,
        )
        self.assertFalse(result["eligible"])
        self.assertIn("final_holdout_only", "; ".join(result["errors"]))
        self.assertIn("No final_holdout_only approval", "; ".join(result["errors"]))
        self.assertTrue(setup["approval"]["baseline_only"])

    def test_edited_candidate_packet_rejected_by_digest_mismatch(self) -> None:
        setup = create_baseline_approved_request(self.db_path, "PH24_APPROVAL")
        impl_id = setup["approval"]["implementation_id"]
        packet_path = _candidate_packet(self.root / "candidate.yaml")
        approval_id = "PH24_FH_DIGEST"
        registry.create_scope_approval(
            self.db_path,
            {
                "approval_id": approval_id,
                "implementation_id": impl_id,
                "implementation_request_id": setup["request"]["implementation_request_id"],
                "strategy_id": "PH24_APPROVAL",
                "strategy_version": "v1",
                "approval_scope": "final_holdout_only",
                "approved_by": "test",
                "approved_at": registry.utc_now(),
                "allow_reuse": 0,
                "scope_metadata_json": json.dumps({"decision_packet_digest": "0" * 64}),
                "created_at": registry.utc_now(),
            },
        )
        result = generated_final_holdout.require_generated_final_holdout_eligibility(
            self.db_path,
            "PH24_APPROVAL",
            "v1",
            decision_packet_path=packet_path,
            allow_mock_compile=True,
        )
        self.assertFalse(result["eligible"])
        self.assertIn("Decision packet digest mismatch", "; ".join(result["errors"]))

    def test_reused_final_holdout_approval_rejected_by_default(self) -> None:
        setup = create_baseline_approved_request(self.db_path, "PH24_APPROVAL")
        impl_id = setup["approval"]["implementation_id"]
        approval_id = "PH24_FH_USED"
        registry.create_scope_approval(
            self.db_path,
            {
                "approval_id": approval_id,
                "implementation_id": impl_id,
                "implementation_request_id": setup["request"]["implementation_request_id"],
                "strategy_id": "PH24_APPROVAL",
                "strategy_version": "v1",
                "approval_scope": "final_holdout_only",
                "approved_by": "test",
                "approved_at": registry.utc_now(),
                "allow_reuse": 0,
                "scope_metadata_json": "{}",
                "created_at": registry.utc_now(),
            },
        )
        registry.update_scope_approval_used(self.db_path, approval_id, "FH_EXP_ALREADY_USED")
        result = generated_final_holdout.require_generated_final_holdout_eligibility(
            self.db_path,
            "PH24_APPROVAL",
            "v1",
            allow_mock_compile=True,
        )
        self.assertFalse(result["eligible"])
        self.assertIn("already consumed", "; ".join(result["errors"]))

    def test_queue_permission_alone_cannot_substitute_for_human_approval(self) -> None:
        item = _minimal_generated_baseline_item(
            task_type="generated_final_holdout_experiment",
            permissions=_base_permissions(allow_final_holdout=True),
            approval_id="FH_APPROVAL_MISSING",
        )
        with self.assertRaisesRegex(SchemaValidationError, "allow_final_holdout is forbidden"):
            queue.validate_queue(self.db_path, _write_queue(self.root / "fh_permission.yaml", item))

    def test_readiness_review_cannot_approve_baseline_or_final_holdout(self) -> None:
        packet = readiness_review.build_readiness_review_packet(
            compile_evidence={"mode": "real_compile", "status": "passed"},
            backtest_readiness_evidence={"mode": "real_backtest_readiness", "status": "passed"},
        )
        self.assertNotIn("approval_scope", packet)
        self.assertNotIn("approved_for_baseline", packet)
        self.assertNotIn("approve_baseline", json.dumps(packet))
        self.assertNotIn("approve_final_holdout", json.dumps(packet))


class ResearchPhase24CandidateInvariantTests(unittest.TestCase):
    def test_exact_allowed_candidate_actions(self) -> None:
        self.assertEqual(
            generated_candidate.ALLOWED_PROPOSED_NEXT_ACTIONS,
            {
                "reject",
                "revise_strategy_spec",
                "revise_implementation",
                "run_additional_bounded_sweep",
                "request_human_review_for_final_holdout",
                "defer",
            },
        )

    def test_exact_allowed_lifecycle_proposal_values(self) -> None:
        self.assertEqual(
            generated_candidate.ALLOWED_LIFECYCLE_PROPOSALS,
            {
                "none",
                "research_candidate",
                "robustness_candidate",
                "final_holdout_candidate",
            },
        )

    def test_forbidden_values_absent_from_candidate_allowed_sets(self) -> None:
        self.assertTrue(FORBIDDEN_VALUES.isdisjoint(generated_candidate.ALLOWED_PROPOSED_NEXT_ACTIONS))
        self.assertTrue(FORBIDDEN_VALUES.isdisjoint(generated_candidate.ALLOWED_LIFECYCLE_PROPOSALS))

    def test_candidate_packet_rejects_readiness_review_artifact_type(self) -> None:
        item = _minimal_generated_baseline_item(
            task_type="generated_candidate_decision_packet",
            permissions=_base_permissions(),
            required_outputs=["generated_readiness_review"],
        )
        with self.assertRaisesRegex(SchemaValidationError, "unknown artifact"):
            queue.validate_queue(Path(":memory:"), _write_queue(Path(tempfile.mkdtemp()) / "q.yaml", item))

    def test_candidate_packet_rejects_rehearsal_summary_artifact_type(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            item = _minimal_generated_baseline_item(
                task_type="generated_candidate_decision_packet",
                permissions=_base_permissions(),
                required_outputs=["real_toolchain_rehearsal_summary"],
            )
            with self.assertRaisesRegex(SchemaValidationError, "unknown artifact"):
                queue.validate_queue(Path(td) / "registry.sqlite", _write_queue(Path(td) / "q.yaml", item))


class ResearchPhase24ReadinessIsolationInvariantTests(unittest.TestCase):
    def test_readiness_artifact_types_excluded_from_authority_bearing_artifact_types(self) -> None:
        self.assertTrue(READINESS_ARTIFACT_TYPES.isdisjoint(ARTIFACT_TYPES))
        self.assertTrue(AUTHORITY_EVIDENCE_TYPES.issubset(ARTIFACT_TYPES))

    def test_authority_bearing_modules_do_not_import_readiness_or_rehearsal_modules(self) -> None:
        for authority in AUTHORITY_MODULES:
            source = _source(authority)
            for readiness_module in READINESS_MODULES:
                with self.subTest(authority=authority, readiness_module=readiness_module):
                    self.assertFalse(_imports_or_references_module(source, readiness_module))

    def test_readiness_review_lacks_candidate_and_lifecycle_authority_fields(self) -> None:
        packet = readiness_review.build_readiness_review_packet(
            compile_evidence={"mode": "real_compile", "status": "passed"},
            backtest_readiness_evidence={"mode": "real_backtest_readiness", "status": "passed"},
        )
        self.assertNotIn("proposed_next_action", packet)
        self.assertNotIn("lifecycle_proposal", packet)

    def test_rehearsal_summary_lacks_candidate_and_lifecycle_authority_fields(self) -> None:
        summary = toolchain_rehearsal._build_summary(
            impl_request_id="PH24_IMPL",
            compile_status="passed",
            backtest_readiness_status="passed",
            readiness_review_packet_path="/tmp/readiness_review_packet.json",
        )
        self.assertNotIn("proposed_next_action", summary)
        self.assertNotIn("lifecycle_proposal", summary)
        self.assertNotIn("approval_scope", summary)

    def test_readiness_and_rehearsal_forbidden_interpretations_cover_all_evidence_domains(self) -> None:
        required = {
            "not_baseline_evidence",
            "not_robustness_evidence",
            "not_final_holdout_evidence",
            "not_candidate_evidence",
            "not_lifecycle_evidence",
            "not_production_evidence",
            "not_live_trading_evidence",
        }
        self.assertTrue(required.issubset(set(readiness_review.FORBIDDEN_INTERPRETATIONS)))
        self.assertTrue(required.issubset(set(toolchain_rehearsal.FORBIDDEN_INTERPRETATIONS)))


class ResearchPhase24QueueInvariantTests(unittest.TestCase):
    def test_no_queue_task_exists_for_real_compile(self) -> None:
        self.assertNotIn("real_compile", queue.TASK_TYPES)
        self.assertNotIn("implementation_real_compile", queue.TASK_TYPES)

    def test_no_queue_task_exists_for_real_backtest_readiness(self) -> None:
        self.assertNotIn("real_backtest_readiness", queue.TASK_TYPES)

    def test_no_queue_task_exists_for_readiness_review(self) -> None:
        self.assertNotIn("generated_readiness_review", queue.TASK_TYPES)
        self.assertNotIn("readiness_review", queue.TASK_TYPES)

    def test_no_queue_task_exists_for_real_toolchain_rehearsal(self) -> None:
        self.assertNotIn("real_toolchain_rehearsal", queue.TASK_TYPES)
        self.assertNotIn("real_toolchain_rehearsal_summary", queue.TASK_TYPES)

    def test_queue_permissions_do_not_create_approval_authority(self) -> None:
        self.assertEqual(queue.FORBIDDEN_PERMISSION_DEFAULTS["allow_final_holdout"], False)
        self.assertNotIn("allow_baseline_approval", queue.PERMISSION_KEYS)
        self.assertNotIn("allow_final_holdout_approval", queue.PERMISSION_KEYS)
        self.assertNotIn("allow_automated_approval", queue.PERMISSION_KEYS)

    def test_queue_permissions_do_not_create_lifecycle_transition_authority(self) -> None:
        self.assertEqual(queue.FORBIDDEN_PERMISSION_DEFAULTS["allow_lifecycle_apply"], False)
        self.assertIn("apply_lifecycle_transition", queue._blocked_actions_for_item({"permissions": _base_permissions()}))


class ResearchPhase24DatasetRunnerThresholdInvariantTests(unittest.TestCase):
    def test_robustness_sweeps_block_dataset_symbol_timeframe_cost_and_threshold_mutations(self) -> None:
        blockers = generated_robustness.has_blocked_mutations(
            {
                "dataset_id": "other",
                "symbol": "EURUSD",
                "timeframe": "M15",
                "cost_multipliers": {"spread": 2},
                "validation": {"min_trades_required": 5},
            }
        )
        joined = "; ".join(blockers)
        for expected in ("dataset", "symbol", "timeframe", "cost", "validation threshold"):
            self.assertIn(expected, joined)

    def test_queue_has_no_permission_to_mutate_runner_script(self) -> None:
        self.assertNotIn("allow_runner_script_changes", queue.PERMISSION_KEYS)
        self.assertNotIn("allow_runner_mutation", queue.PERMISSION_KEYS)

    def test_final_holdout_review_freezes_protected_config_invariant_keys(self) -> None:
        source = _source("generated_final_holdout")
        for key in (
            "symbol_unchanged",
            "timeframe_unchanged",
            "dataset_unchanged",
            "cost_model_unchanged",
            "validation_unchanged",
            "runner_unchanged",
        ):
            self.assertIn(key, source)


class ResearchPhase24DenyListInvariantTests(unittest.TestCase):
    def test_forbidden_values_absent_from_all_allowed_sets(self) -> None:
        for module_name in (
            "contracts",
            "generated_baseline",
            "generated_robustness",
            "generated_candidate",
            "generated_final_holdout",
            "queue",
            "readiness_review",
            "toolchain_rehearsal",
        ):
            module = importlib.import_module(f"automated.research.{module_name}")
            for attr in dir(module):
                if not attr.startswith("ALLOWED_"):
                    continue
                value = getattr(module, attr)
                if isinstance(value, (set, list, tuple)):
                    with self.subTest(module=module_name, attr=attr):
                        self.assertTrue(FORBIDDEN_VALUES.isdisjoint(set(value)))

    def test_forbidden_values_absent_from_queue_task_types(self) -> None:
        self.assertTrue(FORBIDDEN_VALUES.isdisjoint(queue.TASK_TYPES))

    def test_forbidden_values_absent_from_lifecycle_proposals_and_candidate_actions(self) -> None:
        allowed = (
            set(generated_candidate.ALLOWED_PROPOSED_NEXT_ACTIONS)
            | set(generated_candidate.ALLOWED_LIFECYCLE_PROPOSALS)
        )
        self.assertTrue(FORBIDDEN_VALUES.isdisjoint(allowed))


class ResearchPhase24DocumentationInvariantTests(unittest.TestCase):
    DOC_PATH = REPO_ROOT / "automated" / "research" / "docs" / "generated_strategy_authority_invariants.md"

    def test_authority_invariant_doc_exists(self) -> None:
        self.assertTrue(self.DOC_PATH.is_file())

    def test_authority_invariant_doc_names_canonical_evidence_path(self) -> None:
        text = self.DOC_PATH.read_text(encoding="utf-8")
        cursor = 0
        for step in EVIDENCE_PATH:
            idx = text.find(step, cursor)
            self.assertGreaterEqual(idx, 0, f"missing or out-of-order evidence step: {step}")
            cursor = idx + len(step)

    def test_authority_invariant_doc_names_allowed_actions_and_lifecycle_values(self) -> None:
        text = self.DOC_PATH.read_text(encoding="utf-8")
        for term in sorted(generated_candidate.ALLOWED_PROPOSED_NEXT_ACTIONS):
            self.assertIn(term, text)
        for term in sorted(generated_candidate.ALLOWED_LIFECYCLE_PROPOSALS):
            self.assertIn(term, text)

    def test_authority_invariant_doc_names_forbidden_values_and_non_goals(self) -> None:
        text = self.DOC_PATH.read_text(encoding="utf-8")
        for term in FORBIDDEN_VALUES:
            self.assertIn(term, text)
        self.assertIn("production/live non-goals", text.lower())

    def test_authority_invariant_doc_records_maintenance_rule(self) -> None:
        text = self.DOC_PATH.read_text(encoding="utf-8").lower()
        self.assertIn("future phases must update this doc", text)
        self.assertIn("corresponding invariant tests", text)
