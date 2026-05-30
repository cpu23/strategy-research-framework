> **LEGACY — Research OS reference docs. The system these describe is being adapted for a simpler workflow. See automated/README.md for current state.**

# Research OS Pipeline

Research OS is an additive research-control layer around the existing MT5/Wine
backtest workflow. It does not replace the MQL5 expert advisors, the `.conf`
run files, the `.set` parameter files, or the shell runner in
`automated/scripts/run_backtest.sh`.

The purpose is reproducibility and review control. A strategy is now tracked as
a research object tied to a hypothesis, strategy spec, dataset hash,
experiment registry row, artifacts, metrics, validation report, portfolio
report, lifecycle state, agent review artifacts, and optional sweeps.

The old direct runner still works:

```bash
automated/scripts/run_backtest.sh automated/runs/fbr_xauusd_h4_baseline.conf
```

The MT5/Wine runner defaults to `xvfb-run` when available. This keeps
MetaEditor and terminal64 on a virtual X display during automated research
runs. For visible GUI troubleshooting, prefix the command with
`MT5_USE_XVFB=0`.

The Research OS path wraps that flow:

```text
strategy spec + dataset
        |
        v
prepare-run
        |
        v
experiment row + run_context.json + generated runner.conf
        |
        v
MT5/Wine runner
        |
        v
attach-artifacts
        |
        v
artifact_manifest.json + metrics + validation report
        |
        v
portfolio reports + lifecycle gates + agent reviews + sweeps
```

## Implemented Modules

```text
automated/research/
├── contracts.py    centralized states, statuses, artifact types, portable paths
├── schemas.py      YAML loaders and hypothesis/strategy spec validation
├── hashing.py      stable hashes for specs, params, configs, costs, code version
├── datasets.py     bars.csv inspection, dataset metadata, dataset_id generation
├── registry.py     SQLite registry schema and persistence helpers
├── runner.py       experiment-aware wrapper around the existing shell runner
├── metrics.py      parsers for trades.csv, equity.csv, run_summary.json
├── validation.py   validation_report_v2 generation and metric upsert
├── portfolio.py    portfolio_report_v1 return-stream and correlation analytics
├── lifecycle.py    strategy lifecycle gates and transition records
├── agents.py       agent contracts, output validation, attach flow, permissions
├── sweeps.py       sweep planning, child materialization, run, summarize
├── queue.py        bounded autonomous research queue and morning reports
└── cli.py          `python3 -m automated.research` entrypoint
```

## Architecture

```text
Research OS
├── Hypothesis Registry
├── Strategy Specification Layer
├── Dataset Metadata / Hashing
├── Experiment Registry
├── Runner Wrapper
├── Artifact Manifest
├── Metrics Parser
├── Validation Reports
├── Portfolio Analytics
├── Lifecycle Gates
├── Agent Contracts and Reviews
├── Sweep Orchestration
└── Research Queue
```

### Hypothesis Registry

Purpose: stores the research-level idea and falsification context.

Main files:

- `hypotheses/*.yaml`
- `automated/research/schemas.py`

Inputs: hypothesis YAML with `hypothesis_id`, mechanism, predictions, failure
modes, initial test, and invalidation rule.

Outputs: validated hypothesis records linked to strategy specs and experiments
by `hypothesis_id`.

Connection: `schemas.resolve_hypothesis_file()` is used by runner and registry
creation paths to confirm the hypothesis exists.

### Strategy Specification Layer

Purpose: provides a canonical research wrapper around existing MT5 files.

Main files:

- `automated/specs/strategies/*.yaml`
- `automated/research/schemas.py`
- `automated/research/hashing.py`

Inputs: YAML strategy spec referencing `.conf`, `.set`, and `.mq5` files.

Outputs: validated spec, `spec_hash`, `parameter_set_hash`,
`execution_config_hash`, `cost_config_hash`, lifecycle state, validation
requirements, research budget, and sweepable parameter inventory.

Connection: specs are used by experiment preparation, validation gates,
lifecycle gates, and sweep child materialization. In the current phase,
`generation_mode: wrapped_existing_files` is the supported mode;
`generated_from_spec` is rejected as reserved for later work.

