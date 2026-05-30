from __future__ import annotations

import unittest

from automated.research import campaign_planner
from automated.research.schemas import SchemaValidationError
from tests.research_test_helpers import generated_hypothesis_batch, valid_campaign_config, write_yaml_temp


def _write_yaml(data: dict):
    return write_yaml_temp(data, filename="campaign.yaml")


def sample_batch() -> dict:
    return generated_hypothesis_batch()


class ResearchPhase28CampaignConfigTests(unittest.TestCase):
    def test_valid_campaign_loads(self):
        loaded = campaign_planner.load_campaign_config(_write_yaml(valid_campaign_config()))
        self.assertEqual(loaded["campaign_id"], "CAMP_VOL_EXPANSION_2026_05")

    def test_missing_campaign_id_rejects(self):
        config = valid_campaign_config()
        del config["campaign_id"]
        with self.assertRaises(SchemaValidationError):
            campaign_planner.validate_campaign_config(config)

    def test_missing_grid_rejects(self):
        config = valid_campaign_config()
        del config["asset_timeframe_grid"]
        with self.assertRaises(SchemaValidationError):
            campaign_planner.validate_campaign_config(config)

    def test_unknown_dataset_policy_rejects(self):
        config = valid_campaign_config()
        config["controls"]["dataset_policy"] = "auto_download"
        with self.assertRaises(SchemaValidationError):
            campaign_planner.validate_campaign_config(config)

    def test_unknown_runner_policy_rejects(self):
        config = valid_campaign_config()
        config["controls"]["runner_policy"] = "replace_runner"
        with self.assertRaises(SchemaValidationError):
            campaign_planner.validate_campaign_config(config)

    def test_budgets_must_be_positive(self):
        config = valid_campaign_config()
        config["budgets"]["max_total_planned_specs"] = 0
        with self.assertRaises(SchemaValidationError):
            campaign_planner.validate_campaign_config(config)

    def test_campaign_ids_use_camp_prefix(self):
        with self.assertRaises(SchemaValidationError):
            campaign_planner.validate_campaign_config(valid_campaign_config(campaign_id="VOL_EXPANSION_2026_05"))


class ResearchPhase28AssetTimeframeMatrixTests(unittest.TestCase):
    def test_matrix_includes_all_configured_pairs(self):
        matrix = campaign_planner.build_asset_timeframe_matrix(valid_campaign_config())
        pairs = {(item["symbol"], item["timeframe"]) for item in matrix["asset_timeframe_pairs"]}
        self.assertIn(("XAUUSD", "M30"), pairs)
        self.assertIn(("XAUUSD", "H4"), pairs)
        self.assertIn(("EURUSD", "D1"), pairs)

    def test_invalid_timeframe_rejects(self):
        config = valid_campaign_config(asset_timeframe_grid=[{"symbol": "XAUUSD", "timeframes": ["30m"]}])
        with self.assertRaises(SchemaValidationError):
            campaign_planner.build_asset_timeframe_matrix(config)

    def test_duplicate_asset_timeframe_rows_dedupe_deterministically(self):
        config = valid_campaign_config(
            asset_timeframe_grid=[
                {"symbol": "xauusd", "timeframes": ["H1", "H1"]},
                {"symbol": "XAUUSD", "timeframes": ["H1"]},
            ]
        )
        matrix = campaign_planner.build_asset_timeframe_matrix(config)
        self.assertEqual(matrix["asset_timeframe_pairs"], [{"symbol": "XAUUSD", "timeframe": "H1"}])

    def test_symbols_are_normalized_consistently(self):
        matrix = campaign_planner.build_asset_timeframe_matrix(valid_campaign_config())
        symbols = {item["symbol"] for item in matrix["asset_timeframe_pairs"]}
        self.assertIn("EURUSD", symbols)
        self.assertNotIn("eurusd", symbols)


