"""Phase 22: Authority boundary audit and readiness freeze."""

import importlib
import json
import re
import unittest
from pathlib import Path

from automated.research import (
    backtest_readiness,
    compiler,
    readiness_review,
    toolchain_rehearsal,
)
from automated.research.schemas import REPO_ROOT
from automated.research.contracts import ARTIFACT_TYPES


DOCS_DIR = REPO_ROOT / "automated" / "research" / "docs"
CANONICAL_TERMS = [
    "real_compile",
    "real_backtest_readiness",
    "generated_readiness_review",
    "real_toolchain_rehearsal_summary",
]

SIX_DOCS = [
    "real_compile_verification.md",
    "real_backtest_readiness.md",
    "readiness_review_packet.md",
    "real_toolchain_rehearsal_runbook.md",
    "generated_strategy_operator_runbook.md",
    "real_run_readiness_checklist.md",
]

AUTHORITY_MODULES = [
    "queue",
    "generated_candidate",
    "generated_baseline",
    "generated_robustness",
    "generated_final_holdout",
    "lifecycle",
]

READINESS_MODULES = [
    "compiler",
    "backtest_readiness",
    "readiness_review",
    "toolchain_rehearsal",
]

DENY_TERMS = [
    "promote_to_production",
    "production_candidate",
    "live_trading_candidate",
]


def _module_source(mod_name: str) -> str:
    mod = importlib.import_module(f"automated.research.{mod_name}")
    return Path(mod.__file__).read_text(encoding="utf-8")


def _imported_in_source(source: str, mod_name: str) -> bool:
    """Check if mod_name is actually imported or referenced as a module.

    Matches real code-coupling patterns on non-comment lines:
      import mod_name
      from . import mod_name
      from .mod_name import ...
      from automated.research import mod_name
      mod_name.attribute
    """
    esc = re.escape(mod_name)
    import_re = re.compile(
        rf"^(?:"
        rf"import\s+{esc}\b"
        rf"|from\s+\.\s*import\s+.*\b{esc}\b"
        rf"|from\s+\.{esc}\b"
        rf"|from\s+automated\.research\s+import\s+.*\b{esc}\b"
        rf")",
    )
    usage_re = re.compile(rf"\b{esc}\.\s*\w")

    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if import_re.match(stripped):
            return True
        if usage_re.search(stripped):
            return True
    return False


# ---------------------------------------------------------------------------
# WP 22A — Authority boundary import isolation
# ---------------------------------------------------------------------------

class ResearchPhase22AuthorityImportTests(unittest.TestCase):
    """Authority-bearing modules must not import/read/use readiness modules."""

    # (authority, list-of-readiness-modules to check)
    BOUNDARIES: list[tuple[str, list[str]]] = [
        ("queue",                    ["backtest_readiness", "readiness_review", "toolchain_rehearsal"]),
        ("generated_candidate",      ["compiler", "backtest_readiness", "readiness_review", "toolchain_rehearsal"]),
        ("generated_baseline",       ["readiness_review", "toolchain_rehearsal"]),
        ("generated_robustness",     ["readiness_review", "toolchain_rehearsal"]),
        ("generated_final_holdout",  ["readiness_review", "toolchain_rehearsal"]),
        ("lifecycle",                ["compiler", "backtest_readiness", "readiness_review", "toolchain_rehearsal"]),
    ]

    def test_authority_boundaries(self):
        for auth_mod, forbidden_readiness_mods in self.BOUNDARIES:
            source = _module_source(auth_mod)
            for read_mod in forbidden_readiness_mods:
                self.assertFalse(
                    _imported_in_source(source, read_mod),
                    f"{auth_mod}.py must not import or reference {read_mod}",
                )

    def test_implementation_can_import_compiler(self):
        """implementation.py is shared infrastructure, not authority-bearing."""
        source = _module_source("implementation")
        # compiler load is legitimate for compile-check execution
        self.assertTrue(
            _imported_in_source(source, "compiler"),
            "implementation.py should be allowed to import compiler",
        )


# ---------------------------------------------------------------------------
# WP 22B / 22C — Doc existence and content
# ---------------------------------------------------------------------------