### Dataset Metadata / Hashing

Purpose: identifies external MT5 history through exported bar data.

Main files:

- `automated/research/datasets.py`
- `automated/research/hashing.py`
- SQLite table `datasets`

Inputs: `bars.csv` export plus symbol/timeframe/source metadata.

Outputs: `dataset_id`, `file_hash`, row count, start/end timestamps,
export timestamp, timezone assumption, missing-data policy, and cleaning rules.

Connection: experiments reference `dataset_id` and store the dataset hash so a
run can be tied to a specific data export. Dataset bundles have schema support
but executable bundle workflows are reserved for later phases.

### Experiment Registry

Purpose: records intentional research runs and their artifacts.

Main files:

- `automated/research/registry.py`
- `automated/research_registry.sqlite`

Inputs: strategy spec, hypothesis, dataset, hashes, code version, change
summary, rationale, parent/rerun links, and research budget snapshot.

Outputs: append-only experiment rows, artifacts, parsed metrics, lifecycle
transitions, sweeps, and sweep child links.

Connection: all higher-level reports and gates read from the registry. Registry
schema version is tracked in table `schema_version` as component
`research_registry`; current code version is `4`.

### Runner Wrapper

Purpose: create an experiment context around the existing runner.

Main files:

- `automated/research/runner.py`
- `automated/scripts/run_backtest.sh`
- `automated/research_runs/<experiment_id>/`

Inputs: strategy spec, dataset id, optional experiment id, optional
`runner_run_id`, change metadata.

Outputs:

- `run_context.json`
- generated `raw/runner.conf`
- `runner_stdout.log` and `runner_stderr.log` when using `experiment run`
- copied raw runner outputs under `raw/`
- `artifact_manifest.json`

Connection: `prepare-run` rewrites only the generated child runner config with
`RUN_ID=<runner_run_id>`. The original `.conf`, `.set`, and `.mq5` files remain
untouched.

### Artifact Manifest

Purpose: records which files were copied from the runner output and whether
required artifacts are present.

Main files:

- `automated/research/runner.py`
- `automated/research_runs/<experiment_id>/artifact_manifest.json`
- SQLite table `experiment_artifacts`

Required result artifacts:

- `trade_log` from `trades.csv`
- `equity_curve` from `equity.csv`
- `metrics_json` from `run_summary.json`
- `raw_backtest_output` from `terminal_run.log`

Additional recognized runner artifacts include `bars`, `tester_agent_log`,
`compile_log`, `mt5_report`, `runner_config`, `runner_stdout`, and
`runner_stderr`.

### Metrics Parser

Purpose: extracts comparable metrics from MT5 runner artifacts.

Main files:

- `automated/research/metrics.py`
- SQLite table `experiment_metrics`

Inputs: `trades.csv`, `equity.csv`, `run_summary.json`.

Outputs: net return, gross profit/loss, profit factor, trade count, win rate,
average/median trade, largest trade diagnostics, long/short counts, drawdown,
longest losing streak, unannualized Sharpe from equity samples, date range, and
availability notes.

Connection: validation writes a normalized `period_type='full'` metrics row.
Unavailable metrics are marked with reasons rather than silently filled.

### Validation Reports

Purpose: generate a machine-readable validation report with hard gates and
warning gates.

Main files:

- `automated/research/validation.py`
- `automated/research_runs/<experiment_id>/reports/validation_report.json`

Inputs: registry experiment row, artifacts, parsed metrics, strategy spec, and
attached sweep summaries.

Outputs: `validation_report_v2`, experiment `gate_status`, experiment status,
attached `validation_report` artifact, and registry metrics.

Connection: validation results feed lifecycle gates. Sweep summaries can fill
advanced validation sections when present.

### Portfolio Analytics

Purpose: evaluates return-stream compatibility across strategy experiments.

Main files:

- `automated/research/portfolio.py`
- `automated/specs/portfolios/*.yaml`
- `automated/research_runs/<experiment_id>/reports/portfolio_report.json`

Inputs: portfolio config listing experiment ids and each experiment's attached
`equity_curve` artifact.

