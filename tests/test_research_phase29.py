from __future__ import annotations

import unittest

from automated.research import hypothesis_mutation, similarity
from tests.research_test_helpers import valid_edge_thesis as valid_thesis, valid_mutation_recipe as valid_recipe


THRESHOLDS = {
    "duplicate": 0.95,
    "near_variant": 0.75,
    "same_family_different_expression": 0.50,
}


def hypothesis(**overrides) -> dict:
    batch = hypothesis_mutation.build_generated_hypothesis_batch(valid_thesis(), valid_recipe())
    item = dict(batch["hypotheses"][0])
    item.update(overrides)
    return item


class ResearchPhase29SimilarityScoringTests(unittest.TestCase):
    def test_identical_records_score_one_or_near_one(self):
        item = valid_thesis()
        result = similarity.score_edge_thesis_similarity(item, dict(item))
        self.assertGreaterEqual(result["score"], 0.99)

    def test_unrelated_records_score_low(self):
        left = valid_thesis(edge_family="volatility_expansion")
        right = valid_thesis(
            edge_id="EDGE_MEAN_REVERSION_001",
            edge_family="mean_reversion",
            mechanism="Overextended price may revert after exhaustion.",
            testable_prediction="Extreme z-scores should mean revert.",
            mutation_axes=["zscore_window"],
            asset_classes=["rates"],
            market_regimes=["calm"],
            failure_modes=["trend continuation"],
        )
        result = similarity.score_edge_thesis_similarity(left, right)
        self.assertLess(result["score"], 0.50)

    def test_same_edge_family_with_different_implementation_scores_medium(self):
        left = hypothesis(exit_logic_summary="exit_model=fixed_r", risk_model_summary="stop_model=atr_multiple")
        right = hypothesis(
            hypothesis_id="HYP_GEN_OTHER",
            exit_logic_summary="exit_model=time_stop",
            risk_model_summary="stop_model=structure_swing",
        )
        result = similarity.score_hypothesis_similarity(left, right)
        self.assertGreaterEqual(result["score"], 0.50)
        self.assertLess(result["score"], 0.95)

    def test_token_normalization_deterministic(self):
        value = {"B": ["Hello World", "XAUUSD"], "A": "H1"}
        self.assertEqual(
            similarity.normalize_similarity_tokens(value),
            similarity.normalize_similarity_tokens({"A": "H1", "B": ["Hello World", "XAUUSD"]}),
        )

    def test_empty_fields_do_not_crash(self):
        self.assertEqual(similarity.weighted_jaccard({}, {}, {"missing": 1.0}), 0.0)


class ResearchPhase29ClassificationTests(unittest.TestCase):
    def test_duplicate_threshold(self):
        self.assertEqual(similarity.classify_similarity(0.96, THRESHOLDS), "duplicate")

    def test_near_variant_threshold(self):
        self.assertEqual(similarity.classify_similarity(0.80, THRESHOLDS), "near_variant")

    def test_same_family_threshold(self):
        self.assertEqual(similarity.classify_similarity(0.55, THRESHOLDS), "same_family_different_expression")

    def test_below_threshold_classified_different_family(self):
        self.assertEqual(similarity.classify_similarity(0.20, THRESHOLDS), "different_family")

    def test_thresholds_configurable(self):
        self.assertEqual(
            similarity.classify_similarity(0.70, {"duplicate": 0.99, "near_variant": 0.65, "same_family_different_expression": 0.30}),
            "near_variant",
        )