class ResearchPhase22DocExistenceTests(unittest.TestCase):
    """Check both new docs exist and contain required content."""

    def test_artifact_taxonomy_doc_exists(self):
        path = DOCS_DIR / "real_toolchain_artifact_taxonomy.md"
        self.assertTrue(path.is_file(), f"Missing: {path}")

    def test_artifact_taxonomy_names_all_four_types(self):
        path = DOCS_DIR / "real_toolchain_artifact_taxonomy.md"
        text = path.read_text(encoding="utf-8")
        for term in CANONICAL_TERMS:
            self.assertIn(term, text, f"Artifact taxonomy doc should mention {term}")

    def test_artifact_taxonomy_mentions_registry_exclusion(self):
        path = DOCS_DIR / "real_toolchain_artifact_taxonomy.md"
        text = path.read_text(encoding="utf-8")
        self.assertIn("contracts.ARTIFACT_TYPES", text)

    def test_freeze_checklist_doc_exists(self):
        path = DOCS_DIR / "real_toolchain_operator_freeze_checklist.md"
        self.assertTrue(path.is_file(), f"Missing: {path}")

    FREECHECK_REQUIRED = [
        "automated/generated_strategies/",
        "no artifact was written or copied",
        "automated/strategies",
        "real compile config yaml",
        "real backtest readiness config yaml",
        "output directory",
        "output safety",
        "runner script",
        "is unchanged",
        "dataset",
        "cost",
        "no validation threshold",
        "queue was **not** used",
        "readiness review packet was **not** passed",
        "no lifecycle transition",
        "no scope approval",
        "no production promotion",
        "no live trading",
        "test suite passes",
    ]

    def test_freeze_checklist_contains_required_items(self):
        path = DOCS_DIR / "real_toolchain_operator_freeze_checklist.md"
        text = path.read_text(encoding="utf-8").lower()
        for item in self.FREECHECK_REQUIRED:
            with self.subTest(item=item):
                self.assertIn(item.lower(), text, f"Freeze checklist should mention: {item}")


# ---------------------------------------------------------------------------
# WP 22E — Docs terminology consistency
# ---------------------------------------------------------------------------

class ResearchPhase22DocTerminologyTests(unittest.TestCase):
    """Existing docs must use the four canonical terms consistently."""

    def test_compile_doc_uses_canonical_term(self):
        text = (DOCS_DIR / "real_compile_verification.md").read_text(encoding="utf-8")
        self.assertIn("real_compile", text)

    def test_backtest_readiness_doc_uses_canonical_term(self):
        text = (DOCS_DIR / "real_backtest_readiness.md").read_text(encoding="utf-8")
        self.assertIn("real_backtest_readiness", text)

    def test_readiness_review_doc_uses_canonical_term(self):
        text = (DOCS_DIR / "readiness_review_packet.md").read_text(encoding="utf-8")
        self.assertIn("generated_readiness_review", text)

    def test_rehearsal_runbook_doc_uses_canonical_term(self):
        text = (DOCS_DIR / "real_toolchain_rehearsal_runbook.md").read_text(encoding="utf-8")
        self.assertIn("real_toolchain_rehearsal_summary", text)

    def test_operator_runbook_uses_canonical_terms(self):
        text = (DOCS_DIR / "generated_strategy_operator_runbook.md").read_text(encoding="utf-8")
        # The runbook uses workflow terms, not schema artifact_type strings.
        # Check for the concepts rather than exact canonical terms.
        self.assertIn("real_compile", text)
        self.assertIn("real_backtest_readiness", text)
        self.assertIn("real_toolchain_rehearsal_summary", text)
        # generated_readiness_review is the artifact_type in the packet schema;
        # the runbook calls it "readiness review packet" — check the concept.
        self.assertIn("readiness_review_packet", text)

    def test_readiness_checklist_uses_canonical_terms(self):
        text = (DOCS_DIR / "real_run_readiness_checklist.md").read_text(encoding="utf-8")
        self.assertIn("real_compile", text)
        self.assertIn("real_backtest_readiness", text)


# ---------------------------------------------------------------------------
# WP 22D — Artifact shape assertions
# ---------------------------------------------------------------------------

