from __future__ import annotations

import unittest
from pathlib import Path

from automated.research.schemas import REPO_ROOT

RESEARCH_DOCS_DIR = REPO_ROOT / "automated" / "research" / "docs"


def _read_doc(filename: str) -> str:
    path = RESEARCH_DOCS_DIR / filename
    return path.read_text(encoding="utf-8")


class ResearchPhase17RunbookTests(unittest.TestCase):
    """Doc-existence and safety-language tests for generated_strategy_operator_runbook.md."""

    def setUp(self):
        self.content = _read_doc("generated_strategy_operator_runbook.md")

    def test_runbook_mentions_compile_check_then_diff_review(self):
        self.assertIn("Compile-check", self.content)
        self.assertIn("Diff-review", self.content)
        ci = self.content.index("Compile-check")
        dr = self.content.index("Diff-review")
        self.assertLess(ci, dr, "Compile-check must appear before Diff-review in the runbook")

    def test_runbook_warns_against_parallel_compile_and_diff(self):
        self.assertIn("Do not run compile-check and diff-review in parallel", self.content)
        # Text spans a newline: "Running them\nin parallel"
        self.assertIn("Running them", self.content)
        self.assertIn("in parallel", self.content)

    def test_runbook_contains_request_human_review_for_final_holdout(self):
        self.assertIn("request_human_review_for_final_holdout", self.content)

    def test_runbook_contains_final_holdout_candidate(self):
        self.assertIn("final_holdout_candidate", self.content)

    def test_runbook_distinguishes_baseline_from_final_holdout_approval(self):
        self.assertIn("Baseline approval and final holdout approval are distinct scopes", self.content)

    def test_runbook_says_baseline_approval_no_separate_approval_id(self):
        # Text spans a newline: "does not emit a\nseparate `approval_id`"
        self.assertIn("does not emit a", self.content)
        self.assertIn("separate `approval_id`", self.content)

    def test_runbook_says_final_holdout_approval_emits_approval_id(self):
        self.assertIn("emits an `approval_id`", self.content)


class ResearchPhase17ReadinessChecklistTests(unittest.TestCase):
    """Doc-existence and safety-language tests for real_run_readiness_checklist.md."""

    def setUp(self):
        self.path = RESEARCH_DOCS_DIR / "real_run_readiness_checklist.md"
        self.assertTrue(self.path.is_file(), "real_run_readiness_checklist.md must exist")
        self.content = self.path.read_text(encoding="utf-8")

    def test_readiness_checklist_forbids_writes_to_automated_strategies(self):
        self.assertIn("automated/strategies/", self.content)

    def test_readiness_checklist_forbids_lifecycle_transition(self):
        self.assertIn("No lifecycle transition", self.content)

    def test_readiness_checklist_forbids_production_promotion(self):
        self.assertIn("No production, live-trading, or promote-to-production candidate is proposed", self.content)

    def test_readiness_checklist_forbids_live_trading(self):
        self.assertIn("No live trading is performed", self.content)

    def test_readiness_checklist_forbids_dataset_cost_symbol_timeframe_mutation(self):
        self.assertIn("No dataset, cost, symbol, or timeframe is mutated", self.content)

    def test_readiness_checklist_forbids_runner_replacement(self):
        self.assertIn("No runner replacement occurs", self.content)

    def test_readiness_checklist_links_to_real_compile_verification(self):
        self.assertIn("real_compile_verification.md", self.content)


class ResearchPhase17DryRunCleanupTests(unittest.TestCase):
    """Doc-existence and safety-language tests for dry_run_artifact_cleanup.md."""

    def setUp(self):
        self.path = RESEARCH_DOCS_DIR / "dry_run_artifact_cleanup.md"
        self.assertTrue(self.path.is_file(), "dry_run_artifact_cleanup.md must exist")
        self.content = self.path.read_text(encoding="utf-8")

    def test_cleanup_contains_phase16_dry_run_id(self):
        self.assertIn("DRYRUN_PHASE16_20260513T143641Z", self.content)

    def test_cleanup_lists_automated_strategies_as_forbidden(self):
        self.assertIn("automated/strategies/", self.content)

    def test_cleanup_says_future_command_must_default_to_preview(self):
        self.assertIn("default to dry-run/preview mode", self.content)

    def test_cleanup_says_future_command_must_require_explicit_strategy_id(self):
        self.assertIn("require an explicit strategy ID", self.content)

    def test_cleanup_says_future_command_must_refuse_broad_globs(self):
        self.assertIn("refuse broad glob patterns", self.content)


