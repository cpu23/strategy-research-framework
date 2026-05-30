> **LEGACY — Research OS reference docs. The system these describe is being adapted for a simpler workflow. See automated/README.md for current state.**

# Research Workflow Example: `failed_breakout_reversal_v1`

This is the first migrated Research OS example. It is useful because it wraps a
real existing MT5 strategy without editing the `.mq5` file or changing runner
behavior.

## Files

Hypothesis:

```text
hypotheses/HYP_FAILED_BREAKOUT_REVERSAL_001.yaml
```

Strategy spec:

```text
automated/specs/strategies/failed_breakout_reversal_v1.yaml
```

Existing implementation files referenced by the spec:

```text
automated/runs/fbr_xauusd_h4_baseline.conf
automated/runs/sets/fbr_xauusd_h4_baseline.set
automated/strategies/failed_breakout_reversal_v1/FailedBreakoutReversalV1.mq5
```

Portfolio config:

```text
automated/specs/portfolios/research_portfolio.yaml
```

Sweep examples:

```text
automated/specs/sweeps/failed_breakout_reversal_v1_parameter_robustness.yaml
automated/specs/sweeps/failed_breakout_reversal_parameter_robustness_example.yaml
automated/specs/sweeps/cost_stress_scaffold_example.yaml
automated/specs/sweeps/execution_delay_stress_scaffold_example.yaml
automated/specs/sweeps/walk_forward_scaffold_example.yaml
```

Current registered dataset:

```text
DATA_XAUUSD_H4_0DB89C617104
symbol: XAUUSD
timeframe: H4
rows: 3087
start: 2023.12.29 20:00:00
end: 2025.12.30 16:00:00
```

Current example experiments in the registry:

```text
EXP_20260511_151915_failed_breakout_reversal_v1
EXP_20260511_152200_phase2_attach_check
```

Both are `completed_with_warnings` with `gate_status: warn`.

## Hypothesis

The hypothesis is that failed breakouts in range or chop regimes can trap
breakout participants and support a reversal toward the range midpoint.

The initial test is XAUUSD H4 with ADX and ATR-percentile regime filters.

The invalidation rule rejects or redesigns the idea if isolated improvements
cannot produce positive expectancy across nearby breakout-distance thresholds
and at least one additional liquid market/timeframe without materially worse
drawdown.

## Validate the Strategy Spec

```bash
python3 -m automated.research validate-strategy-spec \
  automated/specs/strategies/failed_breakout_reversal_v1.yaml
```

Expected behavior: outputs JSON with `status: valid`, the `spec_hash`, and the
baseline `.set` `parameter_set_hash`.

## Prepare a Baseline Experiment

```bash
python3 -m automated.research experiment prepare-run \
  --strategy automated/specs/strategies/failed_breakout_reversal_v1.yaml \
  --dataset-id DATA_XAUUSD_H4_0DB89C617104 \
  --change-type baseline \
  --run-reason manual
```

This writes:

```text
automated/research_runs/<experiment_id>/run_context.json
automated/research_runs/<experiment_id>/raw/runner.conf
```

The generated `runner.conf` points `RUN_ID` at the experiment id. The baseline
`.conf`, `.set`, and `.mq5` files are not modified.

## Run Through MT5/Wine

The standard runner uses `xvfb-run` by default when available, so MT5/Wine
should not open visible desktop windows during normal research runs. Use
`MT5_USE_XVFB=0` only for visible GUI troubleshooting.

Use the Research OS wrapper:

```bash
python3 -m automated.research experiment run \
  --strategy automated/specs/strategies/failed_breakout_reversal_v1.yaml \
  --dataset-id DATA_XAUUSD_H4_0DB89C617104 \
  --change-type baseline \
  --run-reason manual
```

Or use the generated config with the existing runner:

```bash
automated/scripts/run_backtest.sh \
  automated/research_runs/<experiment_id>/raw/runner.conf
```

The existing runner still writes raw MT5 output to:

```text
automated/reports/<RUN_ID>/
```

## Attach Existing Runner Artifacts

If the run was executed outside `experiment run`, attach its output:

```bash
python3 -m automated.research experiment attach-artifacts \
  --experiment-id EXP_20260511_152200_phase2_attach_check \
  --output-dir automated/reports/fbr_xauusd_h4_baseline
```

The example attached research output exists at:

```text
automated/research_runs/EXP_20260511_152200_phase2_attach_check/
```

Important files:

```text
run_context.json
artifact_manifest.json
raw/trades.csv
raw/equity.csv
raw/bars.csv
raw/run_summary.json
raw/terminal_run.log
raw/tester_agent.log
raw/compile.log
reports/validation_report.json
reports/portfolio_report.json
```

