from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from automated.research import cli, implementation as impl_mod, intake, registry, validation
from automated.research.schemas import (
    GENERATED_SPECS_DIR,
    HYPOTHESES_DIR,
    REPO_ROOT,
    SANDBOX_ROOT,
    validate_strategy_spec,
)


def _clean_test_hypotheses() -> None:
    for path in HYPOTHESES_DIR.glob("HYP_GEN_FBR_*"):
        path.unlink(missing_ok=True)


def _clean_generated_specs() -> None:
    for path in GENERATED_SPECS_DIR.glob("demo_fbr_generated*"):
        path.unlink(missing_ok=True)
    for path in GENERATED_SPECS_DIR.glob("test_fbr_generated*"):
        path.unlink(missing_ok=True)


def _clean_sandbox(strategy_id: str) -> None:
    sandbox = SANDBOX_ROOT / strategy_id
    if sandbox.is_dir():
        import shutil
        shutil.rmtree(sandbox)


def _clean_generated_run_files(strategy_id: str) -> None:
    (REPO_ROOT / "automated" / "runs" / f"{strategy_id}_baseline.conf").unlink(missing_ok=True)
    (REPO_ROOT / "automated" / "runs" / "sets" / f"{strategy_id}_baseline.set").unlink(missing_ok=True)


class ResearchPhase10HypothesisGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        _clean_test_hypotheses()

    def tearDown(self) -> None:
        _clean_test_hypotheses()

    def test_generate_hypotheses_creates_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = intake.generate_hypotheses(
                research_theme="H4 XAUUSD test",
                symbol="XAUUSD",
                timeframe="H4",
                market_regime="ranging",
                strategy_family="failed_breakout_reversal",
                max_hypotheses=1,
                created_by="test",
                hypothesis_set_dir=tmp,
            )
            self.assertEqual(len(result), 1)
            hyp = result[0]
            self.assertIn("hypothesis_id", hyp)
            self.assertEqual(hyp["hypothesis_id"], "HYP_GEN_FBR_RANGING_000")
            self.assertIn("path", hyp)
            hyp_path = Path(hyp["path"])
            self.assertTrue(hyp_path.is_file())

            import yaml
            data = yaml.safe_load(hyp_path.read_text(encoding="utf-8"))
            self.assertEqual(data["hypothesis_id"], "HYP_GEN_FBR_RANGING_000")
            self.assertIn("_phase10", data)
            self.assertEqual(data["_phase10"]["strategy_family"], "failed_breakout_reversal")

    def test_max_hypotheses_respected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = intake.generate_hypotheses(
                research_theme="test",
                symbol="XAUUSD",
                timeframe="H4",
                market_regime="ranging",
                strategy_family="failed_breakout_reversal",
                max_hypotheses=3,
                created_by="test",
                hypothesis_set_dir=tmp,
            )
            self.assertEqual(len(result), 1)

    def test_generate_hypotheses_invalid_family(self) -> None:
        with self.assertRaises(ValueError):
            intake.generate_hypotheses(
                research_theme="test",
                symbol="XAUUSD",
                timeframe="H4",
                market_regime="ranging",
                strategy_family="nonexistent_family",
                max_hypotheses=1,
                created_by="test",
            )

    def test_generate_hypotheses_invalid_regime(self) -> None:
        with self.assertRaises(ValueError):
            intake.generate_hypotheses(
                research_theme="test",
                symbol="XAUUSD",
                timeframe="H4",
                market_regime="unknown_regime",
                strategy_family="failed_breakout_reversal",
                max_hypotheses=1,
                created_by="test",
            )

    def test_hypothesis_validates_with_existing_schema(self) -> None:
        _clean_test_hypotheses()
        result = intake.generate_hypotheses(
            research_theme="test",
            symbol="XAUUSD",
            timeframe="H4",
            market_regime="ranging",
            strategy_family="failed_breakout_reversal",
            max_hypotheses=1,
            created_by="test",
        )
        hyp_path = Path(result[0]["path"])
        from automated.research.schemas import load_yaml, validate_hypothesis
        data = load_yaml(hyp_path)
        validate_hypothesis(data)

    def test_hypothesis_set_artifact_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = intake.generate_hypotheses(
                research_theme="test theme",
                symbol="XAUUSD",
                timeframe="H4",
                market_regime="ranging",
                strategy_family="failed_breakout_reversal",
                max_hypotheses=1,
                created_by="test_agent",
                hypothesis_set_dir=tmp,
            )
            set_path = Path(tmp) / "hypothesis_set.yaml"
            self.assertTrue(set_path.is_file())
            import yaml
            set_doc = yaml.safe_load(set_path.read_text(encoding="utf-8"))
            self.assertEqual(set_doc["artifact_type"], "hypothesis_set")
            self.assertEqual(set_doc["research_theme"], "test theme")
            self.assertEqual(set_doc["symbol"], "XAUUSD")
            self.assertEqual(set_doc["generated_count"], 1)
            self.assertEqual(len(set_doc["hypotheses"]), 1)
            self.assertEqual(set_doc["hypotheses"][0]["hypothesis_id"], "HYP_GEN_FBR_RANGING_000")


