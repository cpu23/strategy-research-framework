"""Phase 25: Operator acceptance and release freeze.

Verifies the generated strategy research OS v1 is complete and bounded:
- All 14 core docs exist
- Runbooks agree on the canonical evidence path
- Authority invariants doc names forbidden values only as forbidden/non-goals
- Readiness/rehearsal artifacts are documented as operator-only
- Phase 16 dry-run path remains represented
- No docs propose production/live/promotion as actionable
- No source code exposes forbidden values as allowed values
- No queue tasks for readiness/rehearsal
- No lifecycle apply path for generated strategies
- No writes/copies into automated/strategies/
- Final test command is documented

No new execution authority is created.
"""

import importlib
import re
import unittest
from pathlib import Path

from automated.research.schemas import REPO_ROOT
from automated.research.contracts import ARTIFACT_TYPES


DOCS_DIR = REPO_ROOT / "automated" / "research" / "docs"
RESEARCH_DIR = REPO_ROOT / "automated" / "research"
TESTS_DIR = REPO_ROOT / "tests"

ALL_CORE_DOCS = [
    "generated_strategy_research_os.md",
    "generated_strategy_authority_invariants.md",
    "generated_strategy_operator_runbook.md",
    "phase15d_queue_validation_audit.md",
    "phase16_end_to_end_dry_run_report.md",
    "phase23_cleanup_consolidation.md",
    "real_compile_verification.md",
    "real_backtest_readiness.md",
    "real_run_readiness_checklist.md",
    "real_toolchain_artifact_taxonomy.md",
    "real_toolchain_operator_freeze_checklist.md",
    "real_toolchain_rehearsal_runbook.md",
    "readiness_review_packet.md",
    "dry_run_artifact_cleanup.md",
]

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

FORBIDDEN_VALUES = [
    "promote_to_production",
    "production_candidate",
    "live_trading_candidate",
]

CANONICAL_READINESS_TERMS = [
    "real_compile",
    "real_backtest_readiness",
    "generated_readiness_review",
    "real_toolchain_rehearsal_summary",
]

# Natural-language patterns that correspond to each evidence path step
# in the operator runbook (which uses prose, not code identifiers).
EVIDENCE_PATH_RUNBOOK_PATTERNS = [
    "hypothesis",
    "spec",
    "materialize",
    "compile-check",
    "diff-review",
    "review packet",
    "approve.*baseline",
    "run baseline",
    "review baseline",
    "run robustness",
    "review robustness",
    "candidate decision packet",
    "approve.*final.*holdout",
    "run final holdout",
    "review final holdout",
    "decision packet",
]

RESEARCH_PY_MODULES = [
    "contracts",
    "generated_baseline",
    "generated_robustness",
    "generated_candidate",
    "generated_final_holdout",
    "queue",
    "readiness_review",
    "toolchain_rehearsal",
    "lifecycle",
    "implementation",
    "intake",
    "backtest_readiness",
    "compiler",
]


def _source(module_name: str) -> str:
    mod = importlib.import_module(f"automated.research.{module_name}")
    return Path(mod.__file__).read_text(encoding="utf-8")


def _allowed_sets(module) -> list[set]:
    return [
        getattr(module, attr)
        for attr in dir(module)
        if attr.startswith("ALLOWED_") and isinstance(getattr(module, attr), (set, frozenset))
    ]


# ---------------------------------------------------------------------------
# WP 25A — All core docs exist
# ---------------------------------------------------------------------------

class ResearchPhase25DocExistenceTests(unittest.TestCase):
    """All 14 documentation files must exist."""

    def test_all_core_docs_exist(self):
        missing = []
        for fname in ALL_CORE_DOCS:
            path = DOCS_DIR / fname
            if not path.is_file():
                missing.append(fname)
        self.assertFalse(missing, f"Missing docs: {missing}")


# ---------------------------------------------------------------------------
# WP 25B — Evidence path agreement across three canonical docs
# ---------------------------------------------------------------------------

