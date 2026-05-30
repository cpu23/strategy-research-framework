> **LEGACY — Research OS reference docs. The system these describe is being adapted for a simpler workflow. See automated/README.md for current state.**

# Research OS Schemas and Registry Reference

This page documents implemented contracts. Source of truth is the code under
`automated/research/`.

## Central Contracts

`automated/research/contracts.py` defines shared enumerations.

Experiment statuses:

```text
planned
prepared
running
completed
completed_with_warnings
failed
invalid
```

Gate statuses:

```text
incomplete
pass
warn
fail
not_available
not_implemented
```

Lifecycle transition statuses:

```text
proposed
approved
rejected
applied
```

Strictness modes:

```text
lenient
normal
strict
```

Sweep types:

```text
parameter_robustness
cost_stress
execution_delay_stress_scaffold
walk_forward_scaffold
```

Artifact types are also centralized in `contracts.py`. Registry writes reject
unknown artifact types.

## Hypothesis YAML

Validated by `automated/research/schemas.py`.

Required scalar fields:

```text
hypothesis_id
name
status
mechanism
expected_edge
initial_test
invalidation_rule
created_at
updated_at
```

Required non-empty lists:

```text
timeframes
markets
predictions
failure_modes
```

Implemented example:

```text
hypotheses/HYP_FAILED_BREAKOUT_REVERSAL_001.yaml
```

## Strategy Spec YAML

Validated by `automated/research/schemas.py`.

Required scalar fields:

```text
strategy_id
strategy_version
hypothesis_id
status
timeframe
created_at
updated_at
invalidation_rule
```

Required sections:

```text
universe
entry
exit
risk
implementation
costs
execution_timing
validation
research_budget
lifecycle
```

Implementation rules:

- `implementation.engine` must be `mt5`.
- `implementation.generation_mode` may be `wrapped_existing_files` or
  `generated_from_spec`, but `generated_from_spec` is currently rejected as
  reserved for a later phase.
- `implementation.files.config`, `parameters`, and `expert_advisor` must point
  at existing files when normal validation is used.

Cost rules:

- `costs.assumptions_documented` must be `true`.
- `spread_source`, `slippage`, and `commission` must be mappings.

Execution rules:

- `execution_timing.signal_bar` is required.
- `execution_timing.entry_bar` is required.
- `execution_timing.assumed_fill_price` is required.

Validation rules:

- `validation.min_trades_required` must be a positive integer.
- Optional implemented fields include
  `largest_trade_concentration_warning_threshold`,
  `min_trades_required_hard`, and `trade_count_unavailable_policy`.

Research budget rules:

- `max_structural_variants`
- `max_parameter_sets`
- `max_filter_additions`
- `max_agent_iterations`
- `max_complexity_score`

Each must be a non-negative integer.

Lifecycle rules:

- `lifecycle.state` must be one of the lifecycle states in `contracts.py`.
- `lifecycle.allowed_next_states`, when present, must contain lifecycle states.

Implemented example:

```text
automated/specs/strategies/failed_breakout_reversal_v1.yaml
```

## Portfolio Config YAML

Validated by `automated/research/portfolio.py`.

Required fields:

```text
portfolio_id
name
experiments
```

Rules:

- `experiments` must be a non-empty list of experiment ids.
- `frequency` must be `daily` or `raw`; default is `daily`.
- `correlation.rolling_windows` must contain integers >= 2.
- `correlation.tail_threshold_quantile` must be between 0 and 1.
- `promotion_thresholds.max_average_abs_corr`,
  `max_tail_corr`, and `max_drawdown_overlap` must be non-negative numbers.
- `promotion_thresholds.breach_status` may be `warn` or `fail`; invalid values
  are treated as `warn`.

Implemented example:

```text
automated/specs/portfolios/research_portfolio.yaml
```

## Agent Contracts and Outputs

Contract files live under:

```text
automated/specs/agents/*.yaml
```

Implemented roles:

```text
experiment_designer
strategy_spec_agent
backtest_runner
robustness_agent
statistical_reviewer
portfolio_reviewer
risk_execution_reviewer
red_team_reviewer
research_librarian
```

Every contract requires:

```text
role_name
purpose
allowed_actions
forbidden_actions
required_inputs
output_schema
artifact_type
can_modify_code
requires_implementation_task
```

`can_modify_mql5` must be false. Direct `.mq5` edits require an
`implementation_task` output and are outside ordinary research flow.

Agent output schemas are implemented in `automated/research/agents.py`.
Validated outputs are attached both to `experiment_artifacts` and
`agent_artifacts`.

## Sweep Config YAML

Validated by `automated/research/sweeps.py`.

Common required fields:

```text
sweep_type
parent_experiment_id
budget
```

Budget:

```text
budget.max_child_experiments
budget.max_parameters_changed_per_child
budget.require_one_variable_at_a_time
```

For `parameter_robustness`:

- `mode` must be `one_variable_at_a_time` or `grid`.
- `parameters` must be a non-empty mapping.
- Each parameter may be a list of values or a mapping with `values`, `key` /
  `set_key`, and optional `allow_add`.
- In one-variable mode, missing baseline keys fail unless `allow_add: true`.
- Child count cannot exceed `budget.max_child_experiments`.
- Child parameter changes cannot exceed
  `budget.max_parameters_changed_per_child`.

For `cost_stress`:

- `cost_multipliers` must be a non-empty list.
- Children are currently non-executable scaffolds because the runner does not
  safely materialize cost assumptions into MT5 config.

For `execution_delay_stress_scaffold` and `walk_forward_scaffold`:

- Configs validate as sweep types, but planning returns scaffold warnings and
  no executable children in the current implementation.

Implemented examples:

```text
automated/specs/sweeps/failed_breakout_reversal_v1_parameter_robustness.yaml
automated/specs/sweeps/failed_breakout_reversal_parameter_robustness_example.yaml
automated/specs/sweeps/cost_stress_scaffold_example.yaml
automated/specs/sweeps/execution_delay_stress_scaffold_example.yaml
automated/specs/sweeps/walk_forward_scaffold_example.yaml
```

## SQLite Registry

Default path:

```text
automated/research_registry.sqlite
```

Schema is created in `automated/research/registry.py`. The migration file
`automated/migrations/001_create_registry.sql` points operators to that
canonical schema.

Tables:

```text
schema_version
datasets
dataset_bundles
experiments
experiment_metrics
experiment_artifacts
agent_artifacts
implementation_tasks
lifecycle_transitions
sweeps
sweep_children
```

### `schema_version`

Tracks registry version:

```text
component: research_registry
version: 3
applied_at: ISO timestamp
```

### `datasets`

Stores:

- `dataset_id`
- source type/name, broker, server
- symbol and timeframe
- start/end timestamps
- row count
- bars file path and hash
- export timestamp
- timezone assumption
- missing data policy
- cleaning rules
- metadata JSON

### `experiments`

Stores:

- identity: `experiment_id`, UUID, `hypothesis_id`, `strategy_id`, version
- run metadata: reason, creator, timestamps, status, gate status
- reproducibility hashes: `spec_hash`, `parameter_set_hash`, `dataset_hash`,
  `dataset_bundle_hash`, `code_version`, `execution_config_hash`,
  `cost_config_hash`
- implementation metadata: engine, implementation files, implementation mode
- research metadata: execution timing, timeframe, universe, parent/rerun links,
  change type, change summary, rationale, parameter/structural diffs
- budget snapshot and complexity score
- validation metadata and headline metrics JSON

### `experiment_metrics`

Stores normalized metrics keyed by `(experiment_id, period_type)`, including:

- net return
- CAGR
- Sharpe
- Sortino
- max drawdown
- Calmar
- win rate
- average and median trade
- profit factor
- exposure time
- turnover
- trade count
- best trade percent of total
- cost sensitivity score
- parameter stability score
- correlation to portfolio
- notes

Unavailable fields are stored as `NULL`.

### `experiment_artifacts`