class ResearchPhase22ArtifactShapeTests(unittest.TestCase):
    """Readiness/rehearsal artifacts must lack lifecycle-related fields."""

    def _make_minimal_compile_evidence(self) -> dict:
        return {
            "mode": "real_compile",
            "status": "passed",
            "impl_request_id": "REQ_P22_001",
            "strategy_id": "strat_p22",
            "version": "v1",
            "implementation_id": "IMPL_P22_001",
        }

    def _make_minimal_bt_evidence(self) -> dict:
        return {
            "mode": "real_backtest_readiness",
            "status": "passed",
            "impl_request_id": "REQ_P22_001",
            "strategy_id": "strat_p22",
            "version": "v1",
            "implementation_id": "IMPL_P22_001",
        }

    def test_readiness_review_lacks_proposed_next_action(self):
        packet = readiness_review.build_readiness_review_packet(
            strategy_id="strat_p22",
            version="v1",
            compile_evidence=self._make_minimal_compile_evidence(),
            backtest_readiness_evidence=self._make_minimal_bt_evidence(),
        )
        self.assertNotIn("proposed_next_action", packet)
        self.assertIn("proposed_next_manual_action", packet)

    def test_readiness_review_lacks_lifecycle_proposal(self):
        packet = readiness_review.build_readiness_review_packet(
            strategy_id="strat_p22",
            version="v1",
            compile_evidence=self._make_minimal_compile_evidence(),
            backtest_readiness_evidence=self._make_minimal_bt_evidence(),
        )
        self.assertNotIn("lifecycle_proposal", packet)

    def test_rehearsal_summary_lacks_proposed_next_action(self):
        summary = toolchain_rehearsal._build_summary(
            impl_request_id="REQ_P22_001",
            compile_status="passed",
            backtest_readiness_status="passed",
            readiness_review_packet_path="/tmp/packet.json",
        )
        self.assertNotIn("proposed_next_action", summary)

    def test_rehearsal_summary_lacks_lifecycle_proposal(self):
        summary = toolchain_rehearsal._build_summary(
            impl_request_id="REQ_P22_001",
            compile_status="passed",
            backtest_readiness_status="passed",
            readiness_review_packet_path="/tmp/packet.json",
        )
        self.assertNotIn("lifecycle_proposal", summary)

    def test_readiness_review_has_correct_artifact_type(self):
        packet = readiness_review.build_readiness_review_packet(
            strategy_id="strat_p22",
            version="v1",
            compile_evidence=self._make_minimal_compile_evidence(),
            backtest_readiness_evidence=self._make_minimal_bt_evidence(),
        )
        self.assertEqual(packet.get("artifact_type"), "generated_readiness_review")

    def test_rehearsal_summary_has_correct_artifact_type(self):
        summary = toolchain_rehearsal._build_summary(
            impl_request_id="REQ_P22_001",
            compile_status="passed",
        )
        self.assertEqual(summary.get("artifact_type"), "real_toolchain_rehearsal_summary")


# ---------------------------------------------------------------------------
# WP 22D — Artifact rejection / non-eligibility tests
# ---------------------------------------------------------------------------

class ResearchPhase22ArtifactRejectionTests(unittest.TestCase):
    """Readiness/rehearsal artifact types must not be accepted as
    baseline / robustness / final-holdout / candidate / approval types.
    """

    def test_generated_readiness_review_not_candidate_packet_type(self):
        self.assertNotIn("generated_readiness_review", ARTIFACT_TYPES)
        self.assertIn("generated_candidate_decision_packet", ARTIFACT_TYPES)

    def test_real_toolchain_rehearsal_summary_not_candidate_packet_type(self):
        self.assertNotIn("real_toolchain_rehearsal_summary", ARTIFACT_TYPES)
        self.assertIn("generated_candidate_decision_packet", ARTIFACT_TYPES)

    def test_real_backtest_readiness_not_baseline_review_type(self):
        self.assertNotIn("real_backtest_readiness", ARTIFACT_TYPES)
        self.assertIn("generated_baseline_review", ARTIFACT_TYPES)

    def test_real_backtest_readiness_not_final_holdout_review_type(self):
        self.assertNotIn("real_backtest_readiness", ARTIFACT_TYPES)
        self.assertIn("generated_final_holdout_review", ARTIFACT_TYPES)

    def test_readiness_modes_not_in_artifact_types(self):
        for mode in ("real_compile", "real_backtest_readiness"):
            self.assertNotIn(mode, ARTIFACT_TYPES)

    def test_readiness_review_proposed_action_not_in_candidate_allowed(self):
        """manual_review_only (from readiness) must not be in candidate's
        ALLOWED_PROPOSED_NEXT_ACTIONS."""
        import automated.research.generated_candidate as gc
        self.assertNotIn("manual_review_only", gc.ALLOWED_PROPOSED_NEXT_ACTIONS)

    def test_compile_evidence_not_accepted_as_approval_evidence(self):
        """Approval code does not reference compile evidence."""
        source = _module_source("contracts")
        self.assertFalse(_imported_in_source(source, "compile"))
        self.assertFalse(_imported_in_source(source, "compiler"))

    def test_readiness_types_excluded_from_registry_attachment(self):
        """All four readiness types are absent from ARTIFACT_TYPES, preventing
        registry attachment via _validate_artifact_type."""
        readiness_types = {
            "real_compile",
            "real_backtest_readiness",
            "generated_readiness_review",
            "real_toolchain_rehearsal_summary",
        }
        for rt in readiness_types:
            with self.subTest(artifact_type=rt):
                self.assertNotIn(rt, ARTIFACT_TYPES)

    def test_readiness_artifact_cannot_be_loaded_as_baseline_review(self):
        """load_review_artifact filters by artifact_type; a readiness packet
        would never match a baseline review lookup.  Schema-level assertion."""
        import automated.research.generated_baseline as gb
        source = _module_source("generated_baseline")
        self.assertNotIn("generated_readiness_review", source)
        self.assertNotIn("readiness_review", source)


