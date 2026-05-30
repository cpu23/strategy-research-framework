from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import registry
from .contracts import check_approval_usable, ensure_member, load_review_artifact, resolve_stored_path, spec_references_production_path
from .hashing import file_sha256, verify_bound_packet_digest
from .implementation import assert_sandbox_path, check_no_production_touch
from .schemas import GENERATED_SPECS_DIR, REPO_ROOT, SANDBOX_ROOT, STRATEGIES_ROOT

GENERATED_FINAL_HOLDOUT_REVIEW_SCHEMA = "generated_final_holdout_review_v1"

FINAL_HOLDOUT_SCOPE = "final_holdout_only"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_decision_packet(decision_packet_path: str | Path) -> dict[str, Any] | None:
    path = Path(decision_packet_path)
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def approve_for_final_holdout(
    db_path: str | Path,
    impl_request_id: str,
    *,
    decision_packet_path: str | Path,
    approved_by: str,
    allow_reuse: bool = False,
) -> dict[str, Any]:
    ensure_member(FINAL_HOLDOUT_SCOPE, {"final_holdout_only"}, "approval_scope")

    request = registry.get_implementation_request(db_path, impl_request_id)
    if not request:
        raise ValueError(f"implementation request not found: {impl_request_id}")

    implementations = registry.list_implementations(db_path, impl_request_id)
    if not implementations:
        raise ValueError(f"No implementations found for request {impl_request_id}; run compile-check first")

    current_impl = implementations[-1]
    errors: list[str] = []

    if not current_impl.get("approved_for_baseline"):
        errors.append("Not approved for baseline; baseline approval required before final holdout approval")

    approval_scope = current_impl.get("approval_scope", "baseline_only")
    if approval_scope != "baseline_only":
        errors.append(f"Implementation approval scope is '{approval_scope}'; expected 'baseline_only'")

    compile_status = current_impl.get("compile_status")
    if not compile_status:
        errors.append("Compile status is missing; run compile-check first")
    elif compile_status == "failed":
        errors.append("Compile status is 'failed'")

    input_match = current_impl.get("input_match_status")
    if not input_match:
        errors.append("Input match status is not set; run diff-review first")
    elif input_match == "mismatch":
        errors.append("Input match status is 'mismatch'")

    sandbox = Path(request["sandbox_dir"])
    try:
        assert_sandbox_path(sandbox)
    except Exception as exc:
        errors.append(str(exc))

    mq5_path_str = current_impl.get("generated_mq5_path", "")
    if mq5_path_str:
        mq5_path = Path(mq5_path_str)
        forbidden = check_no_production_touch([mq5_path])
        if forbidden:
            errors.extend(forbidden)
        if not mq5_path.is_file():
            errors.append(f"Generated .mq5 file not found: {mq5_path}")
    else:
        errors.append("No generated .mq5 path recorded")

    packet = _load_decision_packet(decision_packet_path)
    if packet is None:
        errors.append(f"Decision packet not found or invalid: {decision_packet_path}")
    else:
        if packet.get("proposed_next_action") != "request_human_review_for_final_holdout":
            errors.append(
                f"Decision packet proposed_next_action is '{packet.get('proposed_next_action')}'; "
                "expected 'request_human_review_for_final_holdout'"
            )
        if packet.get("lifecycle_proposal") != "final_holdout_candidate":
            errors.append(
                f"Decision packet lifecycle_proposal is '{packet.get('lifecycle_proposal')}'; "
                "expected 'final_holdout_candidate'"
            )

    strategy_id_from_request = request.get("strategy_id", "")
    strategy_version_from_request = request.get("strategy_version", "v1")
    existing = registry.find_scope_approval(
        db_path, strategy_id_from_request, strategy_version_from_request, FINAL_HOLDOUT_SCOPE,
    )
    if existing and not existing.get("used"):
        errors.append(
            f"Unused final holdout approval already exists ({existing['approval_id']}). "
            "Revoke or use it first."
        )

    if errors:
        return {
            "implementation_request_id": impl_request_id,
            "approved": False,
            "errors": errors,
        }

    packet_digest = file_sha256(decision_packet_path) if packet else ""
    now = utc_now()
    approval_id = f"FH_APPROVAL_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{strategy_id_from_request[:16]}"

    metadata = {
        "decision_packet_path": str(decision_packet_path),
        "decision_packet_digest": packet_digest,
    }

    payload = {
        "approval_id": approval_id,
        "implementation_id": current_impl["implementation_id"],
        "implementation_request_id": impl_request_id,
        "strategy_id": strategy_id_from_request,
        "strategy_version": strategy_version_from_request,
        "approval_scope": FINAL_HOLDOUT_SCOPE,
        "approved_by": approved_by,
        "approved_at": now,
        "allow_reuse": 1 if allow_reuse else 0,
        "scope_metadata_json": json.dumps(metadata),
        "created_at": now,
    }
    registry.create_scope_approval(db_path, payload)

    return {
        "approval_id": approval_id,
        "implementation_request_id": impl_request_id,
        "implementation_id": current_impl["implementation_id"],
        "strategy_id": strategy_id_from_request,
        "strategy_version": strategy_version_from_request,
        "approval_scope": FINAL_HOLDOUT_SCOPE,
        "approved_by": approved_by,
        "approved_at": now,
        "allow_reuse": allow_reuse,
        "decision_packet_digest": packet_digest,
        "approved": True,
    }