class ResearchPhase25EvidencePathAgreementTests(unittest.TestCase):
    """authority_invariants.md, research_os.md, and operator_runbook.md must
    all represent the same 16-step evidence path."""

    def _verify_evidence_path_in_doc(self, fname: str, paths: list[str]) -> list[str]:
        text = (DOCS_DIR / fname).read_text(encoding="utf-8")
        missing = []
        for step in paths:
            if step not in text:
                missing.append(step)
        return missing

    def test_authority_invariants_has_all_evidence_steps(self):
        missing = self._verify_evidence_path_in_doc(
            "generated_strategy_authority_invariants.md", EVIDENCE_PATH
        )
        self.assertFalse(missing, f"authority_invariants.md missing steps: {missing}")

    def test_authority_invariants_evidence_steps_in_order(self):
        text = (DOCS_DIR / "generated_strategy_authority_invariants.md").read_text(encoding="utf-8")
        cursor = 0
        for step in EVIDENCE_PATH:
            idx = text.find(step, cursor)
            self.assertGreaterEqual(
                idx, 0,
                f"Evidence step {step!r} not found or out of order in authority_invariants.md",
            )
            cursor = idx + len(step)

    def test_research_os_has_all_evidence_steps(self):
        text = (DOCS_DIR / "generated_strategy_research_os.md").read_text(encoding="utf-8")
        checks = [
            ("hypothesis_generation", "hypothesis_generation"),
            ("strategy_spec_generation", "strategy_spec_generation"),
            ("implementation_materialization", "implementation_materialization"),
            ("compile-check", "compile-check"),
            ("diff-review", "diff-review"),
            ("research_review_packet", "research_review_packet"),
            ("approve-for-baseline", "approve-for-baseline"),
            ("generated_baseline_experiment", "generated_baseline_experiment"),
            ("generated_baseline_review", "generated_baseline_review"),
            ("generated_robustness_sweep", "generated_robustness_sweep"),
            ("generated_robustness_review", "generated_robustness_review"),
            ("generated_candidate_decision_packet", "generated_candidate_decision_packet"),
            ("approve-for-final-holdout", "approve-for-final-holdout"),
            ("generated_final_holdout_experiment", "generated_final_holdout_experiment"),
            ("generated_final_holdout_review", "generated_final_holdout_review"),
            ("updated candidate decision packet", "updated candidate decision packet"),
        ]
        missing = [label for label, needle in checks if needle not in text]
        self.assertFalse(missing, f"research_os.md missing steps: {missing}")

    def test_operator_runbook_has_all_evidence_step_concepts(self):
        text = (DOCS_DIR / "generated_strategy_operator_runbook.md").read_text(encoding="utf-8")
        text_lower = text.lower()
        missing = []
        for pattern in EVIDENCE_PATH_RUNBOOK_PATTERNS:
            compiled = re.compile(pattern, re.IGNORECASE)
            if not compiled.search(text_lower):
                missing.append(pattern)
        self.assertFalse(missing, f"operator_runbook.md missing patterns: {missing}")

    def test_operator_runbook_readiness_rehearsal_steps_not_in_evidence_path(self):
        text = (DOCS_DIR / "generated_strategy_operator_runbook.md").read_text(encoding="utf-8")
        self.assertIn("Real compile verification does not authorize baseline", text)
        self.assertIn("Real backtest readiness does **not** authorize", text)
        self.assertIn("The rehearsal is **evidence-only**", text)

    def test_research_os_readiness_rehearsal_steps_not_in_pipeline_diagram(self):
        text = (DOCS_DIR / "generated_strategy_research_os.md").read_text(encoding="utf-8")
        for term in CANONICAL_READINESS_TERMS:
            self.assertNotIn(term, text)


# ---------------------------------------------------------------------------
# WP 25C — Authority invariants doc: forbidden values as forbidden only
# WP 25F — No docs propose production/live/promotion as actionable
# ---------------------------------------------------------------------------