Outputs: `portfolio_report_v1`, per-strategy metrics, correlation matrix,
rolling correlation, tail correlation, drawdown overlap, marginal contribution,
promotion gate summary, warnings, and status.

Connection: portfolio reports can be attached to experiments and consumed by
validation and lifecycle promotion gates.

### Lifecycle Gates

Purpose: manages state transitions for a strategy without hiding gate failures.

Main files:

- `automated/research/lifecycle.py`
- `automated/research/contracts.py`
- SQLite table `lifecycle_transitions`

Inputs: strategy spec lifecycle state, target state, optional experiment id,
validation/portfolio/review artifacts, strictness mode, and optional override.

Outputs: gate evaluation, transition record, gate snapshot JSON, and, only on
`apply`, an updated strategy spec lifecycle state.

Connection: `evaluate` does not mutate; `propose` records a transition request;
`apply` mutates the YAML spec only if gates allow it.

### Agent Contracts and Reviews

Purpose: defines what research agents may do and validates structured review
artifacts.

Main files:

- `automated/research/agents.py`
- `automated/specs/agents/*.yaml`
- SQLite tables `agent_artifacts` and `implementation_tasks`

Inputs: agent contract YAML, structured agent output YAML, experiment id.

Outputs: permission views, output validation, attached experiment artifact, and
attached agent artifact.

Connection: ordinary research agents cannot edit `.mq5` files or backtest
engine logic. Direct implementation-code changes require an `implementation_task`
artifact with human approval outside the normal research flow.

### Sweep Orchestration

Purpose: creates controlled child experiments linked to a parent experiment.

Main files:

- `automated/research/sweeps.py`
- `automated/specs/sweeps/*.yaml`
- `automated/sweeps/<sweep_id>/`
- SQLite tables `sweeps` and `sweep_children`

Inputs: sweep config, parent experiment, strategy spec parameters, budget.

Outputs: `sweep_plan.json`, child experiment rows, child `run_context.json`,
safe materialized child `.set` and `.conf` files, optional child runs, and
`sweep_summary.json`.

Connection: executable parameter robustness sweeps use copied child files under
`automated/research_runs/<child_experiment_id>/raw/`; originals are never
modified. Cost stress, execution delay stress, and walk-forward configs are
currently scaffolded where the runner cannot safely execute them.

### Research Queue

Purpose: lets agents run bounded overnight research work using existing
hypotheses, strategy specs, experiments, sweeps, validation reports, portfolio
analytics, lifecycle gates, and agent review artifacts.

Main files:

- `automated/research/queue.py`
- `automated/specs/research_queue/*.yaml`
- `automated/research_runs/queue_runs/<queue_run_id>/`
- SQLite tables `research_queue_items` and `research_queue_runs`

Inputs: queue YAML, referenced hypothesis, strategy spec, optional parent
experiment, optional inline or referenced sweep config, budgets, permissions,
required outputs, and allowed agent roles.

Outputs: queue item registry rows, queue run summary JSON, prepared/runnable
experiments or sweeps, validation and portfolio artifacts where configured,
pending agent review requests when no LLM backend exists, lifecycle transition
proposals where allowed, and morning reports.

Connection: the queue delegates to existing Research OS APIs. Parameter sweeps
use `sweeps.prepare_sweep` and `sweeps.run_sweep`; baseline tasks use
`runner.prepare_run` and optionally `runner.run_prepared_experiment`; portfolio
tasks use portfolio reports; lifecycle tasks may call
`lifecycle.propose_transition` only.

## Core Concepts

### Hypothesis

A hypothesis is the research-level idea. It lives in YAML under `hypotheses/`
and is linked by `hypothesis_id`.

Implemented example:

```text
hypotheses/HYP_FAILED_BREAKOUT_REVERSAL_001.yaml
```

### Strategy Spec

A strategy spec is the canonical Research OS wrapper. It links a strategy to:

- hypothesis id
- existing `.conf`, `.set`, and `.mq5` implementation files
- costs and execution assumptions
- validation requirements
- research budget
- lifecycle state
- parameter inventory for sweeps

