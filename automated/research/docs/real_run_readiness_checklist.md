# Real-run readiness checklist

## Scope

This checklist prepares generated strategy research for controlled real MT5/Wine verification.

It does not authorize production promotion, lifecycle changes, live trading, dataset mutation, cost mutation, symbol mutation, timeframe mutation, or runner replacement.

## Required safety invariants

- Generated implementations remain under `automated/generated_strategies/<strategy_id>/<version>/`.
- No generated implementation is written or copied into `automated/strategies/`.
- No lifecycle transition is applied.
- No production, live-trading, or promote-to-production candidate is proposed.
- No live trading is performed.
- No validation threshold is weakened.
- No dataset, cost, symbol, or timeframe is mutated.
- No runner replacement occurs.

## MT5/Wine environment checklist

- Wine is installed and callable.
- The intended Wine prefix is explicit.
- MT5 terminal path is explicit.
- MetaEditor/compiler path is explicit, if compile uses MetaEditor.
- The configured terminal data directory is explicit.
- MQL5 include paths are known.
- Compiler logs are writable.
- Backtest logs are writable.
- The machine has enough disk space for bounded reports and logs.
- The operator knows whether the run is mock compile, real compile, mock backtest, or bounded real backtest.
- A real compile config YAML file exists (see [real_compile_verification.md](real_compile_verification.md) for schema).
- The real compile config specifies the correct `wine_binary`, `metaeditor_path`, and `wine_prefix`.
- `--mock` and `--real-compile-config` are mutually exclusive — the operator must choose one.

## Generated artifact checklist

- Strategy ID is explicit.
- Version is explicit.
- Hypothesis evidence exists.
- Generated strategy spec exists.
- Sandbox `.mq5` implementation exists.
- Declared runner `.conf` file exists.
- Declared `.set` file exists.
- Compile-check artifact exists before diff-review.
- Diff-review artifact exists before research review packet.
- Research review packet exists before baseline approval.

## Approval checklist

- Baseline requires explicit human `baseline_only` approval.
- Baseline approval is single-use by default.
- Baseline approval is distinct from final holdout approval.
- Final holdout requires explicit human `final_holdout_only` approval.
- Final holdout approval is single-use by default.
- Final holdout approval is bound to the candidate decision packet SHA-256 digest.
- Edited candidate packets must fail digest verification.
- Queue permission must not substitute for human approval.

## Baseline and robustness checklist

- Baseline evidence exists before robustness.
- Robustness uses only whitelisted sweep parameters.
- Robustness does not mutate dataset, cost, symbol, timeframe, or validation thresholds.
- Robustness review is evidence-only.
- Warnings are preserved and not converted into approval.

## Final holdout checklist

- Candidate decision packet recommends `request_human_review_for_final_holdout`.
- Candidate decision packet lifecycle proposal is `final_holdout_candidate`.
- Baseline evidence exists.
- Robustness evidence exists.
- Compile, diff, and spec gates pass.
- Matching unused `final_holdout_only` approval exists.
- Approval packet digest matches the candidate decision packet.
- Final holdout review is evidence-only.
- Final holdout does not trigger lifecycle apply, production promotion, copy to production, or live trading.

## Output safety checklist

After any real-run readiness or smoke verification, confirm:

- No files were written to `automated/strategies/`.
- No lifecycle transition rows were created.
- No production candidate proposal was emitted.
- No live-trading candidate proposal was emitted.
- No promote-to-production proposal was emitted.
- No live trading occurred.
- Existing validation thresholds were not weakened.
- Dataset, cost, symbol, and timeframe identifiers did not change.
- Runner path was not replaced.

## Recommended real verification sequence

The preferred real verification sequence is:

1. real MT5/Wine compile (via `implementation compile-check --real-compile-config`)
2. bounded real backtest readiness (via `implementation backtest-readiness --real-backtest-readiness-config`)
3. mock backtest (via `generated-baseline run --run --runner-script <mock>`)
4. no lifecycle action
5. no production copy
6. no live trading

Steps 1 and 2 are operator-only CLI commands. They are not queue tasks and not
part of the automated baseline pipeline.

## See also

- [Real compile verification](real_compile_verification.md) — detailed procedure for the first real MT5/Wine compile check.
- [Real backtest readiness](real_backtest_readiness.md) — bounded backtest readiness verification procedure.
