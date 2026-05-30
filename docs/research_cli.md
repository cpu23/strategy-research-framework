> **LEGACY — Research OS reference docs. The system these describe is being adapted for a simpler workflow. See automated/README.md for current state.**

# Research OS CLI Reference

Entrypoint:

```bash
python3 -m automated.research [--db automated/research_registry.sqlite] <command>
```

The default registry is `automated/research_registry.sqlite`.

## Dataset

Register a bars export as a dataset:

```bash
python3 -m automated.research register-dataset \
  --bars automated/reports/fbr_xauusd_h4_baseline/bars.csv \
  --symbol XAUUSD \
  --timeframe H4 \
  --source-name MT5 \
  --timezone broker_server
```

Outputs JSON containing `dataset_id`, `file_hash`, timestamps, row count, and
metadata.

## Strategy Spec

Validate a strategy spec:

```bash
python3 -m automated.research validate-strategy-spec \
  automated/specs/strategies/failed_breakout_reversal_v1.yaml
```

Outputs `strategy_id`, `strategy_version`, `hypothesis_id`, `spec_hash`,
`parameter_set_hash`, and `status: valid`.

## Experiment

Preferred nested commands are under `experiment`.

Prepare an experiment without running MT5:

```bash
python3 -m automated.research experiment prepare-run \
  --strategy automated/specs/strategies/failed_breakout_reversal_v1.yaml \
  --dataset-id DATA_XAUUSD_H4_0DB89C617104 \
  --change-type baseline \
  --run-reason manual
```

This creates:

- an experiment row with status `prepared`
- `automated/research_runs/<experiment_id>/run_context.json`
- `automated/research_runs/<experiment_id>/raw/runner.conf`
- `run_context` and `runner_config` artifact records

Run the prepared experiment through the existing runner:

```bash
python3 -m automated.research experiment run \
  --strategy automated/specs/strategies/failed_breakout_reversal_v1.yaml \
  --dataset-id DATA_XAUUSD_H4_0DB89C617104 \
  --change-type baseline \
  --run-reason manual
```

The command internally calls:

```text
automated/scripts/run_backtest.sh <generated runner.conf>
```

Attach artifacts from an existing runner report directory:

```bash
python3 -m automated.research experiment attach-artifacts \
  --experiment-id EXP_20260511_152200_phase2_attach_check \
  --output-dir automated/reports/fbr_xauusd_h4_baseline
```

Options:

- `--research-output-dir <dir>` copies into a non-default research run folder.
- `--artifact-regenerated` marks attached artifacts as regenerated.
- `--no-validation` skips automatic validation/portfolio report generation.

Show parsed registry metrics:

```bash
python3 -m automated.research experiment metrics \
  --experiment-id EXP_20260511_152200_phase2_attach_check
```

List experiments:

```bash
python3 -m automated.research list-experiments
```

Legacy top-level commands still exist for compatibility:

```bash
python3 -m automated.research create-experiment ...
python3 -m automated.research attach-artifact ...
python3 -m automated.research attach-result-artifacts ...
python3 -m automated.research generate-validation-report ...
python3 -m automated.research generate-portfolio-report ...
```

Use the nested `experiment`, `validation`, and `portfolio` commands for new
work.

## Validation

Generate a validation report:

```bash
python3 -m automated.research validation generate \
  --experiment-id EXP_20260511_152200_phase2_attach_check
```

Default output:

```text
automated/research_runs/<experiment_id>/reports/validation_report.json
```

Show the validation report:

```bash
python3 -m automated.research validation show \
  --experiment-id EXP_20260511_152200_phase2_attach_check
```

## Portfolio

Validate a portfolio config:

```bash
python3 -m automated.research portfolio validate-config \
  --portfolio automated/specs/portfolios/research_portfolio.yaml
```

Generate a portfolio report:

```bash
python3 -m automated.research portfolio generate \
  --portfolio automated/specs/portfolios/research_portfolio.yaml
```

Show a portfolio report, generating it first if missing:

```bash
python3 -m automated.research portfolio show \
  --portfolio automated/specs/portfolios/research_portfolio.yaml
```

Evaluate an additional candidate against a portfolio:

```bash
python3 -m automated.research portfolio evaluate-candidate \
  --portfolio automated/specs/portfolios/research_portfolio.yaml \
  --candidate-experiment-id EXP_...
```

## Lifecycle

Show current lifecycle state:

```bash
python3 -m automated.research lifecycle show \
  --strategy failed_breakout_reversal_v1
```

Evaluate a transition without mutating:

```bash
python3 -m automated.research lifecycle evaluate \
  --strategy failed_breakout_reversal_v1 \
  --to-state robustness_testing \
  --experiment-id EXP_20260511_152200_phase2_attach_check
```

Strictness modes are `lenient`, `normal`, and `strict`:

```bash
python3 -m automated.research lifecycle evaluate \
  --strategy failed_breakout_reversal_v1 \
  --to-state stat_review \
  --experiment-id EXP_... \
  --strictness strict
```

Propose a transition and write a gate snapshot:

```bash
python3 -m automated.research lifecycle propose \
  --strategy failed_breakout_reversal_v1 \
  --to-state robustness_testing \
  --experiment-id EXP_20260511_152200_phase2_attach_check \
  --reason "Baseline hard gates passed; begin robustness checks"
```

Apply a proposed transition:

```bash
python3 -m automated.research lifecycle apply \
  --transition-id TRANSITION_...
```

Show transition history:

```bash
python3 -m automated.research lifecycle history \
  --strategy failed_breakout_reversal_v1
```

## Agents

List available contracts:

```bash
python3 -m automated.research agent list-contracts
```

Show permissions for a role:

```bash
python3 -m automated.research agent permissions \
  --role red_team_reviewer
```

Validate a structured agent output:

```bash
python3 -m automated.research agent validate-output \
  --file automated/reviews/red_team_example.yaml
```

Attach a validated agent output to an experiment:

```bash
python3 -m automated.research agent attach-output \
  --experiment-id EXP_20260511_152200_phase2_attach_check \
  --file automated/reviews/red_team_example.yaml
```

## Sweeps

Validate a sweep config:

```bash
python3 -m automated.research sweep validate-config \
  --config automated/specs/sweeps/failed_breakout_reversal_v1_parameter_robustness.yaml
```

Prepare child experiments and copied child configs:

```bash
python3 -m automated.research sweep prepare \
  --config automated/specs/sweeps/failed_breakout_reversal_v1_parameter_robustness.yaml
```

List sweeps:

```bash
python3 -m automated.research sweep list
```

Show a sweep and children:

```bash
python3 -m automated.research sweep show \
  --sweep-id SWEEP_...
```

Dry-run child execution:

```bash
python3 -m automated.research sweep run \
  --sweep-id SWEEP_... \
  --dry-run
```

Run child experiments:

```bash
python3 -m automated.research sweep run \
  --sweep-id SWEEP_... \
  --limit 3 \
  --continue-on-error
```

Attach output for a specific child:

```bash
python3 -m automated.research sweep attach-child-artifacts \
  --sweep-id SWEEP_... \
  --child-experiment-id EXP_..._000 \
  --output-dir automated/reports/EXP_..._000
```

Summarize a sweep and attach the summary to the parent:

```bash
python3 -m automated.research sweep summarize \
  --sweep-id SWEEP_...
```

## Verified Command Groups

The current parser exposes these groups:

```text
register-dataset
validate-strategy-spec
create-experiment
attach-artifact
attach-result-artifacts
generate-validation-report
generate-portfolio-report
list-experiments
validation {generate, show}
portfolio {validate-config, generate, show, evaluate-candidate}
experiment {prepare-run, attach-artifacts, run, metrics}
lifecycle {show, evaluate, propose, apply, history}
agent {validate-output, attach-output, permissions, list-contracts}
sweep {validate-config, prepare, list, show, run, summarize, attach-child-artifacts}
```