It does not replace the MQL5 implementation in the current phase.

Implemented example:

```text
automated/specs/strategies/failed_breakout_reversal_v1.yaml
```

### Dataset

MT5 history is external to the repo, so Research OS identifies a dataset by the
hash of an exported `bars.csv` file. The current registered example is:

```text
DATA_XAUUSD_H4_0DB89C617104
symbol: XAUUSD
timeframe: H4
rows: 3087
start: 2023.12.29 20:00:00
end: 2025.12.30 16:00:00
```

### Experiment

An experiment is a registry record for an intentional run. It stores identity,
hashes, code version, dataset linkage, change metadata, implementation file
references, status, gate status, artifacts, and metrics.

Parent/child links support sweeps. Rerun links support technical reruns or
artifact regeneration without pretending a new research idea was tested.

### Artifact

Artifact types are centralized in `automated/research/contracts.py`.

Common artifacts:

- `run_context`
- `artifact_manifest`
- `runner_config`
- `trade_log`
- `equity_curve`
- `bars`
- `metrics_json`
- `raw_backtest_output`
- `validation_report`
- `portfolio_report`
- `sweep_plan`
- `sweep_summary`
- `review_request`
- `queue_run_summary`
- `morning_report`
- `statistical_review`
- `portfolio_review`
- `risk_execution_review`
- `red_team_review`
- `research_librarian_summary`
- `implementation_task`

Repo-owned artifact paths are stored as portable relative paths where possible.

### Validation Report

Validation reports are JSON documents with schema version
`validation_report_v2`. Status values are:

- `pass`
- `warn`
- `fail`
- `not_available`
- `not_implemented`

Hard gates include identity, required artifacts, sample size, cost assumptions,
and execution assumptions. Warning/future sections include trade diagnostics,
largest-trade concentration, parameter robustness, cost/delay stress,
walk-forward, and portfolio correlation.

### Portfolio Report

Portfolio reports are JSON documents with schema version `portfolio_report_v1`.
They parse each experiment's `equity.csv`, derive returns, align overlapping
timestamps, and compute correlation and drawdown-overlap diagnostics. The
current implementation warns clearly when only one strategy is available or
history does not overlap.

### Lifecycle

Lifecycle states and allowed transitions are centralized in
`automated/research/contracts.py`. Evaluation is read-only. Proposal records a
transition and gate snapshot. Apply mutates the strategy spec only after gates
allow the transition.

### Agent Contracts

Agent roles live under `automated/specs/agents/`. The validator checks required
fields, enums, booleans, lists, artifact type, and permissions. All ordinary
research contracts have `can_modify_mql5: false`.

### Sweeps

Sweeps create child experiments from a parent experiment. Parameter robustness
children can be executable and materialized safely. Missing replacement keys
fail unless the sweep item uses `allow_add: true`. Cost stress, execution delay
stress, and walk-forward examples are scaffolded unless the runner gains safe
config support for those dimensions.

## Lifecycle Model

Supported strategy states:

```text
idea
hypothesis_defined
baseline_testing
robustness_testing
stat_review
portfolio_review
paper_trading
incubation_capital
production
reduced
retired
archived
```

Allowed forward path:

```text
idea -> hypothesis_defined -> baseline_testing -> robustness_testing
     -> stat_review -> portfolio_review -> paper_trading
     -> incubation_capital -> production
```

Production can move to `reduced`, `retired`, or `archived`; `reduced` can move
back to `production` or onward to `retired`/`archived`; active states can be
archived. Other jumps require `--override` and a reason.

Strictness modes:

- `lenient`: missing advanced review artifacts warn.
- `normal`: required review artifacts block some promotions; advanced missing
  checks usually warn.
- `strict`: missing robustness, walk-forward, portfolio, paper-trading, or
  production-readiness gates can block promotion.

## Agent Review Flow

```text
validation/portfolio reports
        |
        v
external agent writes structured YAML
        |
        v
agent validate-output
        |
        v
agent attach-output
        |
        v
registry experiment_artifacts + agent_artifacts
        |
        v
lifecycle evaluate/propose/apply
```

