from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import yaml

from . import registry
from .contracts import (
    ALLOWED_LIFECYCLE_TRANSITIONS,
    LIFECYCLE_STATES,
    STRICTNESS_MODES,
    resolve_stored_path,
)
from .schemas import REPO_ROOT, SchemaValidationError, load_yaml, resolve_hypothesis_file, validate_strategy_spec


ACTIVE_STATES = set(LIFECYCLE_STATES) - {"retired", "archived"}
ALLOWED_TRANSITIONS = ALLOWED_LIFECYCLE_TRANSITIONS


def resolve_strategy_spec(strategy: str | Path) -> Path:
    path = Path(strategy)
    if path.is_file():
        return path
    if not path.suffix:
        candidate = REPO_ROOT / "automated" / "specs" / "strategies" / f"{strategy}.yaml"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"strategy spec not found: {strategy}")


def load_strategy(strategy: str | Path) -> tuple[Path, dict[str, Any]]:
    path = resolve_strategy_spec(strategy)
    data = load_yaml(path)
    validate_strategy_spec(data)
    state = data.get("lifecycle", {}).get("state")
    if state not in LIFECYCLE_STATES:
        raise SchemaValidationError(f"strategy.lifecycle.state must be one of {LIFECYCLE_STATES}")
    return path, data


def _artifact_by_type(db_path: str | Path, experiment_id: str | None) -> dict[str, dict[str, Any]]:
    if not experiment_id:
        return {}
    latest: dict[str, dict[str, Any]] = {}
    for artifact in registry.list_artifacts(db_path, experiment_id):
        latest[artifact["artifact_type"]] = artifact
    return latest


def _load_json_artifact(artifacts: dict[str, dict[str, Any]], artifact_type: str) -> dict[str, Any] | None:
    artifact = artifacts.get(artifact_type)
    if not artifact:
        return None
    path = resolve_stored_path(artifact["path"], REPO_ROOT)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = yaml.safe_load(text)
    return data if isinstance(data, dict) else None


def _hard_validation_failures(report: dict[str, Any] | None) -> list[str]:
    if not report:
        return ["validation report is missing"]
    failures = report.get("hard_failures")
    if isinstance(failures, list):
        return [str(item) for item in failures]
    if report.get("gate_status") == "fail":
        return ["validation report gate_status is fail"]
    return []


def _portfolio_status(report: dict[str, Any] | None) -> str | None:
    if not report:
        return None
    status = report.get("status")
    return str(status) if status else None


def _review_decision(report: dict[str, Any] | None) -> str | None:
    if not report:
        return None
    decision = report.get("decision")
    return str(decision) if decision else None


def _gate(requirement: str, status: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"requirement": requirement, "status": status, "reason": reason, **extra}


def _missing_advanced_status(strictness: str) -> str:
    return "block" if strictness == "strict" else "warn"


def _required_artifact_status(strictness: str) -> str:
    return "warn" if strictness == "lenient" else "block"


