from __future__ import annotations

import importlib
import unittest
from pathlib import Path

from automated.research import (
    agent_workspace,
    campaign_analysis,
    campaign_planner,
    edge_library,
    hypothesis_mutation,
    similarity,
)
from automated.research.contracts import (
    AGENT_WORKSPACE_ARTIFACT_TYPES,
    ARTIFACT_TYPES,
    GENERATED_BASELINE_ARTIFACT_TYPES,
    GENERATED_CANDIDATE_ARTIFACT_TYPES,
    GENERATED_FINAL_HOLDOUT_ARTIFACT_TYPES,
    GENERATED_ROBUSTNESS_ARTIFACT_TYPES,
    HYPOTHESIS_MUTATION_ARTIFACT_TYPES,
    RESEARCH_CAMPAIGN_ANALYSIS_ARTIFACT_TYPES,
    RESEARCH_CAMPAIGN_PLANNING_ARTIFACT_TYPES,
    RESEARCH_LIBRARY_ARTIFACT_TYPES,
    STRATEGY_SIMILARITY_ARTIFACT_TYPES,
)
from automated.research.schemas import REPO_ROOT
from tests.research_test_helpers import (
    FORBIDDEN_VALUES,
    agent_workspace_bundle,
    campaign_analysis_record,
    generated_hypothesis_batch,
    valid_campaign_config,
    valid_edge_thesis,
    valid_mutation_recipe,
    valid_research_source,
)


NEW_RESEARCH_MODULES = {
    "edge_library": edge_library,
    "hypothesis_mutation": hypothesis_mutation,
    "campaign_planner": campaign_planner,
    "similarity": similarity,
    "campaign_analysis": campaign_analysis,
    "agent_workspace": agent_workspace,
}

ADVISORY_ARTIFACT_GROUPS = [
    RESEARCH_LIBRARY_ARTIFACT_TYPES,
    HYPOTHESIS_MUTATION_ARTIFACT_TYPES,
    RESEARCH_CAMPAIGN_PLANNING_ARTIFACT_TYPES,
    STRATEGY_SIMILARITY_ARTIFACT_TYPES,
    RESEARCH_CAMPAIGN_ANALYSIS_ARTIFACT_TYPES,
    AGENT_WORKSPACE_ARTIFACT_TYPES,
]

AUTHORITY_EVIDENCE_GROUPS = [
    GENERATED_BASELINE_ARTIFACT_TYPES,
    GENERATED_ROBUSTNESS_ARTIFACT_TYPES,
    GENERATED_CANDIDATE_ARTIFACT_TYPES,
    GENERATED_FINAL_HOLDOUT_ARTIFACT_TYPES,
]

DOC_NON_GOALS = {
    "edge_thesis_library.md": [
        "No implementation generation",
        "No baseline approval",
        "No final-holdout approval",
        "No lifecycle apply",
        "No production authority",
        "No live trading authority",
    ],
    "hypothesis_mutation_engine.md": [
        "No code generation",
        "No baseline approval",
        "No final holdout approval",
        "No lifecycle apply",
        "No production authority",
        "No live trading authority",
    ],
    "generated_research_campaign_planner.md": [
        "No automatic baseline",
        "No automatic final holdout",
        "No lifecycle apply",
        "No production authority",
        "No live trading authority",
    ],
    "generated_strategy_similarity.md": [
        "Not a pass/fail validation gate",
        "No approval authority",
        "No lifecycle authority",
        "No production authority",
        "No live trading authority",
    ],
    "generated_research_campaign_meta_analysis.md": [
        "No production scoring",
        "No automatic approval",
        "No lifecycle apply",
        "No live trading authority",
    ],
    "agent_workspace_protocol.md": [
        "No new execution authority",
        "No new queue execution authority",
        "No lifecycle apply",
        "No baseline approval",
        "No final-holdout approval",
        "No production behavior",
        "No live trading behavior",
    ],
}