class ResearchPhase25ForbiddenValueContextTests(unittest.TestCase):
    """Forbidden values appear only in forbidden/non-goal contexts, not as
    allowed or actionable values."""

    def test_authority_invariants_exists(self):
        path = DOCS_DIR / "generated_strategy_authority_invariants.md"
        self.assertTrue(path.is_file())

    def test_authority_invariants_has_forbidden_values_section(self):
        text = (DOCS_DIR / "generated_strategy_authority_invariants.md").read_text(encoding="utf-8")
        self.assertIn("Forbidden Values", text)

    def test_authority_invariants_has_production_live_non_goals_section(self):
        text = (DOCS_DIR / "generated_strategy_authority_invariants.md").read_text(encoding="utf-8")
        self.assertIn("Production/Live Non-Goals", text)

    def test_authority_invariants_names_all_three_forbidden_values(self):
        text = (DOCS_DIR / "generated_strategy_authority_invariants.md").read_text(encoding="utf-8")
        for term in FORBIDDEN_VALUES:
            self.assertIn(term, text)

    def test_forbidden_values_not_in_research_os_as_actionable(self):
        text = (DOCS_DIR / "generated_strategy_research_os.md").read_text(encoding="utf-8")
        for term in FORBIDDEN_VALUES:
            self.assertNotIn(term, text)

    def test_forbidden_values_not_in_operator_runbook_as_actionable(self):
        text = (DOCS_DIR / "generated_strategy_operator_runbook.md").read_text(encoding="utf-8")
        for term in FORBIDDEN_VALUES:
            if term in text:
                lines = text.splitlines()
                for i, line in enumerate(lines, 1):
                    if term in line:
                        self.fail(
                            f"{term} found in operator_runbook.md line {i}: {line.strip()}"
                        )

    def test_forbidden_values_not_in_readiness_runbook_as_actionable(self):
        text = (DOCS_DIR / "real_toolchain_rehearsal_runbook.md").read_text(encoding="utf-8")
        for term in FORBIDDEN_VALUES:
            self.assertNotIn(term, text)

    def test_forbidden_values_not_in_readiness_checklist_as_actionable(self):
        text = (DOCS_DIR / "real_run_readiness_checklist.md").read_text(encoding="utf-8")
        self.assertNotIn("promote_to_production", text)
        self.assertNotIn("production_candidate", text)
        self.assertNotIn("live_trading_candidate", text)

    def test_forbidden_values_only_in_deny_list_or_non_goal_contexts(self):
        exempted = [
            "generated_strategy_authority_invariants.md",
            "dry_run_artifact_cleanup.md",
            "phase23_cleanup_consolidation.md",
            "readiness_review_packet.md",
            "real_toolchain_operator_freeze_checklist.md",
            "real_toolchain_rehearsal_runbook.md",
        ]
        exempt_set = set(exempted)

        for fname in ALL_CORE_DOCS:
            if fname in exempt_set:
                continue
            path = DOCS_DIR / fname
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for term in FORBIDDEN_VALUES:
                if term in text:
                    for i, line in enumerate(text.splitlines(), 1):
                        if term in line:
                            self.fail(
                                f"{term} found in {fname} line {i}: {line.strip()}"
                            )


# ---------------------------------------------------------------------------
# WP 25D — Readiness/rehearsal documented as operator-only
# WP 25E — Phase 16 dry-run path remains represented
# ---------------------------------------------------------------------------