class ResearchPhase29ClusterTests(unittest.TestCase):
    def test_deterministic_cluster_ids(self):
        items = [hypothesis(hypothesis_id="HYP_GEN_B"), hypothesis(hypothesis_id="HYP_GEN_A")]
        left = similarity.cluster_items_by_similarity(items, similarity.score_hypothesis_similarity, THRESHOLDS)
        right = similarity.cluster_items_by_similarity(list(reversed(items)), similarity.score_hypothesis_similarity, THRESHOLDS)
        self.assertEqual([c["cluster_id"] for c in left["clusters"]], [c["cluster_id"] for c in right["clusters"]])

    def test_order_independent_clustering(self):
        a = hypothesis(hypothesis_id="HYP_GEN_A")
        b = dict(a, hypothesis_id="HYP_GEN_B")
        left = similarity.cluster_items_by_similarity([a, b], similarity.score_hypothesis_similarity, THRESHOLDS)
        right = similarity.cluster_items_by_similarity([b, a], similarity.score_hypothesis_similarity, THRESHOLDS)
        self.assertEqual(left["clusters"][0]["member_ids"], right["clusters"][0]["member_ids"])

    def test_duplicates_cluster_together(self):
        a = hypothesis(hypothesis_id="HYP_GEN_A")
        b = dict(a, hypothesis_id="HYP_GEN_B")
        report = similarity.cluster_items_by_similarity([a, b], similarity.score_hypothesis_similarity, THRESHOLDS)
        self.assertEqual(report["cluster_count"], 1)

    def test_different_families_separate(self):
        a = hypothesis(hypothesis_id="HYP_GEN_A")
        b = hypothesis(
            hypothesis_id="HYP_GEN_B",
            strategy_family="mean_reversion",
            entry_logic_summary="zscore fade",
            exit_logic_summary="mean touch",
            risk_model_summary="tight stop",
            mutation_axes={"zscore": "extreme"},
        )
        report = similarity.cluster_items_by_similarity([a, b], similarity.score_hypothesis_similarity, THRESHOLDS)
        self.assertEqual(report["cluster_count"], 2)

    def test_cluster_summaries_include_representative_item(self):
        report = similarity.cluster_items_by_similarity([hypothesis()], similarity.score_hypothesis_similarity, THRESHOLDS)
        self.assertIn("representative_item", report["clusters"][0])


class ResearchPhase29DiversityBudgetTests(unittest.TestCase):
    def test_duplicates_capped(self):
        a = hypothesis(hypothesis_id="HYP_GEN_A")
        b = dict(a, hypothesis_id="HYP_GEN_B")
        sim_report = similarity.build_similarity_report([a, b], "hypothesis", THRESHOLDS)
        diversity = similarity.build_diversity_report(sim_report, {"max_duplicates": 1})
        self.assertEqual(diversity["summary"]["rejected_duplicate"], 1)

    def test_near_variants_allowed_up_to_cap(self):
        items = [
            hypothesis(hypothesis_id="HYP_GEN_A", exit_logic_summary="exit_model=fixed_r"),
            hypothesis(hypothesis_id="HYP_GEN_B", exit_logic_summary="exit_model=atr_trail"),
        ]
        sim_report = similarity.build_similarity_report(items, "hypothesis", THRESHOLDS)
        diversity = similarity.build_diversity_report(sim_report, {"max_near_variants_per_cluster": 2})
        self.assertGreaterEqual(diversity["summary"]["kept_with_cap"], 1)

    def test_overflow_near_variants_deprioritized(self):
        items = [
            hypothesis(hypothesis_id=f"HYP_GEN_{idx}", exit_logic_summary=f"exit_model=variant_{idx}")
            for idx in range(5)
        ]
        sim_report = similarity.build_similarity_report(items, "hypothesis", {"duplicate": 0.99, "near_variant": 0.40, "same_family_different_expression": 0.20})
        diversity = similarity.build_diversity_report(sim_report, {"max_near_variants_per_cluster": 1})
        self.assertGreater(diversity["summary"]["deprioritized"], 0)

    def test_report_records_reasons(self):
        a = hypothesis(hypothesis_id="HYP_GEN_A")
        b = dict(a, hypothesis_id="HYP_GEN_B")
        sim_report = similarity.build_similarity_report([a, b], "hypothesis", THRESHOLDS)
        diversity = similarity.build_diversity_report(sim_report, {"max_duplicates": 1})
        self.assertTrue(all("reason" in decision for decision in diversity["decisions"]))

    def test_similarity_not_automatic_failure_unless_policy_says_so(self):
        item = hypothesis()
        sim_report = similarity.build_similarity_report([item], "hypothesis", THRESHOLDS)
        diversity = similarity.build_diversity_report(sim_report, {})
        self.assertEqual(diversity["summary"]["kept"], 1)


if __name__ == "__main__":
    unittest.main()
