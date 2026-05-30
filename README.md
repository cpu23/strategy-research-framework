# Strategy Research Framework

A public-safe architecture snapshot for a local-first trading strategy research
workflow.

This repository intentionally excludes proprietary strategy rules, hypotheses,
backtest results, generated reports, prompts, dashboards, and run artifacts. It
is meant to document the operating model and repository boundaries for a
repeatable research system without publishing private alpha.

## Architecture

The private system uses four conceptual layers:

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

- Concrete strategies or expert advisor source code
- Strategy rules, parameter sets, or optimization sweeps
- Backtest outputs, charts, trade logs, equity curves, and dashboards
- Research prompts, generated theses, and private notes
- Local databases and registry snapshots

## Status

This is a sanitized public shell. The private implementation remains separate.

## Suggested Repository Topics

`trading-research`, `research-framework`, `backtesting`, `experiment-tracking`,
`workflow-architecture`