Roles:

| Role | Purpose | Expected artifact | Allowed actions |
|---|---|---|---|
| `experiment_designer` | Design ceteris-paribus experiment plans before a run exists. | `experiment_plan` | `create_hypothesis_draft`, `propose_experiment_plan`, `propose_lifecycle_transition` |
| `strategy_spec_agent` | Propose strategy YAML changes without editing implementation code. | `strategy_diff_proposal` | `propose_strategy_spec_diff`, `propose_parameter_diff`, `propose_lifecycle_transition` |
| `backtest_runner` | Run approved backtests through the existing Research OS wrapper. | `run_execution_record` | `run_approved_backtest`, `generate_validation_report`, `generate_portfolio_report` |
| `robustness_agent` | Propose and review robustness checks while keeping tests isolated. | `robustness_review` | `propose_experiment_plan`, `propose_parameter_diff`, `generate_validation_report`, `write_structured_review_artifact` |
| `statistical_reviewer` | Review statistical reliability, sample size, trial count, and leakage risk. | `statistical_review` | `write_structured_review_artifact`, `propose_lifecycle_transition` |
| `portfolio_reviewer` | Review portfolio fit, correlations, drawdown overlap, and marginal contribution. | `portfolio_review` | `generate_portfolio_report`, `write_structured_review_artifact`, `propose_lifecycle_transition` |
| `risk_execution_reviewer` | Review execution realism, liquidity, gap risk, and position concentration. | `risk_execution_review` | `write_structured_review_artifact`, `propose_lifecycle_transition` |
| `red_team_reviewer` | Search for reasons the result may be invalid before promotion. | `red_team_review` | `write_structured_review_artifact`, `propose_lifecycle_transition` |
| `research_librarian` | Summarize tested ideas, failed variants, passed variants, and duplication. | `research_librarian_summary` | `write_structured_review_artifact`, `create_hypothesis_draft`, `propose_lifecycle_transition` |

All implemented role contracts forbid ordinary research agents from:

- `edit_mql5`
- `change_order_execution_logic`
- `change_indicator_implementation`
- `change_backtest_engine_logic`
- `change_data_cleaning_logic`
- `change_metric_definitions`

Several contracts also forbid role-specific shortcuts, such as removing bad
assets without structural rationale or changing validation periods after seeing
results unless an override is recorded. Use `agent permissions --role <role>`
for the exact current contract.

A red-team `decision: reject` can block promotion to paper trading outside
lenient mode.

## Sweep Parent/Child Model

```text
parent experiment
        |
        v
sweep config + budget
        |
        v
sweep prepare
        |
        +--> child experiment 000 + copied .set/.conf
        +--> child experiment 001 + copied .set/.conf
        +--> child experiment ...
        |
        v
sweep run --dry-run or sweep run
        |
        v
sweep summarize
        |
        v
sweep_summary artifact attached to parent
```

The one-change-at-a-time research rule still applies. Use
`mode: one_variable_at_a_time` for ordinary robustness. Grid mode exists, but
it increases multiple-testing risk and should be explicitly named and budgeted.

## Autonomous Research Queue

Research OS separates execution from authority:

- Level 0, manual only: a human runs direct shell commands and records results.
- Level 1, prepared automation: Research OS prepares reproducible experiment
  contexts and artifacts, but the human launches runs.
- Level 2, bounded queue execution: the queue may prepare and run approved
  experiments or sweeps within explicit budgets and permissions.
- Level 3, advisory review: agents or placeholders may produce structured
  review artifacts and lifecycle proposals.
- Level 4, manual authority: a human reviews evidence, approves implementation
  tasks, applies lifecycle transitions, changes validation rules, changes
  datasets, or touches MQL5 code.

The implemented queue is Level 2 plus Level 3 proposals. It is not Level 4.

Queue YAMLs live under `automated/specs/research_queue/`. A file can contain a
single queue item or a top-level `items:` list. Items are processed by ascending
`priority`.