class ResearchPhase32CanonicalInvariantTests(unittest.TestCase):
    def test_advisory_artifact_types_are_registered(self):
        for group in ADVISORY_ARTIFACT_GROUPS:
            self.assertTrue(group.issubset(ARTIFACT_TYPES))

    def test_advisory_artifact_types_do_not_overlap_authority_evidence(self):
        authority_types = set().union(*AUTHORITY_EVIDENCE_GROUPS)
        for group in ADVISORY_ARTIFACT_GROUPS:
            self.assertTrue(group.isdisjoint(authority_types))

    def test_advisory_artifact_types_are_not_queue_execution_tasks(self):
        queue = importlib.import_module("automated.research.queue")
        advisory_types = set().union(*ADVISORY_ARTIFACT_GROUPS)
        self.assertTrue(advisory_types.isdisjoint(queue.TASK_TYPES))
        for forbidden_task in [
            "research_campaign_execution",
            "agent_workspace_import",
            "generated_hypothesis_execution",
        ]:
            self.assertNotIn(forbidden_task, queue.TASK_TYPES)

    def test_forbidden_values_not_in_allowed_sets(self):
        module_names = ["contracts", *NEW_RESEARCH_MODULES.keys()]
        for module_name in module_names:
            module = importlib.import_module(f"automated.research.{module_name}")
            for attr in dir(module):
                if not attr.startswith("ALLOWED_"):
                    continue
                value = getattr(module, attr)
                if isinstance(value, (set, frozenset)):
                    self.assertTrue(FORBIDDEN_VALUES.isdisjoint(value), f"{module_name}.{attr}")

    def test_forbidden_values_not_in_queue_task_types(self):
        queue = importlib.import_module("automated.research.queue")
        self.assertTrue(FORBIDDEN_VALUES.isdisjoint(queue.TASK_TYPES))

    def test_new_modules_do_not_call_authority_or_execution_paths(self):
        blocked_snippets = [
            "apply_transition",
            "approve_for_baseline",
            "create_scope_approval",
            "generated_baseline_experiment",
            "generated_final_holdout_experiment",
            "run_backtest",
        ]
        for module_name, module in NEW_RESEARCH_MODULES.items():
            source = Path(module.__file__).read_text(encoding="utf-8")
            for snippet in blocked_snippets:
                self.assertNotIn(snippet, source, f"{module_name} contains {snippet}")

    def test_research_artifacts_cannot_satisfy_candidate_packet_evidence(self):
        candidate = importlib.import_module("automated.research.generated_candidate")
        source = Path(candidate.__file__).read_text(encoding="utf-8")
        advisory_types = set().union(*ADVISORY_ARTIFACT_GROUPS)
        for artifact_type in advisory_types:
            self.assertNotIn(f'"{artifact_type}"', source)
            self.assertNotIn(f"'{artifact_type}'", source)

    def test_authority_flags_remain_false_in_representative_reports(self):
        edge_report = edge_library.build_edge_thesis_extraction_report(valid_research_source(), [valid_edge_thesis()])
        batch = hypothesis_mutation.build_generated_hypothesis_batch(valid_edge_thesis(), valid_mutation_recipe())
        plan = campaign_planner.build_campaign_plan(valid_campaign_config(), [generated_hypothesis_batch()])
        sim_report = similarity.build_similarity_report(batch["hypotheses"][:1], "hypothesis", similarity.DEFAULT_THRESHOLDS)
        campaign_report = campaign_analysis.build_generated_research_campaign_report({"records": [campaign_analysis_record()]})
        workspace_report = agent_workspace.validate_agent_output_bundle(agent_workspace_bundle())

        for report in [edge_report, batch, plan, sim_report, campaign_report, workspace_report]:
            authority = report.get("authority", {})
            for key, value in authority.items():
                if key.endswith("_authority") or key in {"execution_authority", "queue_execution_authority"}:
                    self.assertFalse(value, f"{report.get('artifact_type')} {key}")

    def test_docs_keep_non_goals(self):
        docs_dir = REPO_ROOT / "automated" / "research" / "docs"
        for filename, required_phrases in DOC_NON_GOALS.items():
            text = (docs_dir / filename).read_text(encoding="utf-8")
            for phrase in required_phrases:
                self.assertIn(phrase, text, filename)


if __name__ == "__main__":
    unittest.main()
