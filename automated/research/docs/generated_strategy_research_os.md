# Generated Strategy Research OS — Architecture

## Evidence Path

A generated strategy moves through six sequential stages. Each stage has an
eligibility gate, an experiment or sweep, and a review artifact. Manual
approval is required at three points.

```
hypothesis_generation
  -> strategy_spec_generation
  -> implementation_materialization
  -> compile-check
  -> diff-review
  -> research_review_packet
  v manual: approve-for-baseline (scope=baseline_only, single-use)
  -> generated_baseline_experiment
  -> generated_baseline_review
  -> generated_robustness_sweep
  -> generated_robustness_review
  -> generated_candidate_decision_packet
  v manual: approve-for-final-holdout (scope=final_holdout_only, packet-bound by digest)
  -> generated_final_holdout_experiment
  -> generated_final_holdout_review
  -> updated candidate decision packet
```

## Module Responsibilities

| Module | Role |
|--------|------|
| `contracts.py` | Allowed sets: approval scopes, statuses, artifact types, permission defaults. |
| `schemas.py` | YAML schema validation, path constants, sandbox boundary definition. |
| `hashing.py` | SHA-256 file digests, stable-key hashing, git code version derivation. |
| `registry.py` | SQLite persistence layer (~1500 lines). All READ and WRITE operations. |
| `implementation.py` | Sandbox guard (`assert_sandbox_path`), compile check, diff review, baseline approval, production-touch prevention. |
| `intake.py` | Hypothesis generation, spec generation, materialization, research review packet building. |
| `generated_baseline.py` | Baseline eligibility, red-team check, baseline review packet builder. |
| `generated_robustness.py` | Robustness eligibility, sweep parameter validation, sweep config generation, review builder. |
| `generated_candidate.py` | Candidate decision eligibility, decision packet builder. |
| `generated_final_holdout.py` | Final holdout approval eligibility, packet digest verification, review builder. |
| `queue.py` | Queue validation + execution dispatch for all 14 generated task types (~2300 lines). |
| `cli.py` | CLI argument parsing + dispatch for all commands (~1700 lines). |
| `validation.py` | Validation report builder (gates on trades, costs, artifacts). |

## Artifact Types

| Artifact | Format | Produced By |
|----------|--------|-------------|
| Hypothesis | YAML | `hypothesis_generation` |
| Strategy Spec | YAML | `strategy_spec_generation` |
| Implementation Request | YAML | `implementation_materialization` |
| Compile Check Result | YAML | `implementation_compile_check` |
| Diff Review | YAML | `implementation_review` |
| Research Review Packet | YAML | `research_review_packet` |
| Baseline Experiment Output | JSON/CSV | `generated_baseline_experiment` |
| Baseline Review Packet | YAML | `generated_baseline_review` |
| Robustness Sweep Config | YAML | `generated_robustness_sweep` |
| Robustness Review Packet | YAML | `generated_robustness_review` |
| Candidate Decision Packet | YAML | `generated_candidate_decision_packet` |
| Final Holdout Experiment Output | JSON/CSV | `generated_final_holdout_experiment` |
| Final Holdout Review Packet | YAML | `generated_final_holdout_review` |

## Queue Task Types (14)

| Task Type | Stage |
|-----------|-------|
| `hypothesis_generation` | Intake |
| `strategy_spec_generation` | Intake |
| `implementation_materialization` | Intake |
| `implementation_compile_check` | Implementation |
| `implementation_review` | Implementation |
| `research_review_packet` | Intake |
| `generated_baseline_experiment` | Baseline |
| `generated_baseline_review` | Baseline |
| `generated_robustness_sweep` | Robustness |
| `generated_robustness_review` | Robustness |
| `generated_candidate_decision_packet` | Candidate Decision |
| `generated_final_holdout_experiment` | Final Holdout |
| `generated_final_holdout_review` | Final Holdout |

## Approval Scopes

| Scope | Grants Permission To | Bound To |
|-------|---------------------|----------|
| `baseline_only` | Run `generated_baseline_experiment` | Approval record in registry |
| `final_holdout_only` | Run `generated_final_holdout_experiment` | Decision packet SHA-256 digest |

Both scopes are single-use: the `used` flag on the `scope_approvals` record
prevents re-use. Final holdout approval additionally verifies the on-disk
decision packet digest matches the digest recorded at approval time.

## Manual Authority Gates

1. **Approve for baseline** (`research approve-for-baseline` or equivalent) —
   grants `baseline_only` scope. Requires an `approved_by` human identifier.
2. **Approve for final holdout** (`research approve-for-final-holdout` or
   equivalent) — grants `final_holdout_only` scope bound to the decision
   packet digest. Requires the packet to recommend final holdout.
3. All approvals are recorded in `scope_approvals` with `approval_scope`,
   `scope_metadata_json`, `approved_by`, and `used` flag.

## Forbidden Behavior

- Writing generated `.mq5` files into `automated/strategies/`
- Setting `allow_lifecycle_apply=true` on any generated queue item
- Setting `allow_runner_execution=true` on candidate decision packets
- Weakening validation thresholds via config mutation
- Replacing the runner module at queue execution time
- Proposing lifecycle transitions from decision packets (string-only proposals)
- Any automatic production promotion or live trading path

## Evidence Write Locations

| Location | Contents |
|----------|----------|
| Registry SQLite DB | All approvals, experiments, usages, lifecycle records |
| `SANDBOX_ROOT` (configurable path) | Generated `.mq5` source, compile outputs |
| Artifact paths (on-disk YAML files) | Review packets, decision packets, sweep configs |
| Experiment output directories | Run summaries, trade CSVs, equity curves |

## Generated Code Sandbox

Generated `.mq5` files live under `SANDBOX_ROOT`, never under
`automated/strategies/`. The functions `assert_sandbox_path()` in
`implementation.py` and `check_path_safety()` in `schemas.py` enforce this
at compile check, diff review, and implementation materialization time.

## Why Generated Code Must Not Be Copied Into `automated/strategies/`

- `automated/strategies/` is the home for manually authored, human-reviewed
  production strategies.
- Generated code has not passed manual strategy review — it is experimental.
- Copying generated code out of the sandbox would bypass
  `check_no_production_touch()` and the entire safety invariant chain.
- Operators who want to promote a generated strategy must re-implement it
  manually in `automated/strategies/` after review.

## Pipeline Stage Relationships

| Stage | Purpose | Input | Output |
|-------|---------|-------|--------|
| **Baseline** | Establishes performance floor on a single dataset. One experiment. | Implementation + dataset | Review packet with metrics |
| **Robustness** | Tests stability across parameter variations and config options. Multi-experiment sweep. | Baseline experiment ID + strategy version | Review packet with sweep summary |
| **Candidate Decision** | Evaluates whether robustness results warrant a final holdout. No experiments run. | Robustness review + baseline review | Decision packet with `proposed_next_action` |
| **Final Holdout** | Confirmatory run on unseen data. One experiment. Only if decision packet recommends it. | Decision packet + approval + dataset | Review packet; decision packet is updated with result |

The pipeline is sequential: each stage depends on the previous. Approval
gates between intake/baseline and candidate/final-holdout prevent autonomous
authority escalation.