class ResearchPhase25OperatorBoundaryTests(unittest.TestCase):
    """Readiness/rehearsal artifacts are operator-only evidence. Phase 16
    dry-run path is still documented."""

    def test_taxonomy_doc_states_operator_evidence_only(self):
        text = (DOCS_DIR / "real_toolchain_artifact_taxonomy.md").read_text(encoding="utf-8")
        self.assertIn("evidence only", text)
        self.assertIn("operator", text)

    def test_taxonomy_doc_names_all_four_canonical_types(self):
        text = (DOCS_DIR / "real_toolchain_artifact_taxonomy.md").read_text(encoding="utf-8")
        for term in CANONICAL_READINESS_TERMS:
            self.assertIn(term, text)

    def test_taxonomy_doc_says_excluded_from_contracts_artifact_types(self):
        text = (DOCS_DIR / "real_toolchain_artifact_taxonomy.md").read_text(encoding="utf-8")
        self.assertIn("**not** listed in", text)
        self.assertIn("contracts.ARTIFACT_TYPES", text)
        self.assertIn("Exclusion from `contracts.ARTIFACT_TYPES`", text)

    def test_taxonomy_doc_forbids_readiness_as_baseline_evidence(self):
        text = (DOCS_DIR / "real_toolchain_artifact_taxonomy.md").read_text(encoding="utf-8")
        self.assertIn("not satisfy baseline evidence", text)

    def test_taxonomy_doc_forbids_readiness_as_candidate_evidence(self):
        text = (DOCS_DIR / "real_toolchain_artifact_taxonomy.md").read_text(encoding="utf-8")
        self.assertIn("not satisfy candidate decision evidence", text)

    def test_taxonomy_doc_forbids_readiness_as_lifecycle_evidence(self):
        text = (DOCS_DIR / "real_toolchain_artifact_taxonomy.md").read_text(encoding="utf-8")
        self.assertIn("not satisfy lifecycle evidence", text)

    def test_taxonomy_doc_forbids_readiness_as_production_live_evidence(self):
        text = (DOCS_DIR / "real_toolchain_artifact_taxonomy.md").read_text(encoding="utf-8")
        self.assertIn("not satisfy production evidence", text)
        self.assertIn("not satisfy live trading evidence", text)

    def test_rehearsal_runbook_doc_states_evidence_only(self):
        text = (DOCS_DIR / "real_toolchain_rehearsal_runbook.md").read_text(encoding="utf-8")
        self.assertIn("evidence-only", text)
        self.assertIn("no lifecycle steps", text)

    def test_readiness_types_excluded_from_contracts_artifact_types(self):
        for term in CANONICAL_READINESS_TERMS:
            self.assertNotIn(term, ARTIFACT_TYPES)

    def test_phase16_dry_run_report_exists(self):
        path = DOCS_DIR / "phase16_end_to_end_dry_run_report.md"
        self.assertTrue(path.is_file())

    def test_phase16_dry_run_report_contains_full_pipeline_result(self):
        text = (DOCS_DIR / "phase16_end_to_end_dry_run_report.md").read_text(encoding="utf-8")
        self.assertIn("PASS", text)
        self.assertIn("DRYRUN_PHASE16_20260513T143641Z", text)
        self.assertIn("hypothesis", text.lower())
        self.assertIn("final holdout review", text.lower())
        self.assertIn("candidate decision packet", text.lower())
        self.assertIn("baseline", text.lower())
        self.assertIn("robustness", text.lower())

    def test_phase16_dry_run_report_records_negative_checks(self):
        text = (DOCS_DIR / "phase16_end_to_end_dry_run_report.md").read_text(encoding="utf-8")
        self.assertIn("No writes or copies into `automated/strategies/`", text)
        self.assertIn("digest mismatch", text)
        self.assertIn("approval already consumed", text)

    def test_dry_run_artifact_cleanup_doc_references_phase16(self):
        text = (DOCS_DIR / "dry_run_artifact_cleanup.md").read_text(encoding="utf-8")
        self.assertIn("Phase 16", text)
        self.assertIn("DRYRUN_PHASE16_20260513T143641Z", text)


# ---------------------------------------------------------------------------
# WP 25G — No source code exposes forbidden values as allowed
# WP 25H — No queue tasks for readiness/rehearsal
# WP 25I — No lifecycle apply path for generated strategies
# WP 25J — No writes/copies into automated/strategies/
# ---------------------------------------------------------------------------

