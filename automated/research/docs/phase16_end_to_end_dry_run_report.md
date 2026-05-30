# Phase 16 End-to-End Dry Run Report

Date: 2026-05-13

## Result

Overall result: PASS with minimal plumbing fixes.

Autonomous research execution was verified in a mock/safe runner path. Autonomous research authority remained blocked by approvals and packet binding.

No live trading, production promotion, lifecycle apply, or copy into `automated/strategies/` occurred.

## Strategy

- Strategy ID: `DRYRUN_PHASE16_20260513T143641Z`
- Strategy version: `v1`
- Hypothesis ID: `HYP_GEN_FBR_RANGING_000`
- Dataset ID: `DATA_XAUUSD_H4_0DB89C617104`
- Mock runner: `/tmp/phase16_mock_runner.py`

## Commands And Artifacts

| Stage | Result | Command or call | Key artifact/evidence |
|---|---:|---|---|
| Generate hypothesis evidence | PASS | `python3 -m automated.research.cli intake generate-hypotheses --theme phase16_end_to_end_dry_run --symbol XAUUSD --timeframe H4 --market-regime ranging --strategy-family failed_breakout_reversal --max-hypotheses 1 --created-by phase16_dry_run` | `hypotheses/HYP_GEN_FBR_RANGING_000.yaml` |
| Generate strategy spec | PASS | `python3 -m automated.research.cli intake generate-spec --hypothesis-id HYP_GEN_FBR_RANGING_000 --strategy-id DRYRUN_PHASE16_20260513T143641Z --strategy-version v1 --created-by phase16_dry_run` | `automated/generated_specs/DRYRUN_PHASE16_20260513T143641Z.yaml` |
| Materialize sandbox implementation | PASS after fix | `python3 -m automated.research.cli intake materialize --strategy-id DRYRUN_PHASE16_20260513T143641Z --strategy-version v1 --spec-path automated/generated_specs/DRYRUN_PHASE16_20260513T143641Z.yaml --mock --created-by phase16_dry_run_after_runner_file_fix` | `automated/generated_strategies/DRYRUN_PHASE16_20260513T143641Z/v1/DRYRUN_PHASE16_20260513T143641Z.mq5` |
| Materialize runner files | PASS after fix | same materialization command | `automated/runs/DRYRUN_PHASE16_20260513T143641Z_baseline.conf`, `automated/runs/sets/DRYRUN_PHASE16_20260513T143641Z_baseline.set` |
| Compile-check | PASS | `python3 -m automated.research.cli implementation compile-check IMPL_REQ_20260513_153652_A93B51DF --mock` | compile status `mock_checked` |
| Diff-review | PASS | `python3 -m automated.research.cli implementation diff-review IMPL_REQ_20260513_153652_A93B51DF` | `automated/implementation_requests/IMPL_REQ_20260513_153652_A93B51DF/diff_review.yaml` |
| Research review packet | PASS | `python3 -m automated.research.cli intake review-packet --strategy-id DRYRUN_PHASE16_20260513T143641Z --strategy-version v1` | `automated/generated_strategies/DRYRUN_PHASE16_20260513T143641Z/v1/review_packet.yaml` |
| Baseline pre-approval rejection | PASS | `generated-baseline run ... --allow-mock-compile --prepare-only` before approval | rejected: `Not approved for baseline` |
| Baseline approval | PASS | `python3 -m automated.research.cli implementation approve-for-baseline IMPL_REQ_20260513_153842_A83553AF --approved-by phase16_operator --allow-mock-compile` | implementation `IMPL_20260513_153842_9C3F9996` approved `baseline_only`, no production promotion |
| Baseline run | PASS | `python3 -m automated.research.cli generated-baseline run ... --run --runner-script /tmp/phase16_mock_runner.py` | experiment `EXP_20260513_153909_dryrun_phase16_20260513t143641z_D44587`; usage `USAGE_20260513_153909_9884336E` |
| Baseline review | PASS after validation/red-team fix | `python3 -m automated.research.cli generated-baseline review --experiment-id EXP_20260513_153909_dryrun_phase16_20260513t143641z_D44587 ...` | `automated/research_runs/EXP_20260513_153909_dryrun_phase16_20260513t143641z_D44587/reports/generated_baseline_review.yaml`; recommendation `run_robustness_sweep_next` |
| Robustness pre-evidence rejection | PASS | `generated-robustness run-sweep ... --baseline-experiment-id EXP_PHASE16_MISSING_BASELINE ...` | rejected: parent experiment not found |
| Robustness sweep | PASS | `python3 -m automated.research.cli generated-robustness run-sweep ... --params '{"InpAtrPeriod":[13,14,15]}' --child-cap 3 --allow-mock-compile --run --runner-script /tmp/phase16_mock_runner.py` | sweep `SWEEP_20260513_154111_dryrun_phase16_20260513t143641z_A96E9E`; children `_000`, `_001` |
| Robustness review | PASS | `python3 -m automated.research.cli generated-robustness review --sweep-id SWEEP_20260513_154111_dryrun_phase16_20260513t143641z_A96E9E ...` | baseline report `generated_robustness_review.yaml`; recommendation `consider_lifecycle_candidate` |
| Candidate decision packet | PASS | `python3 -m automated.research.cli generated-candidate decision-packet ...` | `automated/research_runs/generated_candidate_dryrun_phase16_20260513t143641z/generated_candidate_decision_packet.yaml`; `request_human_review_for_final_holdout`, `final_holdout_candidate` |
| Final holdout pre-approval rejection | PASS | `generated-final-holdout run ... --prepare-only` before final approval | rejected: no `final_holdout_only` approval |
| Final holdout approval | PASS | `python3 -m automated.research.cli generated-final-holdout approve ... --approved-by phase16_operator` | approval `FH_APPROVAL_20260513_154224_DRYRUN_PHASE16_2`; digest `586f961d9988c001fd4b233ae13d1b524c6a322982c9f500f7107e9f915e5588`; `allow_reuse=false` |
| Edited packet digest rejection | PASS | copied packet to `/tmp/phase16_edited_decision_packet.yaml`, edited `candidate_status`, then ran final-holdout prepare | rejected by digest mismatch |
| Final holdout run | PASS | `python3 -m automated.research.cli generated-final-holdout run ... --run --runner-script /tmp/phase16_mock_runner.py` | experiment `FH_EXP_20260513_154243_dryrun_phase16_20260513t143641z_6577FD`; usage `USAGE_20260513_154243_78ADA953` |
| Approval reuse rejection | PASS | repeated final-holdout prepare with same approval | rejected: approval already consumed; reuse requires explicit `allow_reuse` |
| Final holdout review | PASS | `python3 -m automated.research.cli generated-final-holdout review ... --approval-id FH_APPROVAL_20260513_154224_DRYRUN_PHASE16_2` | `automated/research_runs/FH_EXP_20260513_154243_dryrun_phase16_20260513t143641z_6577FD/reports/generated_final_holdout_review.yaml`; status `pass` |
| Candidate packet after final holdout | PASS | `python3 -m automated.research.cli generated-candidate decision-packet ... --output automated/research_runs/generated_candidate_dryrun_phase16_20260513t143641z_after_fh` | proposed next action `defer`; lifecycle proposal `robustness_candidate` |