class ResearchPhase10SpecGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        _clean_test_hypotheses()
        _clean_generated_specs()

    def tearDown(self) -> None:
        _clean_test_hypotheses()
        _clean_generated_specs()

    def test_generate_spec_from_hypothesis(self) -> None:
        intake.generate_hypotheses(
            research_theme="test",
            symbol="XAUUSD",
            timeframe="H4",
            market_regime="ranging",
            strategy_family="failed_breakout_reversal",
            max_hypotheses=1,
            created_by="test",
        )
        result = intake.generate_strategy_spec(
            hypothesis_id="HYP_GEN_FBR_RANGING_000",
            strategy_id="demo_fbr_generated",
            strategy_version="v1",
            created_by="test",
        )
        self.assertEqual(result["strategy_id"], "demo_fbr_generated")
        self.assertEqual(result["strategy_version"], "v1")
        spec_path = Path(result["spec_path"])
        self.assertTrue(spec_path.is_file())
        self.assertIn("demo_fbr_generated", str(spec_path))
        self.assertIn("generated_specs", str(spec_path))

    def test_generated_spec_has_required_fields(self) -> None:
        intake.generate_hypotheses(
            research_theme="test",
            symbol="XAUUSD",
            timeframe="H4",
            market_regime="ranging",
            strategy_family="failed_breakout_reversal",
            max_hypotheses=1,
            created_by="test",
        )
        result = intake.generate_strategy_spec(
            hypothesis_id="HYP_GEN_FBR_RANGING_000",
            strategy_id="demo_fbr_generated",
            strategy_version="v1",
            created_by="test",
        )
        import yaml
        data = yaml.safe_load(Path(result["spec_path"]).read_text(encoding="utf-8"))
        self.assertEqual(data["strategy_id"], "demo_fbr_generated")
        self.assertEqual(data["strategy_version"], "v1")
        self.assertEqual(data["hypothesis_id"], "HYP_GEN_FBR_RANGING_000")
        self.assertIn("entry", data)
        self.assertIn("exit", data)
        self.assertIn("risk", data)
        self.assertIn("parameters", data)
        self.assertIn("implementation", data)
        self.assertEqual(data["implementation"]["generation_mode"], "wrapped_existing_files")
        self.assertIn("expert_advisor", data["implementation"]["files"])
        self.assertIn("generated_strategies/demo_fbr_generated", data["implementation"]["files"]["expert_advisor"])

    def test_generated_spec_validates_without_files(self) -> None:
        intake.generate_hypotheses(
            research_theme="test",
            symbol="XAUUSD",
            timeframe="H4",
            market_regime="ranging",
            strategy_family="failed_breakout_reversal",
            max_hypotheses=1,
            created_by="test",
        )
        result = intake.generate_strategy_spec(
            hypothesis_id="HYP_GEN_FBR_RANGING_000",
            strategy_id="demo_fbr_generated",
            strategy_version="v1",
            created_by="test",
        )
        import yaml
        data = yaml.safe_load(Path(result["spec_path"]).read_text(encoding="utf-8"))
        validated = validate_strategy_spec(data, require_files=False)
        self.assertEqual(validated["strategy_id"], "demo_fbr_generated")

    def test_generated_spec_written_to_generated_specs_dir(self) -> None:
        intake.generate_hypotheses(
            research_theme="test",
            symbol="XAUUSD",
            timeframe="H4",
            market_regime="ranging",
            strategy_family="failed_breakout_reversal",
            max_hypotheses=1,
            created_by="test",
        )
        result = intake.generate_strategy_spec(
            hypothesis_id="HYP_GEN_FBR_RANGING_000",
            strategy_id="demo_fbr_generated",
            strategy_version="v1",
            created_by="test",
        )
        spec_path = Path(result["spec_path"])
        self.assertTrue(str(spec_path).startswith(str(GENERATED_SPECS_DIR)))
        strategies_specs_dir = REPO_ROOT / "automated" / "specs" / "strategies"
        self.assertFalse(str(spec_path).startswith(str(strategies_specs_dir)))


