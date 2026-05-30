from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import implementation as impl_mod, registry
from .contracts import load_review_artifact, resolve_stored_path, spec_references_production_path
from .schemas import REPO_ROOT, SANDBOX_ROOT, GENERATED_SPECS_DIR, STRATEGIES_ROOT, load_yaml, resolve_hypothesis_file

GENERATED_CANDIDATE_DECISION_PACKET_SCHEMA = "generated_candidate_decision_packet_v1"

ALLOWED_PROPOSED_NEXT_ACTIONS = {
    "reject",
    "revise_strategy_spec",
    "revise_implementation",
    "run_additional_bounded_sweep",
    "request_human_review_for_final_holdout",
    "defer",
}

ALLOWED_LIFECYCLE_PROPOSALS = {
    "none",
    "research_candidate",
    "robustness_candidate",
    "final_holdout_candidate",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def has_final_holdout_run(db_path: str | Path, strategy_id: str, strategy_version: str) -> bool:
    all_experiments = registry.list_experiments(db_path)
    candidate_ids = [e["experiment_id"] for e in all_experiments if e.get("strategy_id") == strategy_id]
    for exp_id in candidate_ids:
        exp = registry.get_experiment(db_path, exp_id)
        if not exp:
            continue
        change_summary = (exp.get("change_summary") or "").lower()
        rationale = (exp.get("rationale") or "").lower()
        notes = (exp.get("notes") or "").lower()
        if "final_holdout" in change_summary or "final holdout" in change_summary or "final_holdout" in rationale or "final holdout" in rationale or "final_holdout" in notes or "final holdout" in notes:
            return True
    return False


def _find_final_holdout_experiment(db_path: str | Path, strategy_id: str, strategy_version: str) -> str | None:
    all_experiments = registry.list_experiments(db_path)
    candidate_ids = [e["experiment_id"] for e in all_experiments if e.get("strategy_id") == strategy_id]
    for exp_id in candidate_ids:
        exp = registry.get_experiment(db_path, exp_id)
        if not exp:
            continue
        cs = (exp.get("change_summary") or "").lower()
        if "final_holdout" in cs or "final holdout" in cs:
            return exp_id
    return None


def require_generated_candidate_decision_eligibility(
    db_path: str | Path,
    strategy_id: str,
    strategy_version: str,
    *,
    baseline_experiment_id: str | None = None,
    robustness_sweep_id: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    evidence_gaps: list[str] = []

    request = registry.find_implementation_request(db_path, strategy_id, strategy_version)
    if not request:
        errors.append("Implementation request not found")
        return {"eligible": False, "errors": errors, "warnings": warnings, "evidence_gaps": evidence_gaps}

    impl_request_id = request["implementation_request_id"]

    implementations = registry.list_implementations(db_path, impl_request_id)
    if not implementations:
        errors.append("No implementation record found")
        return {"eligible": False, "errors": errors, "warnings": warnings, "evidence_gaps": evidence_gaps}

    current_impl = implementations[-1]

    compile_status = current_impl.get("compile_status")
    if not compile_status:
        errors.append("Compile status is missing")
    elif compile_status == "failed":
        errors.append("Compile status is 'failed'")

    input_match = current_impl.get("input_match_status")
    if not input_match:
        errors.append("Input match status is not set")
    elif input_match == "mismatch":
        errors.append("Input match status is 'mismatch'")

    generated_mq5 = current_impl.get("generated_mq5_path", "")
    if generated_mq5:
        try:
            impl_mod.assert_sandbox_path(Path(generated_mq5))
        except Exception:
            errors.append(f"Generated .mq5 path outside sandbox: {generated_mq5}")
    else:
        errors.append("No generated .mq5 path recorded")

    spec_path = GENERATED_SPECS_DIR / f"{strategy_id}.yaml"
    if spec_path.is_file():
        try:
            offending = spec_references_production_path(spec_path, STRATEGIES_ROOT)
            if offending is not None:
                errors.append(f"Generated spec points to automated/strategies/ in {offending}")
        except Exception:
            evidence_gaps.append("Could not parse generated spec for sandbox check")

    prod_candidate = STRATEGIES_ROOT / f"{strategy_id}.mq5"
    if prod_candidate.is_file():
        warnings.append(f"Production strategy file exists at {prod_candidate}; candidate path may conflict")

    if baseline_experiment_id:
        baseline_exp = registry.get_experiment(db_path, baseline_experiment_id)
        if not baseline_exp:
            errors.append(f"Baseline experiment not found: {baseline_experiment_id}")
        elif not str(baseline_exp.get("status", "")).startswith("completed"):
            errors.append(f"Baseline experiment status is '{baseline_exp.get('status')}'; must be 'completed'")
    else:
        evidence_gaps.append("No baseline_experiment_id provided; cannot verify baseline completion")

    if baseline_experiment_id:
        artifacts_exp = registry.list_artifacts(db_path, baseline_experiment_id)
        baseline_review_found = any(
            art.get("artifact_type") == "generated_baseline_review" for art in artifacts_exp
        )
        if not baseline_review_found:
            errors.append(f"generated_baseline_review artifact not found for experiment {baseline_experiment_id}")

    if robustness_sweep_id:
        sweep = registry.get_sweep(db_path, robustness_sweep_id)
        if not sweep:
            errors.append(f"Robustness sweep not found: {robustness_sweep_id}")
        elif not str(sweep.get("status", "")).startswith("completed"):
            errors.append(f"Robustness sweep status is '{sweep.get('status')}'; must be 'completed'")
    else:
        evidence_gaps.append("No robustness_sweep_id provided; cannot verify robustness sweep completion")

    if robustness_sweep_id:
        sweep = registry.get_sweep(db_path, robustness_sweep_id)
        if sweep:
            parent_exp_id = sweep.get("parent_experiment_id")
            if parent_exp_id:
                artifacts_rob = registry.list_artifacts(db_path, parent_exp_id)
                robustness_review_found = any(
                    art.get("artifact_type") == "generated_robustness_review" for art in artifacts_rob
                )
                if not robustness_review_found:
                    errors.append(f"generated_robustness_review artifact not found for sweep {robustness_sweep_id}")

    if has_final_holdout_run(db_path, strategy_id, strategy_version):
        warnings.append("Final holdout has already been run for this strategy; including existing evidence")
        evidence_gaps.append("final_holdout_has_run")

    transitions = registry.list_lifecycle_transitions(db_path, strategy_id)
    applied = [t for t in transitions if t.get("status") == "applied"]
    if applied:
        errors.append(f"Lifecycle transition already applied: {applied[-1].get('transition_id', 'unknown')}")

    if baseline_experiment_id:
        baseline_exp = registry.get_experiment(db_path, baseline_experiment_id)
        if baseline_exp:
            exp_min_trades = baseline_exp.get("min_trades_required")
            if spec_path.is_file():
                try:
                    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
                    spec_min_trades = (spec.get("validation") or {}).get("min_trades_required")
                    if spec_min_trades is not None and exp_min_trades is not None:
                        if exp_min_trades < spec_min_trades:
                            warnings.append(
                                f"Validation threshold min_trades_required weakened: "
                                f"spec={spec_min_trades}, experiment={exp_min_trades}"
                            )
                    elif spec_min_trades is None or exp_min_trades is None:
                        evidence_gaps.append("Cannot compare validation thresholds: data missing")
                except Exception:
                    evidence_gaps.append("Could not parse spec for validation threshold comparison")

    if robustness_sweep_id:
        sweep = registry.get_sweep(db_path, robustness_sweep_id)
        if sweep:
            raw = sweep.get("config_json") or sweep.get("config") or {}
            config: dict[str, Any] = {}
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        config = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
            elif isinstance(raw, dict):
                config = raw
            if config.get("dataset_id"):
                evidence_gaps.append(f"Robustness sweep used different dataset: {config['dataset_id']}")
            if config.get("cost_multipliers") or config.get("cost_config"):
                warnings.append("Cost/slippage mutation detected in robustness sweep")

    if errors:
        return {
            "eligible": False,
            "errors": errors,
            "warnings": warnings,
            "evidence_gaps": evidence_gaps,
            "implementation_request_id": impl_request_id,
            "implementation_id": current_impl.get("implementation_id"),
        }

    return {
        "eligible": True,
        "errors": [],
        "warnings": warnings,
        "evidence_gaps": evidence_gaps,
        "implementation_request_id": impl_request_id,
        "implementation_id": current_impl.get("implementation_id"),
        "note": "Generated strategy is eligible for candidate decision packet",
    }


def _load_hypothesis(hypothesis_id: str) -> dict[str, Any] | None:
    path = resolve_hypothesis_file(hypothesis_id)
    if path and path.is_file():
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _proposed_next_action(
    eligibility: dict[str, Any],
    baseline_metrics: dict[str, Any] | None,
    validation_report: dict[str, Any] | None,
    baseline_review: dict[str, Any] | None,
    robustness_review: dict[str, Any] | None,
    evidence_gaps: list[str],
    final_holdout_summary: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    rules_applied: list[str] = []

    hard_failures = []
    if validation_report:
        hard_failures = validation_report.get("hard_failures", [])

    if hard_failures:
        rules_applied.append("baseline_hard_validation_failure: hard_failures detected in baseline validation")
        return "reject", rules_applied

    if final_holdout_summary:
        fh_status = final_holdout_summary.get("status", "")
        if fh_status == "fail":
            rules_applied.append("final_holdout_failed: final holdout experiment did not pass")
            return "reject", rules_applied
        if fh_status == "pass":
            rules_applied.append("final_holdout_passed: final holdout passed, human decision required for next steps")
            return "defer", rules_applied
        rules_applied.append("final_holdout_needs_review: final holdout status requires review")
        return "defer", rules_applied

    robustness_risk = None
    if robustness_review:
        red_team_rob = robustness_review.get("red_team_assessment") or {}
        robustness_risk = red_team_rob.get("overall_assessment", "unknown")
        rob_risk_flags = red_team_rob.get("risk_flags", [])

        if robustness_risk == "high_risk":
            if "negative_net_return" in rob_risk_flags or "profit_factor_below_breakeven" in rob_risk_flags:
                rules_applied.append("robustness_high_risk_with_fatal_flags: robustness review is high risk with negative returns or PF<1")
                return "reject", rules_applied
            rules_applied.append("robustness_high_risk: robustness review assessment is high risk")
            return "revise_strategy_spec", rules_applied

    baseline_risk = None
    if baseline_review:
        red_team_bl = baseline_review.get("red_team_results") or {}
        baseline_risk = red_team_bl.get("overall_assessment", "unknown")

    baseline_bl_recommendation = None
    if baseline_review:
        rec = baseline_review.get("recommendation")
        if rec in ("reject", "revise_strategy_spec", "revise_implementation"):
            rules_applied.append(f"baseline_review_recommendation: baseline review recommends {rec}")
            return rec, rules_applied

    robustness_rec = None
    if robustness_review:
        robustness_rec = robustness_review.get("recommendation")
        if robustness_rec == "consider_lifecycle_candidate":
            rules_applied.append("robustness_considers_lifecycle_candidate: robustness review recommends lifecycle consideration")
            return "request_human_review_for_final_holdout", rules_applied

    baseline_pass = False
    if baseline_metrics:
        pf = baseline_metrics.get("profit_factor")
        nr = baseline_metrics.get("net_return")
        if pf is not None and nr is not None and pf >= 1.0 and nr > 0:
            baseline_pass = True

    if evidence_gaps:
        rules_applied.append("evidence_gaps: incomplete evidence prevents confident decision")
        return "defer", rules_applied

    if robustness_review and robustness_risk in ("low_risk", "medium_risk") and baseline_pass:
        rules_applied.append("baseline_passed_robustness_low_medium_risk: baseline passed and robustness risk is acceptable")
        return "request_human_review_for_final_holdout", rules_applied

    if not robustness_review and baseline_pass:
        rules_applied.append("only_baseline_evidence_no_robustness: baseline exists but no robustness sweep evidence")
        return "run_additional_bounded_sweep", rules_applied

    if robustness_review and robustness_risk == "medium_risk" and baseline_pass:
        rules_applied.append("robustness_medium_risk_baseline_passed: robustness medium risk, consider additional sweep")
        return "run_additional_bounded_sweep", rules_applied

    rules_applied.append("fallback_defer: no rule triggered a definitive next action")
    return "defer", rules_applied


def _lifecycle_proposal(
    proposed_next_action: str,
    robustness_review: dict[str, Any] | None,
    evidence_gaps: list[str],
) -> str:
    if proposed_next_action == "reject":
        return "none"
    if proposed_next_action == "request_human_review_for_final_holdout":
        return "final_holdout_candidate"
    if proposed_next_action == "revise_strategy_spec":
        return "research_candidate"
    if proposed_next_action == "revise_implementation":
        return "research_candidate"
    if evidence_gaps and not robustness_review:
        return "research_candidate"
    if robustness_review:
        return "robustness_candidate"
    return "none"


def build_generated_candidate_decision_packet(
    db_path: str | Path,
    *,
    strategy_id: str,
    strategy_version: str,
    implementation_request_id: str | None = None,
    baseline_experiment_id: str | None = None,
    robustness_sweep_id: str | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    eligibility = require_generated_candidate_decision_eligibility(
        db_path,
        strategy_id,
        strategy_version,
        baseline_experiment_id=baseline_experiment_id,
        robustness_sweep_id=robustness_sweep_id,
    )
    if not eligibility["eligible"]:
        raise ValueError(
            f"Cannot build decision packet: {'; '.join(eligibility['errors'])}"
        )

    request = registry.find_implementation_request(db_path, strategy_id, strategy_version)
    impl_request_id = request["implementation_request_id"] if request else implementation_request_id
    implementations = registry.list_implementations(db_path, impl_request_id) if impl_request_id else []
    current_impl = implementations[-1] if implementations else None

    spec_path = GENERATED_SPECS_DIR / f"{strategy_id}.yaml"
    spec: dict[str, Any] = {}
    if spec_path.is_file():
        try:
            spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
        except Exception:
            spec = {}

    hypothesis_id = spec.get("hypothesis_id", "")
    hypothesis_data = _load_hypothesis(hypothesis_id) if hypothesis_id else None

    hypothesis_summary: dict[str, Any] = {"hypothesis_id": hypothesis_id}
    if hypothesis_data:
        hypothesis_summary["description"] = hypothesis_data.get("description") or hypothesis_data.get("hypothesis_description", "")
        hypothesis_summary["theme"] = hypothesis_data.get("research_theme", "")
        hypothesis_summary["market_regime"] = hypothesis_data.get("market_regime", "")

    spec_summary: dict[str, Any] = {
        "strategy_id": spec.get("strategy_id", strategy_id),
        "universe": spec.get("universe", []),
        "timeframe": spec.get("timeframe", ""),
        "parameters_count": len(spec.get("parameters", {})),
        "filters_count": len(spec.get("filters", [])),
        "generation_mode": spec.get("implementation", {}).get("generation_mode", ""),
    }

    implementation_summary: dict[str, Any] = {
        "implementation_request_id": impl_request_id,
        "implementation_id": current_impl.get("implementation_id") if current_impl else None,
        "compile_status": current_impl.get("compile_status") if current_impl else None,
        "input_match_status": current_impl.get("input_match_status") if current_impl else None,
        "sandboxed": bool(current_impl and current_impl.get("generated_mq5_path", "")),
    }
    if current_impl:
        mq5 = current_impl.get("generated_mq5_path", "")
        if mq5:
            try:
                in_sandbox = str(Path(mq5).resolve()).startswith(str(SANDBOX_ROOT.resolve()))
                implementation_summary["sandboxed"] = in_sandbox
            except Exception:
                pass

    baseline_metrics: dict[str, Any] = {}
    baseline_exp = registry.get_experiment(db_path, baseline_experiment_id) if baseline_experiment_id else None
    if baseline_exp:
        metrics_raw = baseline_exp.get("headline_metrics_json") or "{}"
        try:
            baseline_metrics = json.loads(metrics_raw) if isinstance(metrics_raw, str) else (metrics_raw or {})
        except (json.JSONDecodeError, TypeError):
            baseline_metrics = {}
    baseline_summary: dict[str, Any] = {
        "experiment_id": baseline_experiment_id,
        "net_return": baseline_metrics.get("net_return"),
        "profit_factor": baseline_metrics.get("profit_factor"),
        "trade_count": baseline_metrics.get("trade_count"),
        "max_drawdown": baseline_metrics.get("max_drawdown"),
        "gate_status": baseline_exp.get("gate_status") if baseline_exp else None,
        "status": baseline_exp.get("status") if baseline_exp else None,
    }

    baseline_review = None
    if baseline_experiment_id:
        baseline_review = load_review_artifact(db_path, baseline_experiment_id, "generated_baseline_review", REPO_ROOT)

    child_summaries: list[dict[str, Any]] = []
    child_completed = 0
    child_failed = 0
    child_pfs: list[float] = []
    child_nrs: list[float] = []
    if robustness_sweep_id:
        sweep = registry.get_sweep(db_path, robustness_sweep_id)
        if sweep:
            children = registry.list_sweep_children(db_path, robustness_sweep_id)
            for child in children:
                cid = child.get("child_experiment_id", "")
                status = child.get("status", "")
                c_metrics = registry.get_experiment_metrics(db_path, cid)
                pf = (c_metrics or {}).get("profit_factor")
                nr = (c_metrics or {}).get("net_return")
                if pf is not None:
                    child_pfs.append(pf)
                if nr is not None:
                    child_nrs.append(nr)
                if status == "completed" or nr is not None:
                    child_completed += 1
                elif status == "failed":
                    child_failed += 1
                child_summaries.append({
                    "child_experiment_id": cid,
                    "status": status,
                    "profit_factor": pf,
                    "net_return": nr,
                })

    from statistics import median
    robustness_summary: dict[str, Any] = {
        "sweep_id": robustness_sweep_id,
        "children_completed": child_completed,
        "children_failed": child_failed,
        "median_profit_factor": median(child_pfs) if child_pfs else None,
        "median_net_return": median(child_nrs) if child_nrs else None,
        "child_count": len(child_summaries),
    }

    robustness_review = None
    if robustness_sweep_id:
        sweep = registry.get_sweep(db_path, robustness_sweep_id)
        if sweep and sweep.get("parent_experiment_id"):
            robustness_review = load_review_artifact(db_path, sweep["parent_experiment_id"], "generated_robustness_review", REPO_ROOT)

    final_holdout_summary: dict[str, Any] = {}
    if has_final_holdout_run(db_path, strategy_id, strategy_version):
        fh_exp_id = _find_final_holdout_experiment(db_path, strategy_id, strategy_version)
        if fh_exp_id:
            fh_review = load_review_artifact(db_path, fh_exp_id, "generated_final_holdout_review", REPO_ROOT)
            if fh_review:
                final_holdout_summary = {
                    "experiment_id": fh_exp_id,
                    "approval_id": fh_review.get("approval_id"),
                    "metrics": fh_review.get("metrics_summary"),
                    "status": fh_review.get("status"),
                    "invariant_checks": fh_review.get("invariant_checks"),
                }

    validation_report: dict[str, Any] | None = None
    if baseline_exp:
        vrp = baseline_exp.get("validation_report_path")
        if vrp:
            vrp_path = resolve_stored_path(vrp, REPO_ROOT)
            if vrp_path.is_file():
                try:
                    data = json.loads(vrp_path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        validation_report = data
                except (json.JSONDecodeError, FileNotFoundError):
                    pass

    validation_summary: dict[str, Any] = {
        "hard_failures": validation_report.get("hard_failures", []) if validation_report else [],
        "warnings": validation_report.get("warnings", []) if validation_report else [],
        "gate_status": validation_report.get("gate_status", "not_available") if validation_report else "not_available",
    }

    red_team_summary: dict[str, Any] = {
        "baseline_assessment": (baseline_review or {}).get("red_team_results", {}).get("overall_assessment", "not_available"),
        "robustness_assessment": (robustness_review or {}).get("red_team_assessment", {}).get("overall_assessment", "not_available"),
    }
    all_risk_flags: list[str] = []
    if baseline_review:
        all_risk_flags.extend((baseline_review.get("red_team_results") or {}).get("risk_flags", []))
    if robustness_review:
        all_risk_flags.extend((robustness_review.get("red_team_assessment") or {}).get("risk_flags", []))
    red_team_summary["all_risk_flags"] = list(set(all_risk_flags))
    red_team_summary["baseline_warnings"] = (baseline_review or {}).get("red_team_results", {}).get("warnings", []),
    red_team_summary["robustness_warnings"] = (robustness_review or {}).get("robustness_warnings", []),

    unresolved_warnings: list[str] = []
    unresolved_warnings.extend(eligibility.get("warnings", []))
    if baseline_review:
        for w in (baseline_review.get("red_team_results") or {}).get("warnings", []):
            unresolved_warnings.append(f"baseline red-team: {w}")
    if robustness_review:
        for w in (robustness_review.get("robustness_warnings") or []):
            unresolved_warnings.append(f"robustness: {w}")

    evidence_gaps: list[str] = list(eligibility.get("evidence_gaps", []))

    artifact_paths: dict[str, Any] = {
        "hypothesis": str(resolve_hypothesis_file(hypothesis_id)) if hypothesis_id else None,
        "spec": str(spec_path) if spec_path.is_file() else None,
        "implementation_request": str(REPO_ROOT / "automated" / "implementation_requests" / impl_request_id) if impl_request_id else None,
        "baseline_experiment": f"experiment_id:{baseline_experiment_id}" if baseline_experiment_id else None,
        "baseline_review": None,
        "robustness_sweep": f"sweep_id:{robustness_sweep_id}" if robustness_sweep_id else None,
        "robustness_review": None,
        "validation_reports": [],
    }
    if baseline_review_art := load_review_artifact(db_path, baseline_experiment_id, "generated_baseline_review", REPO_ROOT):
        pass
    if baseline_experiment_id:
        for art in registry.list_artifacts(db_path, baseline_experiment_id):
            if art.get("artifact_type") == "generated_baseline_review":
                artifact_paths["baseline_review"] = resolve_stored_path(art["path"], REPO_ROOT)
            if art.get("artifact_type") == "validation_report":
                artifact_paths["validation_reports"].append(str(resolve_stored_path(art["path"], REPO_ROOT)))
    if robustness_sweep_id:
        sweep = registry.get_sweep(db_path, robustness_sweep_id)
        if sweep and sweep.get("parent_experiment_id"):
            for art in registry.list_artifacts(db_path, sweep["parent_experiment_id"]):
                if art.get("artifact_type") == "generated_robustness_review":
                    artifact_paths["robustness_review"] = str(resolve_stored_path(art["path"], REPO_ROOT))

    artifact_paths = {k: str(v) if not isinstance(v, list) else v for k, v in artifact_paths.items()}

    proposed_next_action, rules_applied = _proposed_next_action(
        eligibility=eligibility,
        baseline_metrics=baseline_metrics,
        validation_report=validation_report,
        baseline_review=baseline_review,
        robustness_review=robustness_review,
        evidence_gaps=evidence_gaps,
        final_holdout_summary=final_holdout_summary,
    )

    lifecycle_proposal = _lifecycle_proposal(
        proposed_next_action=proposed_next_action,
        robustness_review=robustness_review,
        evidence_gaps=evidence_gaps,
    )

    packet: dict[str, Any] = {
        "schema_version": GENERATED_CANDIDATE_DECISION_PACKET_SCHEMA,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "hypothesis_summary": hypothesis_summary,
        "spec_summary": spec_summary,
        "implementation_summary": implementation_summary,
        "baseline_summary": baseline_summary,
        "robustness_summary": robustness_summary,
        "final_holdout_summary": final_holdout_summary,
        "validation_summary": validation_summary,
        "red_team_summary": red_team_summary,
        "unresolved_warnings": unresolved_warnings,
        "evidence_gaps": evidence_gaps,
        "artifact_paths": artifact_paths,
        "candidate_status": "eligible",
        "proposed_next_action": proposed_next_action,
        "lifecycle_proposal": lifecycle_proposal,
        "decision_rules_applied": rules_applied,
        "created_at": utc_now(),
    }

    out_dir = Path(output_dir) if output_dir else (
        REPO_ROOT / "automated" / "research_runs" / f"generated_candidate_{_slug(strategy_id)[:48]}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    packet_path = out_dir / "generated_candidate_decision_packet.yaml"
    packet_path.write_text(yaml.safe_dump(packet, sort_keys=False), encoding="utf-8")

    return {
        "packet_path": str(packet_path),
        "packet": packet,
    }


def _slug(value: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