Required item fields are `queue_id`, `priority`, `hypothesis_id`,
`strategy_id`, `task_type`, `requested_by`, `allowed_agent_roles`, `budget`,
`permissions`, `required_outputs`, `status`, `created_at`, and `notes`.
`parent_experiment_id` and `sweep_config` are required when the task needs an
existing experiment or sweep plan.

Supported task types:

- `baseline_experiment`
- `parameter_robustness`
- `cost_stress`
- `portfolio_review`
- `red_team_review`
- `research_summary`

Validate a queue:

```bash
python3 -m automated.research queue validate --queue automated/specs/research_queue/failed_breakout_reversal_overnight_example.yaml
```

Preview planned work:

```bash
python3 -m automated.research queue run --queue automated/specs/research_queue/failed_breakout_reversal_overnight_example.yaml --dry-run
```

Run approved overnight work:

```bash
python3 -m automated.research queue run --queue automated/specs/research_queue/failed_breakout_reversal_overnight_example.yaml --mode overnight
```

Generate the morning report:

```bash
python3 -m automated.research queue report --run-id QUEUE_RUN_...
```

Outputs are written under
`automated/research_runs/queue_runs/<queue_run_id>/`. Dry runs report tasks
that would run, experiments that would be created, sweeps that would be
prepared, permissions used, blocked actions, and budget estimates.

Hard-coded queue safety rules:

- no `.mq5` edits
- no metric definition changes
- no validation threshold weakening
- no dataset mutation
- no lifecycle apply
- no final holdout usage by default
- no deletion of failed experiments
- no overwriting artifacts

Forbidden permissions default to false. If a queue item asks for forbidden
authority, validation blocks it unless an explicit human-approved implementation
task exists for the override. Queue execution still never calls lifecycle
apply.

Queue permissions are fail-closed: every known permission field must be present
and boolean, and unknown permission fields are rejected. Queue-owned config and
artifact path fields must be repo-relative and may not contain `..` traversal.
Dry runs plan against existing registry state but do not create queue run rows,
experiment rows, sweep rows, or queue artifacts.

Morning reports are generated as:

```text
automated/research_runs/queue_runs/<queue_run_id>/morning_report.md
automated/research_runs/queue_runs/<queue_run_id>/morning_report.json
```

Sections include summary, queue items processed, experiments created, sweeps
created, best candidates, failed candidates, robustness summaries, portfolio
summaries, red-team objections, lifecycle transition proposals, budget usage,
blocked actions, recommended manual reviews, archive candidates, and next
suggested research actions.

Failed and rejected variants are reported alongside winners. Pending
`review_request` artifacts are not reviews and must not be treated as approvals.

## Testing

Unit tests are under `tests/` and use `unittest`. MT5/Wine is not required for
the tests.

Current verification commands:

```bash
python3 -m compileall -q automated/research tests/test_research_phase1.py
python3 -m unittest discover -s tests -q
```

`pytest` is not required unless the repository later adds it.

## Current Limitations

- No `.mq5` edits are part of ordinary Research OS operation.
- The existing runner writes raw output to `automated/reports/$RUN_ID`.
- Research OS integrates by generating wrapper configs with `RUN_ID` set to the
  experiment or child experiment id.
- Dataset bundles have schema/hash support, but runner preparation currently
  raises `NotImplementedError` for bundle execution.
- Costs are validated from strategy YAML, but MT5 outputs do not itemize spread,
  commission, and slippage.
- Cost stress is scaffolded unless costs become safely executable through
  runner config.
- Execution delay stress is scaffolded.
- Full walk-forward execution is scaffolded unless the runner supports safe
  date-window materialization.
- `equity.csv` timezone is not inferable; timestamps are treated as exported
  tester time.
- Portfolio analytics depend on available equity streams and overlapping
  history.
- Portfolio analytics do not directly infer multiple symbols from account-level
  equity unless the exported file includes symbol information.
- The agent system validates and attaches structured outputs; it does not
  perform reasoning by itself.
- In this local folder, `git_code_version()` currently reports
  `unavailable:not_a_git_repository` because the directory is not a Git repo.

See also:

- [Research CLI](research_cli.md)
- [Research Schemas](research_schemas.md)
- [Research Workflow Example](research_workflow_example.md)
