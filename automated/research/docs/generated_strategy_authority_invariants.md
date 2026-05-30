# Generated Strategy Authority Invariants

## Purpose

This document freezes the hard authority boundaries for the generated strategy research OS. It is a compact safety contract: future changes may add experiments or documentation, but they must not silently expand execution, approval, lifecycle, production, or live-trading authority.

## Scope

These invariants apply to generated strategy research artifacts and queues only. They do not add execution capability, CLI commands, queue tasks, production promotion, lifecycle application, approval automation, or live trading.

## Evidence Path

The only generated strategy evidence path is:

1. `hypothesis_generation`
2. `strategy_spec_generation`
3. `implementation_materialization`
4. `compile-check`
5. `diff-review`
6. `research_review_packet`
7. `manual approve-for-baseline`
8. `generated_baseline_experiment`
9. `generated_baseline_review`
10. `generated_robustness_sweep`
11. `generated_robustness_review`
12. `generated_candidate_decision_packet`
13. `manual approve-final-holdout`
14. `generated_final_holdout_experiment`
15. `generated_final_holdout_review`
16. `updated/generated candidate decision packet`

Readiness and rehearsal artifacts are operator evidence only. They are not alternative evidence paths.

## Allowed Candidate Actions

Generated candidate decision packets may use only these `proposed_next_action` values:

- `reject`
- `revise_strategy_spec`
- `revise_implementation`
- `run_additional_bounded_sweep`
- `request_human_review_for_final_holdout`
- `defer`

## Allowed Lifecycle Proposals

Generated candidate decision packets may use only these `lifecycle_proposal` values:

- `none`
- `research_candidate`
- `robustness_candidate`
- `final_holdout_candidate`

These are proposal labels only. They do not apply lifecycle transitions.

## Forbidden Values

These values are not allowed candidate actions, lifecycle proposals, queue task types, or approval outputs:

- `promote_to_production`
- `production_candidate`
- `live_trading_candidate`

They may appear only in deny lists, forbidden-value tests, not-allowed docs, non-goals, or negative tests.

## Approval Boundaries

Baseline approval is manual and explicit. A generated baseline experiment requires prior `approve-for-baseline` approval and cannot be made eligible by queue permissions, readiness review, rehearsal summary, artifact attachment, or registry side effects.

Final holdout approval is manual, explicit, `final_holdout_only`, single-use by default, and bound to the candidate decision packet digest. Edited candidate packets must fail digest verification. Reusing final holdout approval requires an explicit reuse flag.

Queue permission cannot substitute for human approval. Readiness review cannot approve baseline or final holdout. No artifact can silently create approval authority.

## Filesystem Boundaries

Generated `.mq5` files must live under:

`automated/generated_strategies/<strategy_id>/<version>/`

Generated `.mq5` files must never be written, copied, or materialized into:

`automated/strategies/`

Readiness and rehearsal output directories must be explicit and outside `automated/strategies/`.

## Readiness/Rehearsal Isolation

These artifacts remain operator-only evidence:

- `real_compile`
- `real_backtest_readiness`
- `generated_readiness_review`
- `real_toolchain_rehearsal_summary`

They must not satisfy baseline evidence, robustness evidence, final holdout evidence, candidate decision evidence, lifecycle evidence, approval evidence, production evidence, or live trading evidence.

They must remain excluded from `contracts.ARTIFACT_TYPES` while this remains the intended boundary.

Authority-bearing modules must not import or reference readiness/rehearsal modules as eligibility sources.

## Queue Boundaries

The queue must not define tasks for:

- `real_compile`
- `real_backtest_readiness`
- `generated_readiness_review`
- `real_toolchain_rehearsal`
- `real_toolchain_rehearsal_summary`

The queue cannot create approvals. The queue cannot apply lifecycle transitions. Queue permissions may prepare or run bounded generated research tasks only when the existing manual approval gates are already satisfied.

## Dataset, Runner, And Threshold Boundaries

Generated strategy workflows must not silently mutate:

- dataset ID
- symbol
- timeframe
- cost assumptions
- validation thresholds
- runner script

Robustness sweeps remain one-variable-at-a-time parameter experiments and must block dataset, symbol, timeframe, cost, validation-threshold, and code-regeneration mutations.

## Production/Live Non-Goals

This research OS does not promote generated strategies to production. It does not create production candidates, live trading candidates, production promotion paths, live trading paths, automated baseline approval, automated final holdout approval, automated approval, lifecycle apply, or automatic robustness escalation.

## Maintenance Rule

Future phases must update this doc and the corresponding invariant tests if authority boundaries intentionally change. If tests fail because a boundary changed, treat the failure as a design review prompt, not as a routine snapshot update.