class ResearchPhase17RealCompileVerificationTests(unittest.TestCase):
    """Doc-existence and safety-language tests for real_compile_verification.md."""

    def setUp(self):
        self.path = RESEARCH_DOCS_DIR / "real_compile_verification.md"
        self.assertTrue(self.path.is_file(), "real_compile_verification.md must exist")
        self.content = self.path.read_text(encoding="utf-8")

    def test_compile_verification_scope_is_compile_only(self):
        self.assertIn("Real compile verification", self.content)
        self.assertIn("sandbox", self.content)
        self.assertIn("checks that generated sandbox", self.content)

    def test_compile_verification_allows_call_compiler(self):
        self.assertIn("call configured compiler", self.content)

    def test_compile_verification_allows_collect_logs(self):
        self.assertIn("collect compiler logs", self.content)

    def test_compile_verification_forbids_write_to_automated_strategies(self):
        self.assertIn("automated/strategies/", self.content)

    def test_compile_verification_forbids_runner_replacement(self):
        self.assertIn("replace the runner", self.content)

    def test_compile_verification_forbids_dataset_cost_symbol_timeframe_mutation(self):
        self.assertIn("mutate dataset, cost, symbol, timeframe, or validation thresholds", self.content)

    def test_compile_verification_forbids_lifecycle_transitions(self):
        self.assertIn("lifecycle transitions", self.content)

    def test_compile_verification_forbids_production_live_promotion_proposals(self):
        self.assertIn("propose production/live/promotion actions", self.content)

    def test_compile_verification_forbids_trades(self):
        self.assertIn("place trades", self.content)

    def test_compile_verification_sequence_stops_for_manual_review(self):
        self.assertIn("Stop for manual review", self.content)


class ResearchPhase17DenyListTests(unittest.TestCase):
    """Safety test: forbidden proposal/action values must not appear as allowed values."""

    def setUp(self):
        self.all_docs_content = ""
        for fname in ["generated_strategy_operator_runbook.md"]:
            self.all_docs_content += _read_doc(fname)

    def test_promote_to_production_not_in_runbook_as_allowed_value(self):
        content = _read_doc("generated_strategy_operator_runbook.md")
        if "promote_to_production" in content:
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                if "promote_to_production" in line:
                    self.fail(
                        f"promote_to_production found in runbook at line {i}: {line.strip()}"
                    )

    def test_production_candidate_not_in_runbook_as_allowed_value(self):
        content = _read_doc("generated_strategy_operator_runbook.md")
        if "production_candidate" in content:
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                if "production_candidate" in line:
                    self.fail(
                        f"production_candidate found in runbook at line {i}: {line.strip()}"
                    )

    def test_live_trading_candidate_not_in_runbook_as_allowed_value(self):
        content = _read_doc("generated_strategy_operator_runbook.md")
        if "live_trading_candidate" in content:
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                if "live_trading_candidate" in line:
                    self.fail(
                        f"live_trading_candidate found in runbook at line {i}: {line.strip()}"
                    )


class ResearchPhase17RealRunReadinessSafetyLanguageTests(unittest.TestCase):
    """Additional safety language checks in real_run_readiness_checklist.md."""

    def setUp(self):
        self.path = RESEARCH_DOCS_DIR / "real_run_readiness_checklist.md"
        self.content = self.path.read_text(encoding="utf-8")

    def test_no_promote_to_production_in_checklist(self):
        self.assertNotIn("promote_to_production", self.content)

    def test_no_production_candidate_in_checklist(self):
        self.assertNotIn("production_candidate", self.content)

    def test_no_live_trading_candidate_in_checklist(self):
        self.assertNotIn("live_trading_candidate", self.content)


if __name__ == "__main__":
    unittest.main()
