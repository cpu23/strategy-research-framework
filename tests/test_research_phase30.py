from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from automated.research import campaign_analysis
from tests.research_test_helpers import FORBIDDEN_VALUES, campaign_analysis_record as record


class ResearchPhase30GroupingTests(unittest.TestCase):
    def test_group_by_edge_family(self):
        grouped = campaign_analysis.group_results_by_edge_family([record(), record(edge_family="mean_reversion")])
        self.assertIn("volatility_expansion", grouped)
        self.assertIn("mean_reversion", grouped)

    def test_group_by_asset_timeframe(self):
        grouped = campaign_analysis.group_results_by_asset_timeframe([record(symbol="EURUSD", timeframe="H4")])
        self.assertIn("EURUSD:H4", grouped)

    def test_group_by_similarity_cluster(self):
        grouped = campaign_analysis.group_results_by_similarity_cluster([record(similarity_cluster_id="SIM_002")])
        self.assertIn("SIM_002", grouped)

    def test_missing_fields_handled_as_unknown(self):
        grouped = campaign_analysis.group_results_by_edge_family([{}])
        self.assertIn("unknown", grouped)

    def test_partial_campaign_records_do_not_crash(self):
        summary = campaign_analysis.summarize_outcomes([{"symbol": "XAUUSD"}])
        self.assertEqual(summary["record_count"], 1)


class ResearchPhase30OutcomeSummaryTests(unittest.TestCase):
    def test_baseline_pass_warn_fail_summarized(self):
        summary = campaign_analysis.summarize_outcomes([
            record(validation_status="pass", robustness_status="", final_holdout_status=""),
            record(validation_status="warn", robustness_status="", final_holdout_status=""),
            record(validation_status="fail", robustness_status="", final_holdout_status=""),
        ])
        self.assertEqual(summary["outcome_counts"]["baseline_passed"], 1)
        self.assertEqual(summary["outcome_counts"]["baseline_warned"], 1)
        self.assertEqual(summary["outcome_counts"]["baseline_failed"], 1)

    def test_robustness_pass_warn_fail_summarized(self):
        summary = campaign_analysis.summarize_outcomes([
            record(robustness_status="pass"),
            record(robustness_status="warn"),
            record(robustness_status="fail"),
        ])
        self.assertEqual(summary["outcome_counts"]["robustness_passed"], 1)
        self.assertEqual(summary["outcome_counts"]["robustness_warned"], 1)
        self.assertEqual(summary["outcome_counts"]["robustness_failed"], 1)

    def test_final_holdout_pass_warn_fail_summarized(self):
        summary = campaign_analysis.summarize_outcomes([
            record(final_holdout_status="pass"),
            record(final_holdout_status="warn"),
            record(final_holdout_status="fail"),
        ])
        self.assertEqual(summary["outcome_counts"]["final_holdout_passed"], 1)
        self.assertEqual(summary["outcome_counts"]["final_holdout_warned"], 1)
        self.assertEqual(summary["outcome_counts"]["final_holdout_failed"], 1)

    def test_insufficient_evidence_classified(self):
        summary = campaign_analysis.summarize_outcomes([{"edge_family": "trend"}])
        self.assertEqual(summary["outcome_counts"]["insufficient_evidence"], 1)

    def test_no_production_live_outcomes(self):
        self.assertTrue(FORBIDDEN_VALUES.isdisjoint(campaign_analysis.OUTCOME_CATEGORIES))


class ResearchPhase30FailureModeTests(unittest.TestCase):
    def test_compile_failure(self):
        self.assertEqual(campaign_analysis.classify_failure_mode(record(compile_status="failed")), "compile_failure")

    def test_diff_review_failure(self):
        self.assertEqual(campaign_analysis.classify_failure_mode(record(diff_review_status="fail")), "diff_review_failure")

    def test_baseline_failure(self):
        self.assertEqual(campaign_analysis.classify_failure_mode(record(validation_status="fail")), "baseline_failure")

    def test_robustness_instability(self):
        self.assertEqual(campaign_analysis.classify_failure_mode(record(robustness_status="unstable")), "robustness_instability")

    def test_final_holdout_failure(self):
        self.assertEqual(campaign_analysis.classify_failure_mode(record(final_holdout_status="fail")), "final_holdout_failure")

    def test_similarity_redundancy(self):
        self.assertEqual(campaign_analysis.classify_failure_mode(record(similarity_status="redundant")), "similarity_redundancy")

    def test_insufficient_sample_size(self):
        self.assertEqual(campaign_analysis.classify_failure_mode(record(trade_count=5, min_trades_required=20)), "insufficient_sample_size")

    def test_unknown(self):
        self.assertEqual(campaign_analysis.classify_failure_mode(record()), "unknown")


class ResearchPhase30ReportShapeTests(unittest.TestCase):
    def test_campaign_report_has_artifact_type(self):
        report = campaign_analysis.build_generated_research_campaign_report({"campaign_id": "CAMP_TEST", "records": [record()]})
        self.assertEqual(report["artifact_type"], "generated_research_campaign_report")

    def test_edge_family_meta_analysis_shape(self):
        report = campaign_analysis.build_edge_family_meta_analysis([record()])
        self.assertEqual(report["artifact_type"], "edge_family_meta_analysis")
        self.assertIn("volatility_expansion", report["groups"])

    def test_asset_timeframe_effectiveness_shape(self):
        report = campaign_analysis.build_asset_timeframe_effectiveness_report([record()])
        self.assertEqual(report["artifact_type"], "asset_timeframe_effectiveness_report")
        self.assertIn("XAUUSD:H1", report["groups"])

    def test_similarity_cluster_effectiveness_shape(self):
        report = campaign_analysis.build_similarity_cluster_effectiveness_report([record()])
        self.assertEqual(report["artifact_type"], "similarity_cluster_effectiveness_report")
        self.assertIn("SIM_001", report["groups"])

    def test_recommendations_are_advisory_only(self):
        report = campaign_analysis.build_generated_research_campaign_report({"records": [record()]})
        allowed = campaign_analysis.ADVISORY_RECOMMENDATIONS
        self.assertTrue(all(item["recommendation"] in allowed for item in report["recommendations"]))

    def test_no_lifecycle_proposal_or_approval_fields(self):
        report = campaign_analysis.build_generated_research_campaign_report({"records": [record()]})
        self.assertNotIn("lifecycle_proposal", report)
        self.assertNotIn("approval_status", report)

    def test_load_campaign_artifacts_supports_yaml(self):
        path = Path(tempfile.mkdtemp()) / "artifact.yaml"
        path.write_text(yaml.safe_dump(record(), sort_keys=False), encoding="utf-8")
        loaded = campaign_analysis.load_campaign_artifacts([path])
        self.assertEqual(loaded[0]["hypothesis_id"], "HYP_GEN_001")


if __name__ == "__main__":
    unittest.main()