## Inspect Metrics and Validation

Show normalized metrics:

```bash
python3 -m automated.research experiment metrics \
  --experiment-id EXP_20260511_152200_phase2_attach_check
```

Current stored headline from the registry:

```text
trades: 34
win rate: 38.24
net_return: -0.0095679
profit_factor: 0.89
max_drawdown: 2.8
longest losing streak: available in validation metrics as consecutive losses
status: completed_with_warnings
gate_status: warn
```

Generate or refresh validation:

```bash
python3 -m automated.research validation generate \
  --experiment-id EXP_20260511_152200_phase2_attach_check
```

Show validation:

```bash
python3 -m automated.research validation show \
  --experiment-id EXP_20260511_152200_phase2_attach_check
```

Current validation status:

```text
schema_version: validation_report_v2
gate_status: warn
hard_failures: []
```

Known warnings are expected because advanced robustness, cost/delay stress,
walk-forward, and portfolio-correlation sections are scaffolded or unavailable
until their supporting runs/reports exist.

## Portfolio Review

Validate config:

```bash
python3 -m automated.research portfolio validate-config \
  --portfolio automated/specs/portfolios/research_portfolio.yaml
```

Generate portfolio report:

```bash
python3 -m automated.research portfolio generate \
  --portfolio automated/specs/portfolios/research_portfolio.yaml
```

The example portfolio config lists:

```text
EXP_20260511_151915_failed_breakout_reversal_v1
EXP_20260511_152200_phase2_attach_check
```

Because these are related examples, portfolio analytics should be interpreted as
workflow validation, not evidence of diversification.

## Lifecycle Gates

Show current lifecycle:

```bash
python3 -m automated.research lifecycle show \
  --strategy failed_breakout_reversal_v1
```

The current spec state is:

```text
baseline_testing
```

Evaluate promotion to robustness testing:

```bash
python3 -m automated.research lifecycle evaluate \
  --strategy failed_breakout_reversal_v1 \
  --to-state robustness_testing \
  --experiment-id EXP_20260511_152200_phase2_attach_check
```

Evaluate later promotion with strict mode:

```bash
python3 -m automated.research lifecycle evaluate \
  --strategy failed_breakout_reversal_v1 \
  --to-state stat_review \
  --experiment-id EXP_20260511_152200_phase2_attach_check \
  --strictness strict
```

Strict mode can block if robustness or walk-forward sections remain missing,
unavailable, or only scaffolded.

## Agent Reviews

List role contracts:

```bash
python3 -m automated.research agent list-contracts
```

Show red-team permissions:

```bash
python3 -m automated.research agent permissions \
  --role red_team_reviewer
```

Attach reviews only after validating their structured YAML:

```bash
python3 -m automated.research agent validate-output \
  --file automated/reviews/red_team_failed_breakout_reversal.yaml

python3 -m automated.research agent attach-output \
  --experiment-id EXP_20260511_152200_phase2_attach_check \
  --file automated/reviews/red_team_failed_breakout_reversal.yaml
```

A `red_team_reviewer` decision of `reject` can block promotion to paper trading
outside lenient mode.

## Parameter Robustness Sweep

Validate the implemented FBR sweep config:

```bash
python3 -m automated.research sweep validate-config \
  --config automated/specs/sweeps/failed_breakout_reversal_v1_parameter_robustness.yaml
```

Prepare the sweep:

```bash
python3 -m automated.research sweep prepare \
  --config automated/specs/sweeps/failed_breakout_reversal_v1_parameter_robustness.yaml
```

This creates a sweep row, child experiment rows, a sweep plan, and child
materialized files:

```text
automated/sweeps/<sweep_id>/sweep_plan.json
automated/research_runs/<child_experiment_id>/run_context.json
automated/research_runs/<child_experiment_id>/raw/parameters.set
automated/research_runs/<child_experiment_id>/raw/materialized_base.conf
automated/research_runs/<child_experiment_id>/raw/runner.conf
```

Dry-run child execution:

```bash
python3 -m automated.research sweep run \
  --sweep-id SWEEP_... \
  --dry-run
```

Summarize after children have metrics:

```bash
python3 -m automated.research sweep summarize \
  --sweep-id SWEEP_...
```

The summary attaches a `sweep_summary` artifact to the parent experiment so
validation and lifecycle gates can consume it.

## Interpretation

This migrated example is a control-plane demonstration, not a profitable-system
claim. Current baseline metrics are weak, while identity, artifact, cost, and
execution documentation gates pass. Treat the warning status as research debt:
complete robustness, portfolio, statistical, risk/execution, and red-team
review before any promotion beyond research stages.