## Queue Verification

Queue spec: `automated/specs/research_queue/phase16_dryrun_review_queue.yaml`

Queue task IDs:

- `phase16_generated_baseline_review`
- `phase16_generated_robustness_review`
- `phase16_generated_candidate_decision`
- `phase16_generated_final_holdout_review`

Queue commands:

- `python3 -m automated.research.cli queue validate --queue automated/specs/research_queue/phase16_dryrun_review_queue.yaml`
- `python3 -m automated.research.cli queue run --queue automated/specs/research_queue/phase16_dryrun_review_queue.yaml --dry-run --runner-script /tmp/phase16_mock_runner.py`
- `python3 -m automated.research.cli queue run --queue automated/specs/research_queue/phase16_dryrun_review_queue.yaml --runner-script /tmp/phase16_mock_runner.py`

Queue run ID: `QUEUE_RUN_20260513_154357_phase16_dryrun_review_queue_0486AB`

Queue result: `completed_with_warnings`, with no experiments or sweeps created. Warnings were evidence warnings only: final holdout already exists, single-symbol/single-timeframe baseline, and small robustness sweep.

Queue artifacts:

- `automated/research_runs/queue_runs/QUEUE_RUN_20260513_154357_phase16_dryrun_review_queue_0486AB/queue_run_summary.json`
- `automated/research_runs/queue_runs/QUEUE_RUN_20260513_154357_phase16_dryrun_review_queue_0486AB/generated_baseline_reviews/generated_baseline_review.yaml`
- `automated/research_runs/queue_runs/QUEUE_RUN_20260513_154357_phase16_dryrun_review_queue_0486AB/generated_robustness_reviews/generated_robustness_review.yaml`
- `automated/research_runs/queue_runs/QUEUE_RUN_20260513_154357_phase16_dryrun_review_queue_0486AB/generated_candidate_decision_packets/generated_candidate_decision_packet.yaml`
- `automated/research_runs/queue_runs/QUEUE_RUN_20260513_154357_phase16_dryrun_review_queue_0486AB/generated_final_holdout_reviews/generated_final_holdout_review.yaml`

