from __future__ import annotations

import unittest

from automated.research import hypothesis_mutation
from automated.research.schemas import SchemaValidationError
from tests.research_test_helpers import (
    valid_edge_thesis as valid_thesis,
    valid_mutation_recipe as valid_recipe,
    write_yaml_temp,
)


def _write_yaml(data: dict):
    return write_yaml_temp(data, filename="recipe.yaml")


class ResearchPhase27MutationRecipeTests(unittest.TestCase):
    def test_valid_recipe_loads(self):
        loaded = hypothesis_mutation.load_mutation_recipe(_write_yaml(valid_recipe()))
        self.assertEqual(loaded["recipe_id"], "MUT_VOL_BREAKOUT_001")

    def test_missing_fields_reject(self):
        recipe = valid_recipe()
        del recipe["axes"]
        with self.assertRaises(SchemaValidationError):
            hypothesis_mutation.validate_mutation_recipe(recipe)

    def test_unknown_mutation_axis_shape_rejects(self):
        recipe = valid_recipe(axes={"compression_measure": ["atr_percentile"]})
        with self.assertRaises(SchemaValidationError):
            hypothesis_mutation.validate_mutation_recipe(recipe)

    def test_budget_requires_max_hypotheses(self):
        recipe = valid_recipe()
        del recipe["mutation_budget"]["max_hypotheses"]
        with self.assertRaises(SchemaValidationError):
            hypothesis_mutation.validate_mutation_recipe(recipe)

    def test_max_per_similarity_cluster_must_be_positive(self):
        recipe = valid_recipe(mutation_budget={"max_hypotheses": 5, "max_per_similarity_cluster": 0})
        with self.assertRaises(SchemaValidationError):
            hypothesis_mutation.validate_mutation_recipe(recipe)

    def test_recipe_edge_id_must_match_edge_thesis_before_generation(self):
        with self.assertRaises(SchemaValidationError):
            hypothesis_mutation.generate_mutation_signatures(
                valid_thesis(edge_id="EDGE_OTHER_001"),
                valid_recipe(),
            )


class ResearchPhase27HypothesisGenerationTests(unittest.TestCase):
    def test_generated_batch_has_artifact_type(self):
        batch = hypothesis_mutation.build_generated_hypothesis_batch(valid_thesis(), valid_recipe())
        self.assertEqual(batch["artifact_type"], "generated_hypothesis_batch")

    def test_every_hypothesis_has_source_edge_recipe_lineage(self):
        batch = hypothesis_mutation.build_generated_hypothesis_batch(valid_thesis(), valid_recipe())
        self.assertTrue(batch["hypotheses"])
        for hypothesis in batch["hypotheses"]:
            self.assertEqual(hypothesis["lineage"]["source_ids"], ["SRC_CASE_VOL_BREAKOUT_001"])
            self.assertEqual(hypothesis["lineage"]["edge_id"], "EDGE_VOL_COMPRESSION_BREAKOUT_001")
            self.assertEqual(hypothesis["lineage"]["recipe_id"], "MUT_VOL_BREAKOUT_001")
            self.assertIn("mutation_signature", hypothesis["lineage"])

    def test_hypotheses_get_stable_ids_and_signatures(self):
        left = hypothesis_mutation.build_generated_hypothesis_batch(valid_thesis(), valid_recipe())
        right = hypothesis_mutation.build_generated_hypothesis_batch(valid_thesis(), valid_recipe())
        self.assertEqual(
            [h["hypothesis_id"] for h in left["hypotheses"]],
            [h["hypothesis_id"] for h in right["hypotheses"]],
        )
        self.assertEqual(
            [h["lineage"]["mutation_signature"] for h in left["hypotheses"]],
            [h["lineage"]["mutation_signature"] for h in right["hypotheses"]],
        )

    def test_no_generated_hypothesis_proposes_authority_action(self):
        batch = hypothesis_mutation.build_generated_hypothesis_batch(valid_thesis(), valid_recipe())
        forbidden_keys = {"proposed_next_action", "lifecycle_proposal", "approval_status", "baseline_approval"}
        for hypothesis in batch["hypotheses"]:
            self.assertTrue(forbidden_keys.isdisjoint(hypothesis))

    def test_invalidation_rules_are_present(self):
        batch = hypothesis_mutation.build_generated_hypothesis_batch(valid_thesis(), valid_recipe())
        self.assertTrue(all(h["invalidation_rule"] for h in batch["hypotheses"]))

    def test_candidate_symbols_timeframes_are_inherited_not_expanded(self):
        thesis = valid_thesis(candidate_symbols=["XAUUSD"], candidate_timeframes=["H1"])
        batch = hypothesis_mutation.build_generated_hypothesis_batch(thesis, valid_recipe())
        for hypothesis in batch["hypotheses"]:
            self.assertEqual(hypothesis["candidate_symbols"], ["XAUUSD"])
            self.assertEqual(hypothesis["candidate_timeframes"], ["H1"])


