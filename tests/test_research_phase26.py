from __future__ import annotations

import unittest

from automated.research import edge_library
from automated.research.schemas import SchemaValidationError
from tests.research_test_helpers import (
    valid_edge_thesis as valid_thesis,
    valid_research_source as valid_source,
    write_yaml_temp,
)


def _write_yaml(data: dict):
    return write_yaml_temp(data)


class ResearchPhase26SourceValidationTests(unittest.TestCase):
    def test_valid_source_loads(self):
        loaded = edge_library.load_research_source(_write_yaml(valid_source()))
        self.assertEqual(loaded["source_id"], "SRC_CASE_VOL_BREAKOUT_001")

    def test_missing_required_fields_reject(self):
        data = valid_source()
        del data["summary"]
        with self.assertRaises(SchemaValidationError):
            edge_library.validate_research_source(data)

    def test_unknown_source_type_rejects(self):
        with self.assertRaises(SchemaValidationError):
            edge_library.validate_research_source(valid_source(source_type="forum_rumor"))

    def test_source_ids_must_use_src_prefix(self):
        with self.assertRaises(SchemaValidationError):
            edge_library.validate_research_source(valid_source(source_id="CASE_VOL_BREAKOUT_001"))

    def test_extraction_status_enum_enforced(self):
        with self.assertRaises(SchemaValidationError):
            edge_library.validate_research_source(valid_source(extraction_status="approved"))

    def test_url_reference_may_be_plain_citation_text(self):
        loaded = edge_library.validate_research_source(
            valid_source(url_or_reference="Smith and Doe, Journal of Market Studies, 2024")
        )
        self.assertIn("Smith", loaded["url_or_reference"])


class ResearchPhase26EdgeThesisValidationTests(unittest.TestCase):
    def test_valid_edge_thesis_loads(self):
        loaded = edge_library.load_edge_thesis(_write_yaml(valid_thesis()))
        self.assertEqual(loaded["edge_id"], "EDGE_VOL_COMPRESSION_BREAKOUT_001")

    def test_missing_required_fields_reject(self):
        data = valid_thesis()
        del data["mechanism"]
        with self.assertRaises(SchemaValidationError):
            edge_library.validate_edge_thesis(data)

    def test_unknown_edge_family_rejects(self):
        with self.assertRaises(SchemaValidationError):
            edge_library.validate_edge_thesis(valid_thesis(edge_family="astrology"))

    def test_unknown_evidence_strength_rejects(self):
        with self.assertRaises(SchemaValidationError):
            edge_library.validate_edge_thesis(valid_thesis(evidence_strength="certain"))

    def test_unknown_status_rejects(self):
        with self.assertRaises(SchemaValidationError):
            edge_library.validate_edge_thesis(valid_thesis(status="approved"))

    def test_edge_ids_must_use_edge_prefix(self):
        with self.assertRaises(SchemaValidationError):
            edge_library.validate_edge_thesis(valid_thesis(edge_id="VOL_COMPRESSION_BREAKOUT_001"))

    def test_source_ids_must_use_src_prefix(self):
        with self.assertRaises(SchemaValidationError):
            edge_library.validate_edge_thesis(valid_thesis(source_ids=["CASE_VOL_BREAKOUT_001"]))

    def test_candidate_timeframes_accept_mt5_style_values(self):
        loaded = edge_library.validate_edge_thesis(
            valid_thesis(candidate_timeframes=["M15", "M30", "H1", "H4", "D1"])
        )
        self.assertEqual(loaded["candidate_timeframes"], ["M15", "M30", "H1", "H4", "D1"])

    def test_invalid_timeframe_rejects(self):
        with self.assertRaises(SchemaValidationError):
            edge_library.validate_edge_thesis(valid_thesis(candidate_timeframes=["15m"]))


class ResearchPhase26ExtractionReportTests(unittest.TestCase):
    def test_report_shape_is_research_only(self):
        report = edge_library.build_edge_thesis_extraction_report(valid_source(), [valid_thesis()])
        self.assertEqual(report["artifact_type"], "edge_thesis_extraction_report")
        self.assertEqual(report["source_id"], "SRC_CASE_VOL_BREAKOUT_001")
        self.assertEqual(report["extracted_edge_ids"], ["EDGE_VOL_COMPRESSION_BREAKOUT_001"])
        self.assertIn("source_digest", report)
        self.assertEqual(report["warnings"], [])

    def test_report_has_no_execution_or_approval_fields(self):
        report = edge_library.build_edge_thesis_extraction_report(valid_source(), [valid_thesis()])
        forbidden_keys = {
            "proposed_next_action",
            "lifecycle_proposal",
            "approval_status",
            "approval_usage",
            "baseline_approval",
            "final_holdout_approval",
        }
        self.assertTrue(forbidden_keys.isdisjoint(report))
        self.assertFalse(report["authority"]["baseline_decision_authority"])
        self.assertFalse(report["authority"]["final_holdout_decision_authority"])
        self.assertFalse(report["authority"]["state_transition_authority"])

    def test_report_warns_when_thesis_does_not_reference_source(self):
        thesis = valid_thesis(source_ids=["SRC_OTHER_001"])
        report = edge_library.build_edge_thesis_extraction_report(valid_source(), [thesis])
        self.assertTrue(report["warnings"])

    def test_content_id_is_stable(self):
        thesis = valid_thesis(edge_id="EDGE_PLACEHOLDER")
        self.assertEqual(
            edge_library.edge_thesis_id_from_content(thesis),
            edge_library.edge_thesis_id_from_content(dict(thesis)),
        )


if __name__ == "__main__":
    unittest.main()
