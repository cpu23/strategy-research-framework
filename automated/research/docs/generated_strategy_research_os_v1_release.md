# Generated Strategy Research OS v1 — Release Acceptance

## Release Metadata

| Field | Value |
|-------|-------|
| Release ID | `v1` |
| Date | 2026-05-14 |
| System | Generated strategy research operating system (Track 2) |
| Branch | Generated-strategy autonomous pipeline |
| Preceding | Phase 1–24 (hypothesis intake through real-toolchain rehearsal, authority boundary freeze) |

## Scope

This release covers the bounded research OS for generated strategies. It provides autonomous hypothesis-to-final-holdout pipeline execution within hard authority boundaries, plus operator-controlled readiness verification. It does **not** provide production promotion, live trading, lifecycle apply, or automated approval.

## Verification Points

### 1. All core docs exist

All 14 documentation files under `automated/research/docs/` are present:

| # | File | Purpose |
|---|------|---------|
| 1 | `generated_strategy_research_os.md` | Architecture overview |
| 2 | `generated_strategy_authority_invariants.md` | Authority boundary contract |
| 3 | `generated_strategy_operator_runbook.md` | Step-by-step operator runbook |
| 4 | `phase15d_queue_validation_audit.md` | Queue permission matrix |
| 5 | `phase16_end_to_end_dry_run_report.md` | End-to-end dry run results |
| 6 | `phase23_cleanup_consolidation.md` | Cleanup consolidation notes |
| 7 | `real_compile_verification.md` | MT5/Wine compile procedure |
| 8 | `real_backtest_readiness.md` | Bounded backtest readiness procedure |
| 9 | `real_run_readiness_checklist.md` | Pre-run readiness checklist |
| 10 | `real_toolchain_artifact_taxonomy.md` | Readiness artifact type definitions |
| 11 | `real_toolchain_operator_freeze_checklist.md` | Operator freeze checklist |
| 12 | `real_toolchain_rehearsal_runbook.md` | Rehearsal runbook |
| 13 | `readiness_review_packet.md` | Readiness review packet spec |
| 14 | `dry_run_artifact_cleanup.md` | Dry-run artifact cleanup guidance |

Verified by: `ResearchPhase25DocExistenceTests.test_all_core_docs_exist`

### 2. Runbooks agree on canonical evidence path

Three core documents define the canonical evidence path:

- `generated_strategy_authority_invariants.md` — lists all 16 steps in order (hypothesis_generation through updated candidate decision packet)
- `generated_strategy_research_os.md` — lists all 16 steps in the same order in its pipeline diagram
- `generated_strategy_operator_runbook.md` — documents all 16 steps as numbered workflow items

Readiness and rehearsal steps (compile verification, backtest readiness, readiness review, rehearsal) are documented as operator-only in all three docs and are not part of the evidence path.

Verified by: `ResearchPhase25EvidencePathAgreementTests`

### 3. Authority invariants doc exists and names forbidden values only as forbidden/non-goals

`generated_strategy_authority_invariants.md` exists (132 lines) and defines:

- **Forbidden values** (Section 3): `promote_to_production`, `production_candidate`, `live_trading_candidate` — allowed only in deny lists, negative tests, or non-goals sections
- **Allowed candidate actions** (Section 1): six values, none of which are the forbidden values
- **Allowed lifecycle proposals** (Section 2): four values, none of which are the forbidden values
- **Production/Live Non-Goals** (Section 6): explicitly states the research OS does not promote to production, create production candidates, or create live trading paths
- **Maintenance Rule** (Section 7): requires this doc to be updated when authority boundaries change

The three forbidden values appear **only** in:
- The "Forbidden Values" section (subsection 3)
- The "Production/Live Non-Goals" section (subsection 6 — as non-goals)

They do **not** appear as allowed values in any action list, proposal list, or permission set.

Verified by: `ResearchPhase25ForbiddenValueContextTests`

### 4. Readiness/rehearsal artifacts are documented as operator-only

`real_toolchain_artifact_taxonomy.md` defines the four readiness/rehearsal artifact types and states for each:

- `"operator evidence only"` or equivalent
- Excluded from `contracts.ARTIFACT_TYPES`
- Must not be consumed as baseline, robustness, final holdout, candidate decision, lifecycle, production, live trading, or approval evidence

The artifact types (`real_compile`, `real_backtest_readiness`, `generated_readiness_review`, `real_toolchain_rehearsal_summary`) are absent from `contracts.ARTIFACT_TYPES` in source code.

Verified by: `ResearchPhase25OperatorBoundaryTests`

### 5. Phase 16 dry-run path remains represented in docs

`phase16_end_to_end_dry_run_report.md` documents the full end-to-end dry run with:
- Strategy ID `DRYRUN_PHASE16_20260513T143641Z`
- All 16 pipeline stages with PASS/FAIL results
- Queue verification results
- Negative checks (approval rejection, digest mismatch, queue permission bypass)
- Bugs fixed and friction notes

The dry run path is also referenced in `dry_run_artifact_cleanup.md`.

Verified by: `ResearchPhase25OperatorBoundaryTests.test_phase16_dry_run_path_represented`