class ResearchPhase27SimilarityBudgetTests(unittest.TestCase):
    def _hypothesis(self, **overrides) -> dict:
        base = hypothesis_mutation.build_generated_hypothesis(valid_thesis(), valid_recipe(), hypothesis_mutation.generate_mutation_signatures(valid_thesis(), valid_recipe())[0])
        base.update(overrides)
        return base

    def test_identical_hypotheses_cluster_together(self):
        h1 = self._hypothesis(hypothesis_id="HYP_GEN_A")
        h2 = dict(h1, hypothesis_id="HYP_GEN_B")
        clustered = hypothesis_mutation.assign_similarity_clusters([h1, h2], 0.95)
        self.assertEqual(clustered[0]["similarity_cluster_id"], clustered[1]["similarity_cluster_id"])

    def test_near_variants_cluster_together(self):
        h1 = self._hypothesis(hypothesis_id="HYP_GEN_A")
        h2 = self._hypothesis(hypothesis_id="HYP_GEN_B", exit_logic_summary="exit_model=atr_trail")
        clustered = hypothesis_mutation.assign_similarity_clusters([h1, h2], 0.70)
        self.assertEqual(clustered[0]["similarity_cluster_id"], clustered[1]["similarity_cluster_id"])

    def test_unrelated_hypotheses_do_not_cluster(self):
        h1 = self._hypothesis(hypothesis_id="HYP_GEN_A")
        h2 = self._hypothesis(
            hypothesis_id="HYP_GEN_B",
            strategy_family="mean_reversion",
            entry_logic_summary="zscore fade",
            exit_logic_summary="mean touch",
            risk_model_summary="tight stop",
            mutation_axes={"mean_reversion": "zscore"},
        )
        clustered = hypothesis_mutation.assign_similarity_clusters([h1, h2], 0.72)
        self.assertNotEqual(clustered[0]["similarity_cluster_id"], clustered[1]["similarity_cluster_id"])

    def test_cluster_cap_keeps_only_max_per_similarity_cluster(self):
        hypotheses = [
            self._hypothesis(hypothesis_id=f"HYP_GEN_{idx}", similarity_cluster_id="SIM_001", cluster_rank=idx)
            for idx in range(1, 5)
        ]
        result = hypothesis_mutation.apply_similarity_budget(hypotheses, 2)
        self.assertEqual(len(result["accepted"]), 2)
        self.assertEqual(len(result["capped_by_similarity_budget"]), 2)

    def test_capped_variants_are_reported_in_screening_report(self):
        batch = hypothesis_mutation.build_generated_hypothesis_batch(valid_thesis(), valid_recipe())
        report = hypothesis_mutation.build_hypothesis_screening_report(batch)
        self.assertEqual(
            len(report["capped_by_similarity_hypotheses"]),
            batch["screening_summary"]["capped_by_similarity_budget"],
        )

    def test_similarity_is_allowed_up_to_cap(self):
        recipe = valid_recipe(mutation_budget={"max_hypotheses": 4, "max_per_similarity_cluster": 2})
        batch = hypothesis_mutation.build_generated_hypothesis_batch(valid_thesis(), recipe)
        self.assertGreaterEqual(batch["screening_summary"]["accepted"], 2)


class ResearchPhase27ScreeningReportTests(unittest.TestCase):
    def test_report_includes_required_sections(self):
        batch = hypothesis_mutation.build_generated_hypothesis_batch(valid_thesis(), valid_recipe())
        report = hypothesis_mutation.build_hypothesis_screening_report(batch)
        self.assertEqual(report["artifact_type"], "hypothesis_screening_report")
        self.assertIn("accepted_hypotheses", report)
        self.assertIn("rejected_hypotheses", report)
        self.assertIn("capped_by_similarity_hypotheses", report)
        self.assertIn("warning_reasons", report)
        self.assertEqual(report["lineage_summary"]["edge_id"], "EDGE_VOL_COMPRESSION_BREAKOUT_001")

    def test_report_has_no_approval_or_lifecycle_fields(self):
        batch = hypothesis_mutation.build_generated_hypothesis_batch(valid_thesis(), valid_recipe())
        report = hypothesis_mutation.build_hypothesis_screening_report(batch)
        forbidden = {"approval_status", "approval_usage", "baseline_approval", "final_holdout_approval", "lifecycle_proposal"}
        self.assertTrue(forbidden.isdisjoint(report))


if __name__ == "__main__":
    unittest.main()