class ResearchPhase25SourceCodeInvariantTests(unittest.TestCase):
    """Source code invariants for the release freeze."""

    @classmethod
    def setUpClass(cls):
        cls.queue = importlib.import_module("automated.research.queue")

    def test_forbidden_values_not_in_allowed_sets(self):
        for module_name in RESEARCH_PY_MODULES:
            try:
                module = importlib.import_module(f"automated.research.{module_name}")
            except ImportError:
                continue
            for attr in dir(module):
                if not attr.startswith("ALLOWED_"):
                    continue
                value = getattr(module, attr)
                if isinstance(value, (set, frozenset)):
                    for term in FORBIDDEN_VALUES:
                        with self.subTest(module=module_name, attr=attr, term=term):
                            self.assertNotIn(term, value)

    def test_forbidden_values_not_in_queue_task_types(self):
        for term in FORBIDDEN_VALUES:
            self.assertNotIn(term, self.queue.TASK_TYPES)

    def test_no_queue_tasks_for_readiness_rehearsal(self):
        blocked = {
            "real_compile",
            "real_backtest_readiness",
            "generated_readiness_review",
            "readiness_review",
            "real_toolchain_rehearsal",
            "real_toolchain_rehearsal_summary",
        }
        for task in blocked:
            with self.subTest(task=task):
                self.assertNotIn(task, self.queue.TASK_TYPES)

    def test_no_generated_strategy_module_calls_lifecycle_apply_transition(self):
        generated_modules = [
            "generated_baseline",
            "generated_robustness",
            "generated_candidate",
            "generated_final_holdout",
        ]
        for mod_name in generated_modules:
            source = _source(mod_name)
            self.assertNotIn(
                "apply_transition", source,
                f"{mod_name}.py must not call lifecycle apply_transition",
            )

    def test_generated_strategy_modules_do_not_import_lifecycle(self):
        generated_modules = [
            "generated_baseline",
            "generated_robustness",
            "generated_candidate",
            "generated_final_holdout",
        ]
        for mod_name in generated_modules:
            source = _source(mod_name)
            esc = re.escape("lifecycle")
            import_re = re.compile(
                rf"^(?:"
                rf"import\s+{esc}\b"
                rf"|from\s+\.\s*import\s+.*\b{esc}\b"
                rf"|from\s+\.{esc}\b"
                rf"|from\s+automated\.research\s+import\s+.*\b{esc}\b"
                rf")"
            )
            for line in source.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                self.assertFalse(
                    import_re.match(stripped),
                    f"{mod_name}.py must not import lifecycle: {stripped}",
                )

    def test_queue_forbids_lifecycle_apply_in_permissions(self):
        self.assertFalse(self.queue.FORBIDDEN_PERMISSION_DEFAULTS["allow_lifecycle_apply"])
        self.assertNotIn("allow_lifecycle_approval", self.queue.PERMISSION_KEYS)
        self.assertNotIn("allow_baseline_approval", self.queue.PERMISSION_KEYS)

    def test_readiness_rehearsal_modules_do_not_create_lifecycle_or_approval(self):
        for mod_name in ("backtest_readiness", "readiness_review", "toolchain_rehearsal", "compiler"):
            source = _source(mod_name)
            self.assertNotIn("apply_transition", source)
            self.assertNotIn("create_scope_approval", source)

    def test_assert_sandbox_path_rejects_automated_strategies(self):
        from automated.research.schemas import SchemaValidationError, STRATEGIES_ROOT, SANDBOX_ROOT
        from automated.research.implementation import assert_sandbox_path, sandbox_path

        assert_sandbox_path(SANDBOX_ROOT / "P25_SAFE" / "v1")

        with self.assertRaises(SchemaValidationError):
            assert_sandbox_path(STRATEGIES_ROOT / "P25_UNSAFE" / "v1")

    def test_implementation_request_rejects_automated_strategies_sandbox_dir(self):
        import tempfile
        from automated.research.schemas import SchemaValidationError, STRATEGIES_ROOT
        from automated.research.implementation import create_implementation_request

        db = Path(tempfile.mkdtemp()) / "test_registry.sqlite"
        import automated.research.registry as reg
        reg.init_db(db)

        with self.assertRaises(SchemaValidationError):
            create_implementation_request(
                db,
                strategy_id="P25_BAD",
                strategy_version="v1",
                sandbox_dir=STRATEGIES_ROOT / "P25_BAD" / "v1",
                generated_files=["P25_BAD.mq5"],
                created_by="test",
            )

    def test_readiness_review_rejects_automated_strategies_output_path(self):
        from automated.research.readiness_review import build_readiness_review_packet
        from automated.research.schemas import STRATEGIES_ROOT

        compile_ev = {"mode": "real_compile", "status": "passed"}
        bt_ev = {"mode": "real_backtest_readiness", "status": "passed"}

        with self.assertRaises((ValueError, IOError)):
            build_readiness_review_packet(
                strategy_id="P25_BAD",
                version="v1",
                compile_evidence=compile_ev,
                backtest_readiness_evidence=bt_ev,
                output_path=STRATEGIES_ROOT / "P25_BAD" / "packet.json",
            )

    def test_rehearsal_check_out_dir_rejects_automated_strategies(self):
        from automated.research.toolchain_rehearsal import _check_out_dir
        from automated.research.schemas import STRATEGIES_ROOT

        with self.assertRaises((ValueError, IOError)):
            _check_out_dir(STRATEGIES_ROOT / "P25_BAD")


# ---------------------------------------------------------------------------
# WP 25K — Final test command is documented
# ---------------------------------------------------------------------------

class ResearchPhase25ReleaseCommandTests(unittest.TestCase):
    """The final test command must be documented."""

    def test_final_test_command_documented(self):
        text = (DOCS_DIR / "real_toolchain_operator_freeze_checklist.md").read_text(encoding="utf-8")
        self.assertIn("unittest discover", text)
        self.assertIn("test_*.py", text)

    def test_final_test_command_documented_in_release_doc(self):
        text = (DOCS_DIR / "generated_strategy_research_os_v1_release.md").read_text(encoding="utf-8")
        self.assertIn("unittest discover", text)
        self.assertIn("test_*.py", text)


if __name__ == "__main__":
    unittest.main()
