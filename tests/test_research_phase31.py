from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automated.research import agent_workspace
from automated.research.schemas import REPO_ROOT, SchemaValidationError
from tests.research_test_helpers import (
    agent_workspace_bundle as _bundle,
    valid_edge_thesis as valid_thesis,
    valid_mutation_recipe as valid_recipe,
    valid_research_source as valid_source,
)


class ResearchPhase31BundleValidationTests(unittest.TestCase):
    def test_valid_agent_output_bundle_accepts(self):
        report = agent_workspace.validate_agent_output_bundle(_bundle())
        self.assertEqual(report["artifact_type"], "agent_workspace_validation_report")
        self.assertEqual(report["status"], "accepted")
        self.assertEqual(len(report["accepted_artifacts"]), 1)

    def test_missing_manifest_rejects(self):
        root = Path(tempfile.mkdtemp()) / "bundle"
        root.mkdir()
        with self.assertRaises(SchemaValidationError):
            agent_workspace.validate_agent_output_bundle(root)

    def test_invalid_schema_rejects(self):
        report = agent_workspace.validate_agent_output_bundle(_bundle({"schema_version": "v0"}))
        self.assertEqual(report["status"], "rejected")
        self.assertTrue(any("schema_version" in err for err in report["errors"]))

    def test_unknown_role_rejects(self):
        report = agent_workspace.validate_agent_output_bundle(_bundle({"agent_role": "implementation_agent"}))
        self.assertEqual(report["status"], "rejected")
        self.assertTrue(any("agent_role" in err for err in report["errors"]))

    def test_manifest_must_have_awb_bundle_id(self):
        report = agent_workspace.validate_agent_output_bundle(_bundle({"bundle_id": "TEST_001"}))
        self.assertEqual(report["status"], "rejected")
        self.assertTrue(any("bundle_id" in err for err in report["errors"]))

    def test_missing_artifact_file_rejects(self):
        root = _bundle({"artifacts": [{"artifact_type": "research_source_record", "path": "missing.yaml"}]})
        report = agent_workspace.validate_agent_output_bundle(root)
        self.assertEqual(report["status"], "rejected")
        self.assertTrue(any("does not exist" in err for err in report["errors"]))


class ResearchPhase31PermissionTests(unittest.TestCase):
    def test_role_to_artifact_permissions_enforced(self):
        report = agent_workspace.validate_agent_output_bundle(
            _bundle(
                {
                    "agent_role": "research_librarian",
                    "artifacts": [{"artifact_type": "mutation_recipe", "path": "artifacts/artifact.yaml"}],
                },
                artifact_data=valid_recipe(),
            )
        )
        self.assertEqual(report["status"], "rejected")
        self.assertTrue(any("cannot submit" in err for err in report["errors"]))

    def test_experiment_designer_can_submit_mutation_recipe(self):
        report = agent_workspace.validate_agent_output_bundle(
            _bundle(
                {
                    "agent_role": "experiment_designer",
                    "artifacts": [
                        {
                            "artifact_type": "mutation_recipe",
                            "path": "artifacts/artifact.yaml",
                            "target_name": "MUT_VOL_BREAKOUT_001.yaml",
                        }
                    ],
                },
                artifact_data=valid_recipe(),
            )
        )
        self.assertEqual(report["status"], "accepted")

    def test_unregistered_artifact_type_rejects(self):
        report = agent_workspace.validate_agent_output_bundle(
            _bundle({"artifacts": [{"artifact_type": "unknown_artifact", "path": "artifacts/artifact.yaml"}]})
        )
        self.assertEqual(report["status"], "rejected")
        self.assertTrue(any("not registered" in err for err in report["errors"]))

class ResearchPhase31PathSafetyTests(unittest.TestCase):
    def test_path_traversal_rejects(self):
        report = agent_workspace.validate_agent_output_bundle(
            _bundle({"artifacts": [{"artifact_type": "research_source_record", "path": "../outside.yaml"}]})
        )
        self.assertEqual(report["status"], "rejected")
        self.assertTrue(any("escapes allowed root" in err for err in report["errors"]))

    def test_absolute_artifact_path_rejects(self):
        report = agent_workspace.validate_agent_output_bundle(
            _bundle({"artifacts": [{"artifact_type": "research_source_record", "path": "/tmp/source.yaml"}]})
        )
        self.assertEqual(report["status"], "rejected")
        self.assertTrue(any("relative" in err for err in report["errors"]))

    def test_target_name_with_directory_rejects(self):
        report = agent_workspace.validate_agent_output_bundle(
            _bundle({"artifacts": [{"artifact_type": "research_source_record", "path": "artifacts/artifact.yaml", "target_name": "../bad.yaml"}]})
        )
        self.assertEqual(report["status"], "rejected")
        self.assertTrue(any("target_name" in err for err in report["errors"]))

    def test_mq5_artifact_rejects(self):
        root = _bundle(artifact_name="bad.mq5")
        report = agent_workspace.validate_agent_output_bundle(root)
        self.assertEqual(report["status"], "rejected")
        self.assertTrue(any(".mq5" in err for err in report["errors"]))

    def test_automated_strategies_path_text_rejects(self):
        report = agent_workspace.validate_agent_output_bundle(
            _bundle({"artifacts": [{"artifact_type": "research_source_record", "path": "artifacts/automated/strategies/bad.yaml"}]})
        )
        self.assertEqual(report["status"], "rejected")
        self.assertTrue(any("automated/strategies" in err for err in report["errors"]))