# ---------------------------------------------------------------------------
# WP 22F — Deny-list terms
# ---------------------------------------------------------------------------

class ResearchPhase22DenyListTests(unittest.TestCase):
    """promote_to_production, production_candidate, live_trading_candidate
    must not appear in ALLOWED_* sets anywhere."""

    def _allowed_set_attrs(self, mod) -> list[set]:
        return [
            getattr(mod, attr)
            for attr in dir(mod)
            if attr.startswith("ALLOWED_") and isinstance(getattr(mod, attr), (set, frozenset))
        ]

    def test_deny_terms_not_in_readiness_review_allowed(self):
        sets = self._allowed_set_attrs(readiness_review)
        for s in sets:
            for term in DENY_TERMS:
                self.assertNotIn(term, s, f"{term} found in readiness_review.ALLOWED_* set")

    def test_deny_terms_not_in_compiler_allowed(self):
        sets = self._allowed_set_attrs(compiler)
        for s in sets:
            for term in DENY_TERMS:
                self.assertNotIn(term, s)

    def test_deny_terms_not_in_backtest_readiness_allowed(self):
        sets = self._allowed_set_attrs(backtest_readiness)
        for s in sets:
            for term in DENY_TERMS:
                self.assertNotIn(term, s)

    def test_deny_terms_not_in_toolchain_rehearsal_allowed(self):
        sets = self._allowed_set_attrs(toolchain_rehearsal)
        for s in sets:
            for term in DENY_TERMS:
                self.assertNotIn(term, s)

    def test_deny_terms_in_forbidden_action_values(self):
        """The three terms are legitimately in FORBIDDEN_ACTION_VALUES (a
        deny list), not in any ALLOWED_* set."""
        fbv = getattr(readiness_review, "FORBIDDEN_ACTION_VALUES", set())
        for term in DENY_TERMS:
            self.assertIn(term, fbv,
                          f"{term} should appear in FORBIDDEN_ACTION_VALUES "
                          "(deny list, not an allowed value)")

    def test_deny_terms_not_in_readiness_review_allowed_manual_actions(self):
        for term in DENY_TERMS:
            self.assertNotIn(term, readiness_review.ALLOWED_MANUAL_ACTIONS)

    def test_deny_terms_not_in_generated_candidate_allowed(self):
        import automated.research.generated_candidate as gc
        sets = self._allowed_set_attrs(gc)
        for s in sets:
            for term in DENY_TERMS:
                self.assertNotIn(term, s, f"{term} in generated_candidate.ALLOWED_*")

    def test_deny_terms_not_in_generated_baseline_allowed(self):
        import automated.research.generated_baseline as gb
        sets = self._allowed_set_attrs(gb)
        for s in sets:
            for term in DENY_TERMS:
                self.assertNotIn(term, s)

    def test_deny_terms_not_in_generated_robustness_allowed(self):
        import automated.research.generated_robustness as gr
        sets = self._allowed_set_attrs(gr)
        for s in sets:
            for term in DENY_TERMS:
                self.assertNotIn(term, s)

    def test_deny_terms_not_in_generated_final_holdout_allowed(self):
        import automated.research.generated_final_holdout as gf
        sets = self._allowed_set_attrs(gf)
        for s in sets:
            for term in DENY_TERMS:
                self.assertNotIn(term, s)

    def test_deny_terms_not_in_lifecycle_allowed(self):
        import automated.research.lifecycle as lc
        sets = self._allowed_set_attrs(lc)
        for s in sets:
            for term in DENY_TERMS:
                self.assertNotIn(term, s)

    def test_deny_terms_not_in_contracts_allowed(self):
        import automated.research.contracts as ct
        sets = self._allowed_set_attrs(ct)
        for s in sets:
            for term in DENY_TERMS:
                self.assertNotIn(term, s)

    def test_deny_terms_not_in_queue_allowed(self):
        import automated.research.queue as qu
        sets = self._allowed_set_attrs(qu)
        for s in sets:
            for term in DENY_TERMS:
                self.assertNotIn(term, s)


if __name__ == "__main__":
    unittest.main()