def evaluate_transition(
    db_path: str | Path,
    *,
    strategy: str | Path,
    to_state: str,
    experiment_id: str | None = None,
    strictness: str = "normal",
    override: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    if strictness not in STRICTNESS_MODES:
        raise ValueError(f"strictness must be one of {sorted(STRICTNESS_MODES)}")
    if to_state not in LIFECYCLE_STATES:
        raise SchemaValidationError(f"to_state must be one of {LIFECYCLE_STATES}")
    spec_path, spec = load_strategy(strategy)
    strategy_id = spec["strategy_id"]
    from_state = spec["lifecycle"]["state"]
    requirements: list[dict[str, Any]] = []

    allowed = to_state in ALLOWED_TRANSITIONS.get(from_state, set())
    if allowed:
        requirements.append(_gate("allowed_transition", "pass", f"{from_state} -> {to_state} is allowed"))
    elif override and reason:
        requirements.append(_gate("allowed_transition", "warn", f"{from_state} -> {to_state} uses override", override=True))
    else:
        requirements.append(_gate("allowed_transition", "block", f"{from_state} -> {to_state} is not an allowed transition"))

    artifacts = _artifact_by_type(db_path, experiment_id)
    validation_report = _load_json_artifact(artifacts, "validation_report")
    portfolio_report = _load_json_artifact(artifacts, "portfolio_report")
    statistical_review = _load_json_artifact(artifacts, "statistical_review")
    portfolio_review = _load_json_artifact(artifacts, "portfolio_review")
    red_team_review = _load_json_artifact(artifacts, "red_team_review")
    risk_execution_review = _load_json_artifact(artifacts, "risk_execution_review")
    paper_trading_report = _load_json_artifact(artifacts, "paper_trading_report")
    production_readiness = _load_json_artifact(artifacts, "production_readiness_report")

    if to_state == "baseline_testing":
        requirements.extend(_baseline_testing_gates(spec))
    elif to_state == "robustness_testing":
        requirements.extend(_robustness_testing_gates(db_path, experiment_id, validation_report, spec))
    elif to_state == "stat_review":
        requirements.extend(_stat_review_gates(validation_report, strictness))
    elif to_state == "portfolio_review":
        requirements.extend(_portfolio_review_gates(validation_report, statistical_review, strictness))
    elif to_state == "paper_trading":
        requirements.extend(
            _paper_trading_gates(
                portfolio_report=portfolio_report,
                portfolio_review=portfolio_review,
                red_team_review=red_team_review,
                risk_execution_review=risk_execution_review,
                spec=spec,
                strictness=strictness,
            )
        )
    elif to_state == "incubation_capital":
        requirements.extend(_incubation_gates(paper_trading_report, strictness))
    elif to_state == "production":
        requirements.extend(_production_gates(production_readiness, spec, strictness))

    blockers = [item for item in requirements if item["status"] == "block"]
    warnings = [item for item in requirements if item["status"] == "warn"]
    return {
        "strategy_id": strategy_id,
        "strategy_spec_path": str(spec_path),
        "from_state": from_state,
        "to_state": to_state,
        "experiment_id": experiment_id,
        "strictness": strictness,
        "status": "blocked" if blockers else ("warn" if warnings else "pass"),
        "requirements": requirements,
        "blockers": blockers,
        "warnings": warnings,
        "mutated": False,
    }


def _baseline_testing_gates(spec: dict[str, Any]) -> list[dict[str, Any]]:
    gates = [
        _gate(
            "hypothesis_exists",
            "pass" if resolve_hypothesis_file(spec["hypothesis_id"]) else "block",
            "hypothesis resolves to a YAML record" if resolve_hypothesis_file(spec["hypothesis_id"]) else "hypothesis record is missing",
            hypothesis_id=spec["hypothesis_id"],
        ),
        _gate("strategy_spec_exists", "pass", "strategy spec loaded and validated"),
        _gate("dataset_assumptions_documented", "pass", "dataset assumptions are represented by dataset registry metadata"),
    ]
    files = spec.get("implementation", {}).get("files", {})
    missing = [key for key in ["config", "parameters", "expert_advisor"] if not files.get(key)]
    gates.append(
        _gate(
            "implementation_files_referenced",
            "block" if missing else "pass",
            f"missing implementation file references: {', '.join(missing)}" if missing else "implementation files are referenced",
        )
    )
    return gates


def _robustness_testing_gates(
    db_path: str | Path,
    experiment_id: str | None,
    validation_report: dict[str, Any] | None,
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    experiment = registry.get_experiment(db_path, experiment_id) if experiment_id else None
    hard_failures = _hard_validation_failures(validation_report)
    trade_count = None
    if validation_report:
        trade_count = (
            validation_report.get("sections", {})
            .get("sample_size_gate", {})
            .get("total_trades")
        )
    return [
        _gate(
            "completed_experiment",
            "pass" if experiment and str(experiment.get("status", "")).startswith("completed") else "block",
            "experiment is completed" if experiment and str(experiment.get("status", "")).startswith("completed") else "a completed experiment is required",
            experiment_status=experiment.get("status") if experiment else None,
        ),
        _gate(
            "validation_report_exists",
            "pass" if validation_report else "block",
            "validation report is attached" if validation_report else "validation report is missing",
        ),
        _gate(
            "no_hard_validation_failures",
            "block" if hard_failures else "pass",
            "; ".join(hard_failures) if hard_failures else "validation report has no hard failures",
        ),
        _gate(
            "trade_count_parsed",
            "pass" if trade_count is not None else "warn",
            "trade count parsed" if trade_count is not None else "trade count is unavailable; review validation report reason",
            total_trades=trade_count,
        ),
        _gate(
            "cost_assumptions_documented",
            "pass" if spec.get("costs", {}).get("assumptions_documented") is True else "block",
            "cost assumptions documented" if spec.get("costs", {}).get("assumptions_documented") is True else "cost assumptions missing",
        ),
        _gate(
            "execution_assumptions_documented",
            "pass" if all(spec.get("execution_timing", {}).get(key) for key in ["signal_bar", "entry_bar", "assumed_fill_price"]) else "block",
            "execution assumptions documented",
        ),
    ]


def _stat_review_gates(validation_report: dict[str, Any] | None, strictness: str) -> list[dict[str, Any]]:
    hard_failures = _hard_validation_failures(validation_report)
    advanced_gates = validation_report.get("sections", {}).get("placeholder_advanced_gates", {}) if validation_report else {}
    robustness = advanced_gates.get("parameter_robustness")
    walk_forward = advanced_gates.get("walk_forward")
    robustness_status = robustness.get("status") if isinstance(robustness, dict) else None
    walk_forward_status = walk_forward.get("status") if isinstance(walk_forward, dict) else None
    missing_status = _missing_advanced_status(strictness)
    not_implemented_status = "block" if strictness == "strict" else "warn"
    robustness_block = robustness_status in {"fail"} or (strictness == "strict" and robustness_status in {"not_implemented", "not_available", "warn"})
    walk_forward_block = strictness == "strict" and walk_forward_status in {"not_implemented", "not_available", "warn"}
    return [
        _gate(
            "validation_report_exists",
            "pass" if validation_report else "block",
            "validation report exists" if validation_report else "validation report is missing",
        ),
        _gate(
            "robustness_section_present",
            "pass" if robustness else missing_status,
            "robustness section exists" if robustness else "robustness section missing",
            robustness_status=robustness_status,
        ),
        _gate(
            "robustness_implemented",
            "block" if robustness_block else not_implemented_status if robustness_status == "not_implemented" else "pass" if robustness else missing_status,
            f"parameter robustness status is {robustness_status}" if robustness_block else "parameter robustness is available",
            robustness_status=robustness_status,
        ),
        _gate(
            "walk_forward_implemented",
            "block" if walk_forward_block else not_implemented_status if walk_forward_status == "not_implemented" else "pass" if walk_forward else missing_status,
            f"walk-forward validation status is {walk_forward_status}" if walk_forward_block else "walk-forward validation is available",
            walk_forward_status=walk_forward_status,
        ),
        _gate(
            "no_known_hard_failure",
            "block" if hard_failures else "pass",
            "; ".join(hard_failures) if hard_failures else "no known hard validation failure",
        ),
    ]


def _portfolio_review_gates(
    validation_report: dict[str, Any] | None,
    statistical_review: dict[str, Any] | None,
    strictness: str,
) -> list[dict[str, Any]]:
    hard_failures = _hard_validation_failures(validation_report)
    artifact_status = _required_artifact_status(strictness)
    decision = _review_decision(statistical_review)
    return [
        _gate(
            "statistical_review_artifact",
            "pass" if statistical_review else artifact_status,
            "statistical review is attached" if statistical_review else "statistical review artifact is missing",
            decision=decision,
        ),
        _gate(
            "statistical_review_decision",
            "block" if decision == "fail" else "pass" if decision else artifact_status,
            "statistical review did not fail" if decision != "fail" else "statistical review failed",
            decision=decision,
        ),
        _gate(
            "validation_report_exists",
            "pass" if validation_report else "block",
            "validation report exists" if validation_report else "validation report is missing",
        ),
        _gate(
            "no_unresolved_hard_failures",
            "block" if hard_failures else "pass",
            "; ".join(hard_failures) if hard_failures else "no unresolved hard validation failures",
        ),
    ]


def _paper_trading_gates(
    *,
    portfolio_report: dict[str, Any] | None,
    portfolio_review: dict[str, Any] | None,
    red_team_review: dict[str, Any] | None,
    risk_execution_review: dict[str, Any] | None,
    spec: dict[str, Any],
    strictness: str,
) -> list[dict[str, Any]]:
    artifact_status = _required_artifact_status(strictness)
    portfolio_status = _portfolio_status(portfolio_report)
    red_decision = _review_decision(red_team_review)
    portfolio_decision = _review_decision(portfolio_review)
    risk_decision = _review_decision(risk_execution_review)
    portfolio_allowed = portfolio_status == "pass" or (portfolio_status == "warn" and strictness != "strict")
    duplicate_warning = bool(portfolio_review and portfolio_review.get("duplicate_exposure_warning"))
    return [
        _gate(
            "portfolio_report_exists",
            "pass" if portfolio_report else artifact_status,
            "portfolio report is attached" if portfolio_report else "portfolio report is missing",
            portfolio_status=portfolio_status,
        ),
        _gate(
            "portfolio_status",
            "pass" if portfolio_allowed else "block" if portfolio_status == "fail" or strictness == "strict" else artifact_status,
            "portfolio status is acceptable for strictness" if portfolio_allowed else "portfolio status blocks promotion",
            portfolio_status=portfolio_status,
        ),
        _gate(
            "portfolio_review_artifact",
            "pass" if portfolio_review else artifact_status,
            "portfolio review is attached" if portfolio_review else "portfolio review artifact is missing",
            decision=portfolio_decision,
        ),
        _gate(
            "no_duplicate_exposure_hard_fail",
            "block" if duplicate_warning and strictness != "lenient" else "pass",
            "duplicate exposure warning blocks promotion" if duplicate_warning and strictness != "lenient" else "no duplicate exposure hard fail",
        ),
        _gate(
            "red_team_review_artifact",
            "pass" if red_team_review else artifact_status,
            "red-team review is attached" if red_team_review else "red-team review artifact is missing",
            decision=red_decision,
        ),
        _gate(
            "no_red_team_rejection",
            "block" if red_decision == "reject" and strictness != "lenient" else "pass",
            "red-team rejection blocks promotion" if red_decision == "reject" and strictness != "lenient" else "no red-team hard rejection",
            decision=red_decision,
        ),
        _gate(
            "risk_execution_review",
            "block" if risk_decision == "fail" else "pass" if risk_execution_review else "warn",
            "risk/execution review did not fail" if risk_decision != "fail" else "risk/execution review failed",
            decision=risk_decision,
        ),
        _gate(
            "invalidation_rule_documented",
            "pass" if spec.get("invalidation_rule") else "block",
            "invalidation rule documented" if spec.get("invalidation_rule") else "invalidation rule missing",
        ),
    ]


def _incubation_gates(report: dict[str, Any] | None, strictness: str) -> list[dict[str, Any]]:
    status = _missing_advanced_status(strictness)
    return [
        _gate(
            "paper_trading_report",
            "pass" if report else status,
            "paper-trading report exists" if report else "paper-trading report not implemented or missing",
        ),
        _gate(
            "live_paper_assumptions_checked",
            "pass" if report and report.get("live_paper_assumptions_checked") else status,
            "live/paper assumptions checked" if report and report.get("live_paper_assumptions_checked") else "live/paper assumptions check is missing",
        ),
    ]


def _production_gates(report: dict[str, Any] | None, spec: dict[str, Any], strictness: str) -> list[dict[str, Any]]:
    status = _missing_advanced_status(strictness)
    risk_limits = bool(spec.get("risk"))
    return [
        _gate(
            "production_readiness_report",
            "pass" if report else status,
            "production readiness report exists" if report else "production readiness report not implemented or missing",
        ),
        _gate("risk_limits_documented", "pass" if risk_limits else "block", "risk limits documented" if risk_limits else "risk limits missing"),
        _gate(
            "monitoring_config_documented",
            "pass" if report and report.get("monitoring_config_documented") else status,
            "monitoring config documented" if report and report.get("monitoring_config_documented") else "monitoring config is missing",
        ),
    ]


def propose_transition(
    db_path: str | Path,
    *,
    strategy: str | Path,
    to_state: str,
    experiment_id: str | None,
    reason: str,
    requested_by: str = "human",
    approved_by: str | None = None,
    strictness: str = "normal",
    override: bool = False,
    notes: str | None = None,
    snapshot_dir: str | Path | None = None,
) -> dict[str, Any]:
    evaluation = evaluate_transition(
        db_path,
        strategy=strategy,
        to_state=to_state,
        experiment_id=experiment_id,
        strictness=strictness,
        override=override,
        reason=reason,
    )
    transition_id = f"TRANSITION_{uuid.uuid4().hex[:12].upper()}"
    output_dir = Path(snapshot_dir) if snapshot_dir else REPO_ROOT / "automated" / "lifecycle" / evaluation["strategy_id"]
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = output_dir / f"{transition_id}_gate_snapshot.json"
    snapshot_path.write_text(json.dumps(evaluation, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    record = {
        "transition_id": transition_id,
        "strategy_id": evaluation["strategy_id"],
        "strategy_spec_path": evaluation["strategy_spec_path"],
        "from_state": evaluation["from_state"],
        "to_state": to_state,
        "experiment_id": experiment_id,
        "requested_by": requested_by,
        "approved_by": approved_by,
        "reason": reason,
        "gate_snapshot_path": str(snapshot_path),
        "created_at": registry.utc_now(),
        "status": "proposed",
        "notes": notes,
        "override": override,
    }
    registry.create_lifecycle_transition(db_path, record)
    return {"transition": record, "evaluation": evaluation}


def apply_transition(
    db_path: str | Path,
    *,
    transition_id: str,
    strictness: str = "normal",
) -> dict[str, Any]:
    transition = registry.get_lifecycle_transition(db_path, transition_id)
    if not transition:
        raise ValueError(f"transition not found: {transition_id}")
    if transition["status"] == "applied":
        return {"transition": transition, "status": "already_applied"}
    spec_path, spec = load_strategy(transition.get("strategy_spec_path") or transition["strategy_id"])
    evaluation = evaluate_transition(
        db_path,
        strategy=spec_path,
        to_state=transition["to_state"],
        experiment_id=transition.get("experiment_id"),
        strictness=strictness,
        override=bool(transition.get("override")),
        reason=transition.get("reason"),
    )
    if evaluation["status"] == "blocked":
        registry.update_lifecycle_transition(db_path, transition_id, status="rejected")
        return {"transition": registry.get_lifecycle_transition(db_path, transition_id), "evaluation": evaluation, "status": "blocked"}

    lifecycle = dict(spec.get("lifecycle", {}))
    lifecycle["state"] = transition["to_state"]
    lifecycle["last_transition_id"] = transition_id
    lifecycle["updated_at"] = registry.utc_now()
    if transition.get("experiment_id"):
        lifecycle["current_approved_experiment_id"] = transition["experiment_id"]
    lifecycle["allowed_next_states"] = sorted(ALLOWED_TRANSITIONS.get(transition["to_state"], set()))
    spec["lifecycle"] = lifecycle
    spec["status"] = transition["to_state"]
    spec["updated_at"] = registry.utc_now().split("T")[0]
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    registry.update_lifecycle_transition(db_path, transition_id, status="applied")
    return {
        "transition": registry.get_lifecycle_transition(db_path, transition_id),
        "evaluation": evaluation,
        "status": "applied",
        "strategy_spec_path": str(spec_path),
    }


def show_lifecycle(strategy: str | Path) -> dict[str, Any]:
    spec_path, spec = load_strategy(strategy)
    lifecycle = dict(spec.get("lifecycle", {}))
    return {
        "strategy_id": spec["strategy_id"],
        "strategy_spec_path": str(spec_path),
        "strategy_status": spec.get("status"),
        "lifecycle": lifecycle,
        "model": {
            "strategy": "broad lifecycle state",
            "experiment": "gate_status and status in experiment registry",
            "transition": "promotion/demotion decision record",
        },
    }
