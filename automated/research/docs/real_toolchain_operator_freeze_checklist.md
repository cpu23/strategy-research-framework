# Real-toolchain operator freeze checklist

Before and after any real-toolchain readiness workflow execution (compile
verification, backtest readiness, readiness review, rehearsal), the operator
must confirm all items below.

## Sandbox isolation

- [ ] Generated strategy implementation is under `automated/generated_strategies/<strategy_id>/<version>/`
- [ ] No artifact was written or copied into `automated/strategies/`
- [ ] No `.mq5` file was silently mutated

## Config explicitness

- [ ] Real compile config YAML exists with explicit `mode: real_compile`
- [ ] Real backtest readiness config YAML exists with explicit `mode: real_backtest_readiness`
- [ ] All config paths (`wine_binary`, `metaeditor_path`, `terminal_path`, etc.) are explicit
- [ ] Config `timeout_seconds` and `max_duration_seconds` are positive and bounded (≤ 600)

## Output safety

- [ ] Output directory is explicit and was provided via `--out` or `--out-dir`
- [ ] Output directory is **not** under `automated/strategies/`
- [ ] Output directory is **not** under `automated/research/`
- [ ] All evidence files are inside the declared output directory

## Runner fixity

- [ ] Runner script (`automated/scripts/run_backtest.sh`) is unchanged
- [ ] `BROKER` in runner `.conf` is `"mock"`
- [ ] No runner replacement occurred

## Dataset / cost / symbol / timeframe fixity

- [ ] `expected_symbol` in readiness config matches the generated `.conf` `SYMBOL`
- [ ] `expected_timeframe` in readiness config matches the generated `.conf` `TIMEFRAME`
- [ ] `expected_dataset_id` (if provided) matches the generated `.conf` dataset
- [ ] No dataset, cost, symbol, or timeframe identifiers were mutated

## Validation threshold stability

- [ ] `min_trades_required` in strategy spec was not weakened
- [ ] No validation threshold was changed to make the rehearsal pass

## Queue non-use

- [ ] The queue was **not** used to execute compile verification
- [ ] The queue was **not** used to execute backtest readiness
- [ ] The queue was **not** used to execute readiness review
- [ ] The queue was **not** used to execute rehearsal
- [ ] No queue task type exists for readiness or rehearsal commands

## No candidate decision from readiness artifacts

- [ ] Readiness review packet was **not** passed to `generated_candidate.build_generated_candidate_decision_packet()`
- [ ] Rehearsal summary was **not** passed to `generated_candidate.build_generated_candidate_decision_packet()`
- [ ] Backtest readiness evidence was **not** passed to `generated_baseline.build_generated_baseline_review_packet()`
- [ ] Backtest readiness evidence was **not** passed to `generated_final_holdout.build_generated_final_holdout_review_packet()`

## No lifecycle / approval / production / live trading

- [ ] No lifecycle transition was created
- [ ] No scope approval was created
- [ ] No production promotion was proposed
- [ ] No production candidate or live-trading candidate was proposed
- [ ] No live trading occurred
- [ ] Readiness review packet `proposed_next_manual_action` does not include
      `approve_baseline`, `approve_final_holdout`, `promote_to_production`,
      `production_candidate`, or `live_trading_candidate`
- [ ] Rehearsal summary does not contain `proposed_next_action` or `lifecycle_proposal`

## Test suite

- [ ] Full test suite passes:
      `python3 -m unittest discover -s tests -p 'test_*.py'`
