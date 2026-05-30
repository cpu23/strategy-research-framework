# Strategy Research Framework

A public-safe framework for a local-first trading strategy research workflow.

This repository includes the reusable research automation code, schemas, tests,
example specifications, and MT5-oriented workflow scripts. It intentionally
excludes private alpha, generated run artifacts, local databases, prompts, and
the `momentum_v1` strategy family.

## Architecture

The framework uses four conceptual layers:

- **Research intake:** capture a strategy idea as a structured hypothesis with
  assumptions, target market conditions, invalidation criteria, and evidence
  requirements.
- **Implementation workflow:** convert accepted hypotheses into implementation
  requests, test plans, and reproducible experiment definitions.
- **Execution and evidence:** run compile checks, backtests, sweeps, and
  validation gates in isolated output directories.
- **Review and registry:** track experiment lineage, pass/fail gates,
  robustness notes, and promotion decisions.

## Public Boundary

The following are deliberately not included:

- `momentum_v1` source code, rules, parameter sets, or run configs
- Backtest outputs, charts, trade logs, equity curves, and dashboards
- Research prompts, generated theses, and private notes
- Local databases and registry snapshots

## Status

This is a sanitized public code snapshot. Private strategies and research
artifacts remain separate.

## Suggested Repository Topics

`trading-research`, `research-framework`, `backtesting`, `experiment-tracking`,
`workflow-architecture`