Queue permission negative check:

- `/tmp/phase16_queue_permission_bypass.yaml` set `allow_final_holdout=true` with a nonexistent approval.
- `queue run` rejected at validation: `allow_final_holdout is forbidden for autonomous queue execution without a human-approved implementation task`.
- This confirms queue permission alone cannot substitute for human approval.

## Negative Checks

| Check | Result | Evidence |
|---|---:|---|
| Baseline run rejected before baseline approval | PASS | `generated-baseline run` rejected with `Not approved for baseline` |
| Final holdout rejected before `final_holdout_only` approval | PASS | `generated-final-holdout run` rejected before approval |
| Edited decision packet rejected after approval | PASS | digest mismatch against approval-bound digest |
| Reused final holdout approval rejected | PASS | same approval rejected after experiment `FH_EXP_20260513_154243_dryrun_phase16_20260513t143641z_6577FD`; `allow_reuse=false` |
| Queue permission alone cannot substitute for approval | PASS | `allow_final_holdout=true` queue rejected without human-approved implementation task |
| No writes or copies into `automated/strategies/` | PASS | `find automated/strategies -path '*DRYRUN_PHASE16_20260513T143641Z*'` returned nothing |
| No lifecycle transition rows created | PASS | `lifecycle_transitions` count remained `0`; strategy-specific count `0` |
| No production/promotion/live proposed next actions | PASS | candidate packets had only `request_human_review_for_final_holdout` and then `defer`; no production, promotion, or live proposed action |

Note: queue summaries list `apply_lifecycle_transition` and `use_final_holdout` under `blocked_actions`. These are blocked capabilities, not proposed next actions or applied side effects.

## Bugs Fixed

1. Generated materialization did not create the config and `.set` files referenced by generated specs.
   - Fix: `intake.materialize_implementation` now writes declared runner config and parameter files for generated strategies.
   - Regression: `tests.test_research_phase10.ResearchPhase10MaterializationTests.test_materialize_creates_declared_runner_files`.

2. Validation only resolved production strategy specs under `automated/specs/strategies/`, causing generated strategies to lose cost/execution assumptions.
   - Fix: validation now falls back to `automated/generated_specs/`.
   - Regression: `tests.test_research_phase10.ResearchPhase10MaterializationTests.test_validation_resolves_generated_strategy_spec`.

3. Baseline red-team cost check read an older validation shape and falsely flagged missing cost assumptions.
   - Fix: generated baseline red-team check now accepts the current `sections.cost_assumption_gate.status == pass` schema.
   - Regression: `tests.test_research_phase15.TestGeneratedBaselineRedTeamCurrentValidationSchema`.

4. Final holdout eligibility rejected `completed_with_warnings` baseline/sweep evidence, while candidate decision accepted completed-like statuses.
   - Fix: final holdout eligibility accepts statuses starting with `completed`.
   - Regression: `tests.test_research_phase15.TestFinalHoldoutEligibilityCompletedWithWarnings`.

## Friction And Runbook Corrections

- Compile-check and diff-review should be run sequentially. Running them in parallel can leave the newest implementation record without input-match status until diff-review is rerun.
- The runbook says candidate packets proceed if the action is `final_holdout`; the implemented action is `request_human_review_for_final_holdout` with lifecycle proposal `final_holdout_candidate`.
- Baseline approval is implementation-record based and does not emit a separate approval ID. Final holdout approval does emit `approval_id`.

## Side-Effect Confirmation

- No live MT5 command was run; only `/tmp/phase16_mock_runner.py` was used for execution.
- No files were written under `automated/strategies/` for `DRYRUN_PHASE16_20260513T143641Z`.
- No lifecycle transition rows exist.
- No production, promotion, live trading, or lifecycle-apply path was added.
- Final holdout approval was single-use and packet-bound.

## Remaining Operational Risks

- Generated baseline review still reports single-symbol and single-timeframe risk, as expected for this dry run.
- Robustness sweep was intentionally tiny, so review warns that it is too small for robustness conclusions.
- The dry run used mock artifacts; it verifies orchestration and gates, not market performance or MT5 compile fidelity.