class ResearchPhase28BudgetAndSimilarityTests(unittest.TestCase):
    def test_max_total_planned_specs_enforced(self):
        config = valid_campaign_config()
        config["budgets"]["max_total_planned_specs"] = 3
        plan = campaign_planner.build_campaign_plan(config, [sample_batch()])
        self.assertLessEqual(len(plan["planned_specs"]), 3)
        self.assertTrue(plan["budget_capped"])

    def test_max_per_edge_enforced(self):
        config = valid_campaign_config()
        config["budgets"]["max_per_edge"] = 2
        plan = campaign_planner.build_campaign_plan(config, [sample_batch()])
        self.assertLessEqual(len(plan["planned_specs"]), 2)
        self.assertTrue(any(item["budget_cap_reason"] == "max_per_edge" for item in plan["budget_capped"]))

    def test_max_per_asset_timeframe_enforced(self):
        config = valid_campaign_config()
        config["budgets"]["max_per_asset_timeframe"] = 1
        plan = campaign_planner.build_campaign_plan(config, [sample_batch()])
        counts = {}
        for item in plan["planned_specs"]:
            key = (item["symbol"], item["timeframe"])
            counts[key] = counts.get(key, 0) + 1
        self.assertTrue(all(count <= 1 for count in counts.values()))

    def test_max_per_similarity_cluster_enforced(self):
        config = valid_campaign_config()
        config["budgets"]["max_per_similarity_cluster_per_asset_timeframe"] = 1
        config["similarity_policy"]["max_per_cluster_per_asset_timeframe"] = 1
        plan = campaign_planner.build_campaign_plan(config, [sample_batch()])
        self.assertTrue(
            any("cluster" in item["budget_cap_reason"] for item in plan["budget_capped"])
        )

    def test_similar_variants_are_allowed_up_to_budget(self):
        config = valid_campaign_config()
        plan = campaign_planner.build_campaign_plan(config, [sample_batch()])
        clusters = {}
        for item in plan["planned_specs"]:
            clusters[item["similarity_cluster_id"]] = clusters.get(item["similarity_cluster_id"], 0) + 1
        self.assertTrue(any(count > 1 for count in clusters.values()))

    def test_overflow_variants_reported_as_budget_capped(self):
        config = valid_campaign_config()
        config["similarity_policy"]["max_per_cluster_total"] = 1
        plan = campaign_planner.build_campaign_plan(config, [sample_batch()])
        report = campaign_planner.build_campaign_budget_report(plan)
        self.assertGreater(report["budget_capped_count"], 0)
        self.assertIn("max_per_cluster_total", report["budget_cap_reasons"])


class ResearchPhase28ManualReviewQueueTests(unittest.TestCase):
    def test_ranked_manual_review_queue_is_advisory_only(self):
        plan = campaign_planner.build_campaign_plan(valid_campaign_config(), [sample_batch()])
        queue = campaign_planner.build_ranked_manual_review_queue(plan)
        self.assertEqual(queue["artifact_type"], "ranked_manual_baseline_review_queue")
        self.assertTrue(all(item["advisory_only"] for item in queue["entries"]))

    def test_queue_entries_contain_hypothesis_lineage_and_target(self):
        plan = campaign_planner.build_campaign_plan(valid_campaign_config(), [sample_batch()])
        queue = campaign_planner.build_ranked_manual_review_queue(plan)
        entry = queue["entries"][0]
        self.assertIn("lineage", entry)
        self.assertIn("hypothesis_id", entry["lineage"] | {"hypothesis_id": entry["hypothesis_id"]})
        self.assertIn("symbol", entry["target"])
        self.assertIn("timeframe", entry["target"])

    def test_queue_entries_do_not_contain_approval_or_lifecycle_fields(self):
        plan = campaign_planner.build_campaign_plan(valid_campaign_config(), [sample_batch()])
        queue = campaign_planner.build_ranked_manual_review_queue(plan)
        forbidden = {"approval_status", "approval_usage", "baseline_approval", "lifecycle_proposal"}
        for entry in queue["entries"]:
            self.assertTrue(forbidden.isdisjoint(entry))

    def test_no_dataset_cost_threshold_runner_mutation(self):
        plan = campaign_planner.build_campaign_plan(valid_campaign_config(), [sample_batch()])
        auth = plan["authority"]
        self.assertFalse(auth["dataset_mutation_authority"])
        self.assertFalse(auth["cost_mutation_authority"])
        self.assertFalse(auth["threshold_mutation_authority"])
        self.assertFalse(auth["runner_mutation_authority"])


if __name__ == "__main__":
    unittest.main()