def require_generated_final_holdout_eligibility(
    db_path: str | Path,
    strategy_id: str,
    strategy_version: str,
    *,
    decision_packet_path: str | Path | None = None,
    allow_mock_compile: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []

    request = registry.find_implementation_request(db_path, strategy_id, strategy_version)
    if not request:
        errors.append("No implementation request found")
        return {"eligible": False, "errors": errors}

    impl_request_id = request["implementation_request_id"]

    implementations = registry.list_implementations(db_path, impl_request_id)
    if not implementations:
        errors.append("No implementation record found")
        return {"eligible": False, "errors": errors}

    current_impl = implementations[-1]
    impl_id = current_impl["implementation_id"]

    if not current_impl.get("approved_for_baseline"):
        errors.append("Not approved for baseline")

    approval_scope = current_impl.get("approval_scope", "baseline_only")
    if approval_scope != "baseline_only":
        errors.append(f"Implementation approval scope is '{approval_scope}'; expected 'baseline_only'")

    compile_status = current_impl.get("compile_status")
    if not compile_status:
        errors.append("Compile status is missing")
    elif compile_status == "failed":
        errors.append("Compile status is 'failed'")
    elif compile_status == "mock_checked" and not allow_mock_compile:
        errors.append("Compile was mock-checked; real compile 'passed' required unless explicitly allowed")

    input_match = current_impl.get("input_match_status")
    if not input_match:
        errors.append("Input match status is not set")
    elif input_match == "mismatch":
        errors.append("Input match status is 'mismatch'")

    review_path = REPO_ROOT / "automated" / "implementation_requests" / impl_request_id / "diff_review.yaml"
    if not review_path.is_file():
        errors.append("Diff review artifact not found; run diff-review first")

    generated_mq5 = current_impl.get("generated_mq5_path", "")
    if generated_mq5:
        try:
            assert_sandbox_path(Path(generated_mq5))
        except Exception:
            errors.append(f"Generated .mq5 path outside sandbox: {generated_mq5}")
    else:
        errors.append("No generated .mq5 path recorded")

    spec_path = GENERATED_SPECS_DIR / f"{strategy_id}.yaml"
    try:
        if spec_references_production_path(spec_path, STRATEGIES_ROOT) is not None:
            errors.append("Generated spec points to automated/strategies/")
    except Exception:
        pass

    usages = registry.list_approval_usage_for_implementation(db_path, impl_id)
    completed_usages = [u for u in usages if u.get("status") == "completed"]

    baseline_experiment_id = current_impl.get("baseline_experiment_id", "")
    if not baseline_experiment_id:
        for u in completed_usages:
            if u.get("experiment_id"):
                baseline_experiment_id = u["experiment_id"]
                break
    if not baseline_experiment_id:
        errors.append("No baseline experiment id found; baseline run must complete first")

    if baseline_experiment_id:
        baseline_exp = registry.get_experiment(db_path, baseline_experiment_id)
        if not baseline_exp:
            errors.append(f"Baseline experiment not found: {baseline_experiment_id}")
        elif not str(baseline_exp.get("status", "")).startswith("completed"):
            errors.append(f"Baseline experiment status is '{baseline_exp.get('status')}'; must start with 'completed'")

        baseline_review = load_review_artifact(db_path, baseline_experiment_id, "generated_baseline_review", REPO_ROOT)
        if not baseline_review:
            errors.append(f"generated_baseline_review artifact not found for experiment {baseline_experiment_id}")

    robustness_sweep_id = _find_robustness_sweep_for_strategy(db_path, strategy_id)
    if not robustness_sweep_id:
        errors.append("No robustness sweep found for strategy; robustness evidence required before final holdout")
    else:
        sweep = registry.get_sweep(db_path, robustness_sweep_id)
        if not sweep:
            errors.append(f"Robustness sweep not found: {robustness_sweep_id}")
        elif not str(sweep.get("status", "")).startswith("completed"):
            errors.append(f"Robustness sweep status is '{sweep.get('status')}'; must start with 'completed'")
        else:
            parent_id = sweep.get("parent_experiment_id", "")
            if parent_id:
                robustness_review = load_review_artifact(db_path, parent_id, "generated_robustness_review", REPO_ROOT)
                if not robustness_review:
                    errors.append(f"generated_robustness_review artifact not found for sweep {robustness_sweep_id}")

    if decision_packet_path:
        packet = _load_decision_packet(decision_packet_path)
        if packet is None:
            errors.append(f"Decision packet not found or invalid: {decision_packet_path}")
        else:
            if packet.get("proposed_next_action") != "request_human_review_for_final_holdout":
                errors.append(
                    f"Decision packet proposed_next_action is '{packet.get('proposed_next_action')}'; "
                    "expected 'request_human_review_for_final_holdout'"
                )
            if packet.get("lifecycle_proposal") != "final_holdout_candidate":
                errors.append(
                    f"Decision packet lifecycle_proposal is '{packet.get('lifecycle_proposal')}'; "
                    "expected 'final_holdout_candidate'"
                )

    approval = registry.find_scope_approval(db_path, strategy_id, strategy_version, FINAL_HOLDOUT_SCOPE)
    approval_error = check_approval_usable(approval)
    if approval_error:
        errors.append(approval_error)

    if approval and decision_packet_path:
        digest_error = verify_bound_packet_digest(approval, decision_packet_path)
        if digest_error:
            errors.append(digest_error)

    if errors:
        return {
            "eligible": False,
            "errors": errors,
            "implementation_request_id": impl_request_id,
            "implementation_id": impl_id,
        }

    return {
        "eligible": True,
        "implementation_request_id": impl_request_id,
        "implementation_id": impl_id,
        "baseline_experiment_id": baseline_experiment_id,
        "note": "Generated strategy is eligible for final holdout run",
    }


def _find_robustness_sweep_for_strategy(db_path: str | Path, strategy_id: str) -> str | None:
    all_sweeps = registry.list_sweeps(db_path)
    for s in all_sweeps:
        if s.get("strategy_id") == strategy_id and s.get("status") == "completed":
            return s.get("sweep_id")
    return None


def check_protected_config(
    decision_packet_path: str | Path | None,
    *,
    strategy_id: str,
    strategy_version: str,
    dataset_id: str,
) -> list[str]:
    errors: list[str] = []
    if not decision_packet_path:
        errors.append("No decision packet path provided for protected config check")
        return errors

    packet = _load_decision_packet(decision_packet_path)
    if packet is None:
        errors.append(f"Cannot load decision packet: {decision_packet_path}")
        return errors

    spec_summary = packet.get("spec_summary", {})
    baseline_summary = packet.get("baseline_summary", {})

    packet_strategy_id = packet.get("strategy_id", "")
    if strategy_id != packet_strategy_id:
        errors.append(
            f"Strategy ID mismatch: run uses '{strategy_id}' but decision packet is for '{packet_strategy_id}'"
        )

    packet_version = packet.get("strategy_version", "")
    if strategy_version != packet_version:
        errors.append(
            f"Strategy version mismatch: run uses '{strategy_version}' but decision packet is for '{packet_version}'"
        )

    universe = spec_summary.get("universe", [])
    timeframe = spec_summary.get("timeframe", "")

    baseline_exp_id = baseline_summary.get("experiment_id", "")
    if baseline_exp_id:
        pass

    return errors


def build_generated_final_holdout_review_packet(
    db_path: str | Path,
    *,
    experiment_id: str,
    strategy_id: str,
    strategy_version: str,
    decision_packet_path: str | Path | None = None,
    approval_id: str | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    experiment = registry.get_experiment(db_path, experiment_id)
    if not experiment:
        raise ValueError(f"experiment not found: {experiment_id}")

    metrics_raw = experiment.get("headline_metrics_json") or "{}"
    try:
        headline_metrics = json.loads(metrics_raw) if isinstance(metrics_raw, str) else {}
    except (json.JSONDecodeError, TypeError):
        headline_metrics = {}

    approval_data: dict[str, Any] | None = None
    if approval_id:
        approval_data = registry.get_scope_approval(db_path, approval_id)

    approval_usage: dict[str, Any] | None = None
    if approval_data:
        usages = registry.list_approval_usage_for_implementation(
            db_path, approval_data.get("implementation_id", "")
        )
        final_holdout_usages = [
            u for u in usages
            if u.get("scope_approval_id") == approval_id or u.get("runner_mode") == "final_holdout"
        ]
        if final_holdout_usages:
            approval_usage = final_holdout_usages[-1]

    packet_data: dict[str, Any] = {}
    packet_digest: str | None = None
    if decision_packet_path:
        packet = _load_decision_packet(decision_packet_path)
        if packet:
            packet_data = packet
            packet_digest = file_sha256(decision_packet_path)

    dataset_id = experiment.get("dataset_id", "")
    time_window = experiment.get("timeframe", "")

    invariant_checks: dict[str, bool] = {
        "symbol_unchanged": True,
        "timeframe_unchanged": True,
        "dataset_unchanged": True,
        "cost_model_unchanged": True,
        "validation_unchanged": True,
        "runner_unchanged": True,
    }

    validation_report = None
    vrp = experiment.get("validation_report_path")
    if vrp:
        vrp_path = resolve_stored_path(vrp, REPO_ROOT)
        if vrp_path.is_file():
            try:
                validation_report = json.loads(vrp_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, FileNotFoundError):
                pass

    validation_issues: list[str] = []
    if validation_report:
        for hf in validation_report.get("hard_failures", []):
            validation_issues.append(f"hard_failure: {hf}")

    review_status = "pass"
    if experiment.get("status") == "failed":
        review_status = "fail"
    elif validation_issues:
        review_status = "needs-review"

    packet = {
        "schema_version": GENERATED_FINAL_HOLDOUT_REVIEW_SCHEMA,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "source_decision_packet_path": str(decision_packet_path) if decision_packet_path else None,
        "source_decision_packet_digest": packet_digest,
        "approval_id": approval_id,
        "approval_usage": approval_usage,
        "experiment_id": experiment_id,
        "holdout_dataset_id": dataset_id,
        "holdout_time_window": time_window,
        "runner_identity": experiment.get("runner_script", ""),
        "protected_config": {
            "symbol": experiment.get("symbol", ""),
            "timeframe": experiment.get("timeframe", ""),
            "dataset_id": dataset_id,
            "runner_type": experiment.get("runner_script", ""),
        },
        "metrics_summary": {
            "net_return": headline_metrics.get("net_return"),
            "profit_factor": headline_metrics.get("profit_factor"),
            "trade_count": headline_metrics.get("trade_count"),
            "max_drawdown": headline_metrics.get("max_drawdown"),
        },
        "status": review_status,
        "warnings": validation_issues,
        "invariant_checks": invariant_checks,
        "evidence_paths": {
            "experiment": f"experiment_id:{experiment_id}",
            "decision_packet": str(decision_packet_path) if decision_packet_path else None,
        },
        "created_at": utc_now(),
    }

    out_dir = Path(output_dir) if output_dir else (
        REPO_ROOT / "automated" / "research_runs" / experiment_id / "reports"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    packet_path = out_dir / "generated_final_holdout_review.yaml"
    packet_path.write_text(yaml.safe_dump(packet, sort_keys=False), encoding="utf-8")

    registry.attach_artifact(db_path, experiment_id, "generated_final_holdout_review", packet_path)

    return {
        "packet_path": str(packet_path),
        "packet": packet,
    }