class ResearchPhase10MaterializationTests(unittest.TestCase):
    def setUp(self) -> None:
        _clean_test_hypotheses()
        _clean_generated_specs()
        _clean_sandbox("demo_fbr_generated")
        _clean_generated_run_files("demo_fbr_generated")

    def tearDown(self) -> None:
        _clean_test_hypotheses()
        _clean_generated_specs()
        _clean_sandbox("demo_fbr_generated")
        _clean_generated_run_files("demo_fbr_generated")

    def _prepare_full(self, db_path: str | Path) -> dict:
        _clean_test_hypotheses()
        _clean_generated_specs()
        _clean_sandbox("demo_fbr_generated")
        intake.generate_hypotheses(
            research_theme="test",
            symbol="XAUUSD",
            timeframe="H4",
            market_regime="ranging",
            strategy_family="failed_breakout_reversal",
            max_hypotheses=1,
            created_by="test",
        )
        spec_result = intake.generate_strategy_spec(
            hypothesis_id="HYP_GEN_FBR_RANGING_000",
            strategy_id="demo_fbr_generated",
            strategy_version="v1",
            created_by="test",
        )
        spec_result["db_path"] = str(db_path)
        return spec_result

    def test_materialize_creates_sandbox_mq5(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "registry.sqlite"
            spec_result = self._prepare_full(db_path)
            outcome = intake.materialize_implementation(
                db_path,
                strategy_spec_path=spec_result["spec_path"],
                strategy_id="demo_fbr_generated",
                strategy_version="v1",
                created_by="test",
                mock_compile=True,
            )
            self.assertEqual(outcome["status"], "materialized")
            sandbox_dir = SANDBOX_ROOT / "demo_fbr_generated" / "v1"
            self.assertTrue(sandbox_dir.is_dir())
            mq5_files = list(sandbox_dir.rglob("*.mq5"))
            self.assertEqual(len(mq5_files), 1)
            self.assertEqual(mq5_files[0].name, "demo_fbr_generated.mq5")

    def test_generated_mq5_has_expected_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "registry.sqlite"
            spec_result = self._prepare_full(db_path)
            outcome = intake.materialize_implementation(
                db_path,
                strategy_spec_path=spec_result["spec_path"],
                strategy_id="demo_fbr_generated",
                strategy_version="v1",
                created_by="test",
                mock_compile=True,
            )
            mq5_path = Path(outcome["mq5_path"])
            self.assertTrue(mq5_path.is_file())
            text = mq5_path.read_text(encoding="utf-8")
            self.assertIn("InpMagicNumber", text)
            self.assertIn("InpSymbol", text)
            self.assertIn("InpRiskPercent", text)
            self.assertIn("InpStopLossAtr", text)
            self.assertIn("InpTakeProfitAtr", text)
            self.assertIn("InpAtrPeriod", text)

    def test_materialize_creates_declared_runner_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "registry.sqlite"
            spec_result = self._prepare_full(db_path)
            outcome = intake.materialize_implementation(
                db_path,
                strategy_spec_path=spec_result["spec_path"],
                strategy_id="demo_fbr_generated",
                strategy_version="v1",
                created_by="test",
                mock_compile=True,
            )
            config_path = Path(outcome["config_path"])
            parameters_path = Path(outcome["parameters_path"])
            self.assertTrue(config_path.is_file())
            self.assertTrue(parameters_path.is_file())

            config_text = config_path.read_text(encoding="utf-8")
            self.assertIn("automated/generated_strategies/demo_fbr_generated/v1/demo_fbr_generated.mq5", config_text)
            self.assertIn(f'EA_SET_FILE="{parameters_path.resolve()}"', config_text)

            parameters_text = parameters_path.read_text(encoding="utf-8")
            self.assertIn("InpMagicNumber=12345", parameters_text)
            self.assertIn("InpRangeLookbackBars=20", parameters_text)

    def test_validation_resolves_generated_strategy_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "registry.sqlite"
            spec_result = self._prepare_full(db_path)
            intake.materialize_implementation(
                db_path,
                strategy_spec_path=spec_result["spec_path"],
                strategy_id="demo_fbr_generated",
                strategy_version="v1",
                created_by="test",
                mock_compile=True,
            )
            spec = validation._strategy_spec_for_experiment({"strategy_id": "demo_fbr_generated"})
            self.assertIsNotNone(spec)
            self.assertEqual(spec["strategy_id"], "demo_fbr_generated")
            self.assertTrue(spec["costs"]["assumptions_documented"])

    def test_generated_mq5_avoids_forbidden_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "registry.sqlite"
            spec_result = self._prepare_full(db_path)
            outcome = intake.materialize_implementation(
                db_path,
                strategy_spec_path=spec_result["spec_path"],
                strategy_id="demo_fbr_generated",
                strategy_version="v1",
                created_by="test",
                mock_compile=True,
            )
            mq5_path = Path(outcome["mq5_path"])
            findings = impl_mod.scan_dangerous_patterns(mq5_path)
            warning_ids = {f["id"] for f in findings if f["severity"] == "warning"}
            forbidden = {"import_directive", "shell_execute", "web_request",
                         "file_open", "file_write", "file_delete", "global_variable_set"}
            self.assertEqual(len(warning_ids & forbidden), 0)

    def test_materialize_runs_mock_compile_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "registry.sqlite"
            spec_result = self._prepare_full(db_path)
            outcome = intake.materialize_implementation(
                db_path,
                strategy_spec_path=spec_result["spec_path"],
                strategy_id="demo_fbr_generated",
                strategy_version="v1",
                created_by="test",
                mock_compile=True,
            )
            self.assertIn(outcome["compile_status"], ("mock_checked", "passed"))

    def test_materialize_runs_diff_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "registry.sqlite"
            spec_result = self._prepare_full(db_path)
            outcome = intake.materialize_implementation(
                db_path,
                strategy_spec_path=spec_result["spec_path"],
                strategy_id="demo_fbr_generated",
                strategy_version="v1",
                created_by="test",
                mock_compile=True,
            )
            self.assertIn(outcome["diff_review_status"], ("reviewed", "not_run"))
            if outcome["diff_review_path"]:
                self.assertTrue(Path(outcome["diff_review_path"]).is_file())

    def test_materialize_does_not_approve_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "registry.sqlite"
            spec_result = self._prepare_full(db_path)
            outcome = intake.materialize_implementation(
                db_path,
                strategy_spec_path=spec_result["spec_path"],
                strategy_id="demo_fbr_generated",
                strategy_version="v1",
                created_by="test",
                mock_compile=True,
            )
            self.assertFalse(outcome["approved_for_baseline"])

            req_id = outcome["implementation_request_id"]
            request = registry.get_implementation_request(db_path, req_id)
            self.assertIsNotNone(request)
            self.assertNotEqual(request["status"], "approved_for_baseline")

            impls = registry.list_implementations(db_path, req_id)
            if impls:
                self.assertEqual(impls[-1].get("approved_for_baseline"), 0)


class ResearchPhase10ReviewPacketTests(unittest.TestCase):
    def setUp(self) -> None:
        _clean_test_hypotheses()
        _clean_generated_specs()
        _clean_sandbox("demo_fbr_generated")

    def tearDown(self) -> None:
        _clean_test_hypotheses()
        _clean_generated_specs()
        _clean_sandbox("demo_fbr_generated")

    def _run_full_intake(self, db_path: str | Path) -> dict:
        _clean_test_hypotheses()
        _clean_generated_specs()
        _clean_sandbox("demo_fbr_generated")
        intake.generate_hypotheses(
            research_theme="test",
            symbol="XAUUSD",
            timeframe="H4",
            market_regime="ranging",
            strategy_family="failed_breakout_reversal",
            max_hypotheses=1,
            created_by="test",
        )
        spec_result = intake.generate_strategy_spec(
            hypothesis_id="HYP_GEN_FBR_RANGING_000",
            strategy_id="demo_fbr_generated",
            strategy_version="v1",
        )
        mat_result = intake.materialize_implementation(
            db_path,
            strategy_spec_path=spec_result["spec_path"],
            strategy_id="demo_fbr_generated",
            strategy_version="v1",
            created_by="test",
            mock_compile=True,
        )
        return {"spec": spec_result, "mat": mat_result}

    def test_review_packet_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "registry.sqlite"
            self._run_full_intake(db_path)
            outcome = intake.build_review_packet(
                db_path,
                strategy_id="demo_fbr_generated",
                strategy_version="v1",
                output_dir=Path(tmp) / "review",
            )
            self.assertIn("packet_path", outcome)
            packet_path = Path(outcome["packet_path"])
            self.assertTrue(packet_path.is_file())

    def test_review_packet_not_approved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "registry.sqlite"
            self._run_full_intake(db_path)
            outcome = intake.build_review_packet(
                db_path,
                strategy_id="demo_fbr_generated",
                strategy_version="v1",
                output_dir=Path(tmp) / "review",
            )
            packet = outcome["packet"]
            self.assertEqual(packet["approval_status"], "not_approved")

    def test_review_packet_no_production_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "registry.sqlite"
            self._run_full_intake(db_path)
            outcome = intake.build_review_packet(
                db_path,
                strategy_id="demo_fbr_generated",
                strategy_version="v1",
                output_dir=Path(tmp) / "review",
            )
            action = outcome["packet"]["recommended_next_action"]
            allowed = ("reject", "revise_implementation", "approve_for_one_baseline", "defer")
            self.assertIn(action, allowed)
            self.assertNotEqual(action, "promote_to_production")

    def test_review_packet_has_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "registry.sqlite"
            self._run_full_intake(db_path)
            outcome = intake.build_review_packet(
                db_path,
                strategy_id="demo_fbr_generated",
                strategy_version="v1",
                output_dir=Path(tmp) / "review",
            )
            packet = outcome["packet"]
            required = [
                "hypothesis_summary", "strategy_spec_path", "implementation_request_id",
                "sandbox_implementation_path", "compile_status", "input_spec_match_status",
                "diff_review_status", "dangerous_pattern_warnings", "baseline_eligibility",
                "approval_status", "recommended_next_action",
            ]
            for field in required:
                self.assertIn(field, packet, f"review packet missing field: {field}")


class ResearchPhase10QueueIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        _clean_test_hypotheses()
        _clean_generated_specs()
        _clean_sandbox("demo_fbr_generated")

    def tearDown(self) -> None:
        _clean_test_hypotheses()
        _clean_generated_specs()
        _clean_sandbox("demo_fbr_generated")

    def test_queue_can_run_hypothesis_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "registry.sqlite"
            queue_path = Path(tmp) / "test_queue.yaml"
            queue_path.write_text(
                "queue_id: test_gen_hyp\n"
                "items:\n"
                "  - queue_id: gen_test_001\n"
                "    priority: 10\n"
                "    task_type: hypothesis_generation\n"
                "    research_theme: H4 XAUUSD test\n"
                "    symbol: XAUUSD\n"
                "    timeframe: H4\n"
                "    market_regime: ranging\n"
                "    strategy_family: failed_breakout_reversal\n"
                "    max_hypotheses: 1\n"
                "    constraints: {}\n"
                "    requested_by: test\n"
                "    created_at: '2026-05-12T00:00:00+00:00'\n"
                "    allowed_agent_roles: []\n"
                "    budget:\n"
                "      max_experiments: 0\n"
                "      max_child_experiments: 0\n"
                "      max_runtime_minutes: 10\n"
                "      max_failed_runs: 0\n"
                "      max_disk_usage_mb: 64\n"
                "      require_one_variable_at_a_time: true\n"
                "    permissions:\n"
                "      allow_runner_execution: false\n"
                "      allow_mql5_edits: false\n"
                "      allow_dataset_changes: false\n"
                "      allow_validation_threshold_changes: false\n"
                "      allow_lifecycle_apply: false\n"
                "      allow_lifecycle_propose: false\n"
                "      allow_final_holdout: false\n"
                "    required_outputs: []\n"
                "    status: queued\n",
                encoding="utf-8",
            )
            from automated.research.queue import run_queue
            result = run_queue(db_path, queue_path, dry_run=False, output_root=Path(tmp) / "queue_runs")
            self.assertNotEqual(result["status"], "failed")
            self.assertTrue(
                any("HYP_GEN_FBR_RANGING_000" in str(a) for item in result["items"] for a in item.get("artifacts_created", []))
                or any("HYP_GEN" in str(item) for item in result["items"])
            )

    def test_full_intake_queue_does_not_approve_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "registry.sqlite"
            queue_yaml = (
                "queue_id: test_full_intake\n"
                "items:\n"
                "  - queue_id: hyp_gen\n"
                "    priority: 10\n"
                "    task_type: hypothesis_generation\n"
                "    research_theme: H4 XAUUSD test\n"
                "    symbol: XAUUSD\n"
                "    timeframe: H4\n"
                "    market_regime: ranging\n"
                "    strategy_family: failed_breakout_reversal\n"
                "    max_hypotheses: 1\n"
                "    constraints: {}\n"
                "    requested_by: test\n"
                "    created_at: '2026-05-12T00:00:00+00:00'\n"
                "    allowed_agent_roles: []\n"
                "    budget:\n"
                "      max_experiments: 0\n"
                "      max_child_experiments: 0\n"
                "      max_runtime_minutes: 10\n"
                "      max_failed_runs: 0\n"
                "      max_disk_usage_mb: 64\n"
                "      require_one_variable_at_a_time: true\n"
                "    permissions:\n"
                "      allow_runner_execution: false\n"
                "      allow_mql5_edits: false\n"
                "      allow_dataset_changes: false\n"
                "      allow_validation_threshold_changes: false\n"
                "      allow_lifecycle_apply: false\n"
                "      allow_lifecycle_propose: false\n"
                "      allow_final_holdout: false\n"
                "    required_outputs: []\n"
                "    status: queued\n"
                "  - queue_id: spec_gen\n"
                "    priority: 20\n"
                "    task_type: strategy_spec_generation\n"
                "    hypothesis_id: HYP_GEN_FBR_RANGING_000\n"
                "    strategy_id: demo_fbr_generated\n"
                "    strategy_version: v1\n"
                "    selected_index: 0\n"
                "    requested_by: test\n"
                "    created_at: '2026-05-12T00:00:00+00:00'\n"
                "    allowed_agent_roles: []\n"
                "    budget:\n"
                "      max_experiments: 0\n"
                "      max_child_experiments: 0\n"
                "      max_runtime_minutes: 10\n"
                "      max_failed_runs: 0\n"
                "      max_disk_usage_mb: 64\n"
                "      require_one_variable_at_a_time: true\n"
                "    permissions:\n"
                "      allow_runner_execution: false\n"
                "      allow_mql5_edits: false\n"
                "      allow_dataset_changes: false\n"
                "      allow_validation_threshold_changes: false\n"
                "      allow_lifecycle_apply: false\n"
                "      allow_lifecycle_propose: false\n"
                "      allow_final_holdout: false\n"
                "    required_outputs: []\n"
                "    status: queued\n"
                "  - queue_id: materialize\n"
                "    priority: 30\n"
                "    task_type: implementation_materialization\n"
                "    strategy_id: demo_fbr_generated\n"
                "    strategy_version: v1\n"
                "    generated_spec_path: automated/generated_specs/demo_fbr_generated.yaml\n"
                "    requested_by: test\n"
                "    created_at: '2026-05-12T00:00:00+00:00'\n"
                "    allowed_agent_roles: []\n"
                "    budget:\n"
                "      max_experiments: 0\n"
                "      max_child_experiments: 0\n"
                "      max_runtime_minutes: 10\n"
                "      max_failed_runs: 0\n"
                "      max_disk_usage_mb: 64\n"
                "      require_one_variable_at_a_time: true\n"
                "    permissions:\n"
                "      allow_runner_execution: false\n"
                "      allow_mql5_edits: false\n"
                "      allow_dataset_changes: false\n"
                "      allow_validation_threshold_changes: false\n"
                "      allow_lifecycle_apply: false\n"
                "      allow_lifecycle_propose: false\n"
                "      allow_final_holdout: false\n"
                "    required_outputs: []\n"
                "    status: queued\n"
                "  - queue_id: review\n"
                "    priority: 40\n"
                "    task_type: research_review_packet\n"
                "    strategy_id: demo_fbr_generated\n"
                "    strategy_version: v1\n"
                "    requested_by: test\n"
                "    created_at: '2026-05-12T00:00:00+00:00'\n"
                "    allowed_agent_roles: []\n"
                "    budget:\n"
                "      max_experiments: 0\n"
                "      max_child_experiments: 0\n"
                "      max_runtime_minutes: 10\n"
                "      max_failed_runs: 0\n"
                "      max_disk_usage_mb: 64\n"
                "      require_one_variable_at_a_time: true\n"
                "    permissions:\n"
                "      allow_runner_execution: false\n"
                "      allow_mql5_edits: false\n"
                "      allow_dataset_changes: false\n"
                "      allow_validation_threshold_changes: false\n"
                "      allow_lifecycle_apply: false\n"
                "      allow_lifecycle_propose: false\n"
                "      allow_final_holdout: false\n"
                "    required_outputs: []\n"
                "    status: queued\n"
            )
            queue_path = Path(tmp) / "full_intake.yaml"
            queue_path.write_text(queue_yaml, encoding="utf-8")
            from automated.research.queue import run_queue
            result = run_queue(db_path, queue_path, dry_run=False, output_root=Path(tmp) / "queue_runs")
            self.assertNotEqual(result["status"], "failed",
                                f"Queue run failed: {result.get('failures', [])}")

            requests = registry.list_implementation_requests(db_path)
            for req in requests:
                self.assertNotEqual(req["status"], "approved_for_baseline",
                                    f"Request {req['implementation_request_id']} was auto-approved")
                req_impls = registry.list_implementations(db_path, req["implementation_request_id"])
                for impl_rec in req_impls:
                    self.assertEqual(impl_rec.get("approved_for_baseline"), 0,
                                     f"Implementation {impl_rec['implementation_id']} was auto-approved")

    def test_full_intake_queue_no_copy_to_strategies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "registry.sqlite"
            queue_yaml = (
                "queue_id: test_no_copy\n"
                "items:\n"
                "  - queue_id: hyp_gen\n"
                "    priority: 10\n"
                "    task_type: hypothesis_generation\n"
                "    research_theme: H4 XAUUSD test\n"
                "    symbol: XAUUSD\n"
                "    timeframe: H4\n"
                "    market_regime: ranging\n"
                "    strategy_family: failed_breakout_reversal\n"
                "    max_hypotheses: 1\n"
                "    constraints: {}\n"
                "    requested_by: test\n"
                "    created_at: '2026-05-12T00:00:00+00:00'\n"
                "    allowed_agent_roles: []\n"
                "    budget:\n"
                "      max_experiments: 0\n"
                "      max_child_experiments: 0\n"
                "      max_runtime_minutes: 10\n"
                "      max_failed_runs: 0\n"
                "      max_disk_usage_mb: 64\n"
                "      require_one_variable_at_a_time: true\n"
                "    permissions:\n"
                "      allow_runner_execution: false\n"
                "      allow_mql5_edits: false\n"
                "      allow_dataset_changes: false\n"
                "      allow_validation_threshold_changes: false\n"
                "      allow_lifecycle_apply: false\n"
                "      allow_lifecycle_propose: false\n"
                "      allow_final_holdout: false\n"
                "    required_outputs: []\n"
                "    status: queued\n"
                "  - queue_id: spec_gen\n"
                "    priority: 20\n"
                "    task_type: strategy_spec_generation\n"
                "    hypothesis_id: HYP_GEN_FBR_RANGING_000\n"
                "    strategy_id: demo_fbr_generated\n"
                "    strategy_version: v1\n"
                "    selected_index: 0\n"
                "    requested_by: test\n"
                "    created_at: '2026-05-12T00:00:00+00:00'\n"
                "    allowed_agent_roles: []\n"
                "    budget:\n"
                "      max_experiments: 0\n"
                "      max_child_experiments: 0\n"
                "      max_runtime_minutes: 10\n"
                "      max_failed_runs: 0\n"
                "      max_disk_usage_mb: 64\n"
                "      require_one_variable_at_a_time: true\n"
                "    permissions:\n"
                "      allow_runner_execution: false\n"
                "      allow_mql5_edits: false\n"
                "      allow_dataset_changes: false\n"
                "      allow_validation_threshold_changes: false\n"
                "      allow_lifecycle_apply: false\n"
                "      allow_lifecycle_propose: false\n"
                "      allow_final_holdout: false\n"
                "    required_outputs: []\n"
                "    status: queued\n"
                "  - queue_id: materialize\n"
                "    priority: 30\n"
                "    task_type: implementation_materialization\n"
                "    strategy_id: demo_fbr_generated\n"
                "    strategy_version: v1\n"
                "    generated_spec_path: automated/generated_specs/demo_fbr_generated.yaml\n"
                "    requested_by: test\n"
                "    created_at: '2026-05-12T00:00:00+00:00'\n"
                "    allowed_agent_roles: []\n"
                "    budget:\n"
                "      max_experiments: 0\n"
                "      max_child_experiments: 0\n"
                "      max_runtime_minutes: 10\n"
                "      max_failed_runs: 0\n"
                "      max_disk_usage_mb: 64\n"
                "      require_one_variable_at_a_time: true\n"
                "    permissions:\n"
                "      allow_runner_execution: false\n"
                "      allow_mql5_edits: false\n"
                "      allow_dataset_changes: false\n"
                "      allow_validation_threshold_changes: false\n"
                "      allow_lifecycle_apply: false\n"
                "      allow_lifecycle_propose: false\n"
                "      allow_final_holdout: false\n"
                "    required_outputs: []\n"
                "    status: queued\n"
                "  - queue_id: review\n"
                "    priority: 40\n"
                "    task_type: research_review_packet\n"
                "    strategy_id: demo_fbr_generated\n"
                "    strategy_version: v1\n"
                "    requested_by: test\n"
                "    created_at: '2026-05-12T00:00:00+00:00'\n"
                "    allowed_agent_roles: []\n"
                "    budget:\n"
                "      max_experiments: 0\n"
                "      max_child_experiments: 0\n"
                "      max_runtime_minutes: 10\n"
                "      max_failed_runs: 0\n"
                "      max_disk_usage_mb: 64\n"
                "      require_one_variable_at_a_time: true\n"
                "    permissions:\n"
                "      allow_runner_execution: false\n"
                "      allow_mql5_edits: false\n"
                "      allow_dataset_changes: false\n"
                "      allow_validation_threshold_changes: false\n"
                "      allow_lifecycle_apply: false\n"
                "      allow_lifecycle_propose: false\n"
                "      allow_final_holdout: false\n"
                "    required_outputs: []\n"
                "    status: queued\n"
            )
            queue_path = Path(tmp) / "no_copy.yaml"
            queue_path.write_text(queue_yaml, encoding="utf-8")
            from automated.research.queue import run_queue
            result = run_queue(db_path, queue_path, dry_run=False, output_root=Path(tmp) / "queue_runs")
            self.assertNotEqual(result["status"], "failed")

            strategies_dir = REPO_ROOT / "automated" / "strategies"
            fbr_files = list(strategies_dir.rglob("*demo_fbr_generated*"))
            self.assertEqual(len(fbr_files), 0,
                             f"Found files in automated/strategies/: {fbr_files}")

    def test_queue_validates_intake_task_types(self) -> None:
        from automated.research.queue import TASK_TYPES
        for tt in ("hypothesis_generation", "strategy_spec_generation",
                   "implementation_materialization", "research_review_packet"):
            self.assertIn(tt, TASK_TYPES)


class ResearchPhase10CLISmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        _clean_test_hypotheses()
        _clean_generated_specs()

    def tearDown(self) -> None:
        _clean_test_hypotheses()
        _clean_generated_specs()

    def test_cli_generate_hypotheses(self) -> None:
        out = StringIO()
        with redirect_stdout(out):
            rc = cli.main([
                "intake", "generate-hypotheses",
                "--theme", "H4 XAUUSD test",
                "--symbol", "XAUUSD",
                "--timeframe", "H4",
                "--market-regime", "ranging",
                "--strategy-family", "failed_breakout_reversal",
                "--max-hypotheses", "1",
            ])
        self.assertEqual(rc, 0)
        data = json.loads(out.getvalue())
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["hypothesis_id"], "HYP_GEN_FBR_RANGING_000")


if __name__ == "__main__":
    unittest.main()