### 6. No docs propose production/live/promotion as actionable values

All 14 docs were scanned for the three forbidden values (`promote_to_production`, `production_candidate`, `live_trading_candidate`). They appear **only** in:

- Deny lists / forbidden-value sections
- Non-goals statements
- Negative-test examples in runbooks
- Operator checklists as values that must not be proposed

No doc presents them as allowed candidate actions, lifecycle proposals, queue task types, approval outputs, or recommended workflow steps.

Verified by: `ResearchPhase25ForbiddenValueContextTests.test_forbidden_values_not_in_docs_as_actionable`

### 7. No source code exposes forbidden values as allowed values

All Python modules in `automated/research/` were scanned for `ALLOWED_*` sets and `TASK_TYPES`. None of the three forbidden values appear in any allowed-value set. They appear only in:

- `FORBIDDEN_ACTION_VALUES` (deny list in `readiness_review.py`)
- `FORBIDDEN_INTERPRETATIONS` (deny list in `readiness_review.py` and `toolchain_rehearsal.py`)
- Negative-test strings

The `check_no_production_touch()` function in `implementation.py` and `spec_references_production_path()` in `contracts.py` serve as programmatic guards against production-path contamination.

Verified by: `ResearchPhase25SourceCodeInvariantTests.test_forbidden_values_not_in_allowed_sets`

### 8. No queue tasks for readiness/rehearsal

`queue.TASK_TYPES` contains 20 task types. None of the following are present:

- `real_compile`
- `real_backtest_readiness`
- `generated_readiness_review`
- `readiness_review`
- `real_toolchain_rehearsal`
- `real_toolchain_rehearsal_summary`

Queue permission defaults set `allow_lifecycle_apply=false` and `allow_final_holdout=false` as forbidden defaults. Queue validation blocks any item with `allow_final_holdout=true` unless a human-approved implementation task exists.

Verified by: `ResearchPhase25SourceCodeInvariantTests.test_no_queue_tasks_for_readiness_rehearsal`

### 9. No lifecycle apply path for generated strategies

Generated strategy pipeline modules do not call `lifecycle.apply_transition()`:

- `generated_candidate.py` uses `lifecycle_proposal` as a string-only label — it does not apply transitions
- `queue.py` blocks `allow_lifecycle_apply` at validation time
- `generated_baseline.py`, `generated_robustness.py`, `generated_final_holdout.py` do not import or call lifecycle apply
- Readiness/rehearsal modules (`compiler.py`, `backtest_readiness.py`, `readiness_review.py`, `toolchain_rehearsal.py`) do not create lifecycle transitions
- The lifecycle apply CLI command (`research lifecycle apply`) exists for Track 1 manual strategies but is never invoked by generated strategy automation

Verified by: `ResearchPhase25SourceCodeInvariantTests.test_no_lifecycle_apply_for_generated_strategies`

### 10. No writes/copies into `automated/strategies/` for generated strategies

- `assert_sandbox_path()` rejects paths under `automated/strategies/`
- `check_no_production_touch()` flags any path under `automated/strategies/`
- `create_implementation_request()` rejects sandbox dirs set to `automated/strategies/`
- `build_readiness_review_packet()` rejects output paths under `automated/strategies/`
- `_check_out_dir()` in rehearsal orchestration rejects `automated/strategies/` output
- The operator freeze checklist requires confirmation that no artifacts were written to `automated/strategies/`

Verified by: `ResearchPhase25SourceCodeInvariantTests.test_no_writes_to_automated_strategies`

### 11. Final test command

The complete test suite is documented in `real_toolchain_operator_freeze_checklist.md`:

```
python3 -m unittest discover -s tests -p 'test_*.py'
```

All 24 prior phases (1, 9–24) plus this Phase 25 acceptance layer are executed by this single command. The freeze checklist requires the full test suite to pass before any real-toolchain readiness workflow execution.

Verified by: `ResearchPhase25ReleaseCommandTests.test_final_test_command_documented`

## Summary

| # | Verification Point | Status |
|---|--------------------|--------|
| 1 | All core docs exist (14/14) | PASS |
| 2 | Runbooks agree on canonical evidence path | PASS |
| 3 | Authority invariants doc exists with forbidden values as forbidden only | PASS |
| 4 | Readiness/rehearsal artifacts documented as operator-only | PASS |
| 5 | Phase 16 dry-run path remains represented | PASS |
| 6 | No docs propose production/live/promotion as actionable | PASS |
| 7 | No source code exposes forbidden values as allowed | PASS |
| 8 | No queue tasks for readiness/rehearsal | PASS |
| 9 | No lifecycle apply path for generated strategies | PASS |
| 10 | No writes/copies into `automated/strategies/` | PASS |
| 11 | Final test command documented | PASS |

## Conclusion

The generated strategy research OS v1 is released as a bounded research operating system. It provides autonomous hypothesis-to-final-holdout execution, operator-controlled readiness verification, and hard authority boundaries that prevent production promotion, live trading, lifecycle apply, and automated approval.

All verification points pass. No new execution authority is created by this release.