class ResearchPhase31AuthorityBoundaryTests(unittest.TestCase):
    def test_manifest_approval_field_rejects(self):
        report = agent_workspace.validate_agent_output_bundle(_bundle({"approval_status": "approved"}))
        self.assertEqual(report["status"], "rejected")
        self.assertTrue(any("authority" in err for err in report["errors"]))

    def test_artifact_content_lifecycle_field_rejects(self):
        report = agent_workspace.validate_agent_output_bundle(
            _bundle(artifact_data={**valid_source(), "lifecycle_proposal": "apply"})
        )
        self.assertEqual(report["status"], "rejected")
        self.assertTrue(any("content contains forbidden authority" in err for err in report["errors"]))

    def test_forbidden_values_reject_as_allowed_recommendations(self):
        report = agent_workspace.validate_agent_output_bundle(
            _bundle({"allowed_recommendations": ["reject", "production_candidate"]})
        )
        self.assertEqual(report["status"], "rejected")
        self.assertTrue(any("forbidden values" in err for err in report["errors"]))

    def test_unknown_allowed_recommendation_rejects(self):
        report = agent_workspace.validate_agent_output_bundle(
            _bundle({"allowed_recommendations": ["auto_promote"]})
        )
        self.assertEqual(report["status"], "rejected")

    def test_report_has_no_execution_authority(self):
        report = agent_workspace.validate_agent_output_bundle(_bundle())
        authority = report["authority"]
        self.assertFalse(authority["execution_authority"])
        self.assertFalse(authority["queue_execution_authority"])
        self.assertFalse(authority["baseline_decision_authority"])
        self.assertFalse(authority["final_holdout_decision_authority"])
        self.assertFalse(authority["state_transition_authority"])
        self.assertFalse(authority["production_authority"])
        self.assertFalse(authority["live_trading_authority"])


class ResearchPhase31ImportAndQuarantineTests(unittest.TestCase):
    def test_import_accepted_artifacts_only_into_canonical_spec_folders(self):
        bundle = _bundle(artifact_data=valid_thesis(), manifest_overrides={
            "artifacts": [
                {
                    "artifact_type": "edge_thesis",
                    "path": "artifacts/artifact.yaml",
                    "target_name": "EDGE_VOL_COMPRESSION_BREAKOUT_001.yaml",
                }
            ]
        })
        repo = Path(tempfile.mkdtemp()) / "repo"
        report = agent_workspace.import_accepted_agent_bundle(bundle, repo_root=repo)
        self.assertEqual(report["status"], "accepted")
        imported = Path(report["canonical_imports"][0]["canonical_path"])
        self.assertTrue(imported.is_file())
        self.assertIn("automated/specs/edge_theses", str(imported))
        self.assertNotIn("automated/strategies", str(imported))

    def test_import_rejected_bundle_raises(self):
        bundle = _bundle({"approval_status": "approved"})
        with self.assertRaises(SchemaValidationError):
            agent_workspace.import_accepted_agent_bundle(bundle, repo_root=Path(tempfile.mkdtemp()))

    def test_quarantine_rejected_bundle_copies_bundle_and_report(self):
        bundle = _bundle({"approval_status": "approved"})
        report = agent_workspace.validate_agent_output_bundle(bundle)
        rejected_root = Path(tempfile.mkdtemp()) / "rejected"
        quarantine = agent_workspace.quarantine_rejected_bundle(bundle, report, rejected_dir=rejected_root)
        self.assertEqual(quarantine["status"], "quarantined")
        self.assertTrue(Path(quarantine["validation_report_path"]).is_file())

    def test_quarantine_accepted_bundle_rejects(self):
        bundle = _bundle()
        report = agent_workspace.validate_agent_output_bundle(bundle)
        with self.assertRaises(SchemaValidationError):
            agent_workspace.quarantine_rejected_bundle(bundle, report, rejected_dir=Path(tempfile.mkdtemp()))

    def test_write_validation_report(self):
        report = agent_workspace.validate_agent_output_bundle(_bundle())
        out = Path(tempfile.mkdtemp()) / "logs" / "report.yaml"
        written = agent_workspace.write_agent_workspace_validation_report(report, out)
        self.assertTrue(written.is_file())


class ResearchPhase31DocsAndWorkspaceTests(unittest.TestCase):
    def test_workspace_gitkeep_dirs_exist(self):
        for name in ["inbox", "working", "outbox", "rejected", "logs"]:
            self.assertTrue((REPO_ROOT / "automated" / "agent_workspace" / name / ".gitkeep").is_file())


if __name__ == "__main__":
    unittest.main()