Stores experiment artifact type, portable path where possible, SHA-256 hash,
timestamp, and `artifact_regenerated`.

### `agent_artifacts`

Stores validated agent outputs by experiment, role, artifact type, path, hash,
and timestamp.

### `implementation_tasks`

Stores implementation change requests, including requested files, expected
behavior change, required tests, human approval, and status. This table is the
escape hatch for code-level changes, including `.mq5` work.

### `lifecycle_transitions`

Stores proposed/applied transition records, including:

- transition id
- strategy id and spec path
- from/to state
- experiment id
- requester/approver
- reason
- gate snapshot path
- status
- notes
- override flag

### `sweeps` and `sweep_children`

`sweeps` stores parent experiment, strategy, hypothesis, type, status, budget,
config, summary path, and notes.

`sweep_children` stores each child experiment id, index, role, parameter/cost/
execution/window diffs, and status.

## Reproducibility Fields

To reproduce or audit a run, collect:

- `experiment_id`
- `strategy_id` and `strategy_version`
- `hypothesis_id`
- strategy spec file and `spec_hash`
- `.set` file and `parameter_set_hash`
- generated runner config and `execution_config_hash`
- dataset row with `dataset_id` and `dataset_hash`
- `code_version`
- `cost_config_hash`
- `run_context.json`
- `artifact_manifest.json`
- raw artifacts copied under `automated/research_runs/<experiment_id>/raw/`

`code_version` is captured with `git rev-parse HEAD` plus `-dirty` when
available. If the folder is not a Git repository, it is recorded as
`unavailable:not_a_git_repository`.

## Validation Report v2

Generated by `automated/research/validation.py`.

Sections:

- `identity_checks`
- `artifact_checks`
- `trade_diagnostics`
- `sample_size_gate`
- `concentration_gate`
- `cost_assumption_gate`
- `execution_assumption_gate`
- `placeholder_advanced_gates`

Advanced gates currently include:

- `parameter_robustness`
- `cost_delay_stress`
- `walk_forward`
- `portfolio_correlation`

If a matching `sweep_summary` artifact is attached, validation uses it for the
corresponding advanced section. Otherwise sections are marked
`not_implemented` or `not_available` with warnings.

Known limitations:

- Costs are documented and validated from YAML; MT5 output does not itemize
  fees/spread/slippage.
- Cost/delay stress is not executable unless represented by a safe runner
  config change.
- Walk-forward validation is scaffolded.
- Timezone is not inferable from `equity.csv`.
- Portfolio correlation can warn when only one strategy or insufficient overlap
  is available.

## Portfolio Report v1

Generated by `automated/research/portfolio.py`.

Equity parser assumptions:

- Uses `equity`, `balance`, `account_equity`, `cumulative_pnl`, `cum_pnl`, or
  `pnl` columns when available.
- `equity`, `balance`, and `account_equity` with positive values are treated as
  absolute account values.
- Returns are derived from equity changes.
- `daily` frequency compounds intraday samples by date; `raw` uses raw sample
  timestamps.
- Sampling may be irregular.
- Timezone is reported as `not_inferable_from_equity_csv`.
- Multiple symbols are only visible if the exported equity file has symbol
  columns; account-level equity alone does not encode symbol exposure.

Metrics:

- per-strategy annualized return/volatility, Sharpe, max drawdown, skew
- correlation matrix
- average absolute correlation
- rolling correlation summaries
- tail correlations against an equal-weight peer portfolio
- drawdown overlap
- marginal volatility and Sharpe contribution
- promotion gate summary

## Sweep Summary v1

Generated by `automated/research/sweeps.py`.

For `parameter_robustness`, the summary includes:

- parent metrics
- child metrics
- number of completed children
- percent profitable
- percent with PF > 1
- median Sharpe
- median net return
- median drawdown
- degradation from parent
- plateau score by parameter

For `cost_stress`, summaries currently report `not_available` with reason
`not_executable_with_current_runner`.

For execution-delay and walk-forward scaffold sweeps, summaries report
`not_implemented`.
