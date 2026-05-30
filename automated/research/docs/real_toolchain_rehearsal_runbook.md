# Real-toolchain rehearsal runbook

## Scope

The real-toolchain rehearsal runs the existing readiness sequence as an
operator-controlled, evidence-only dry-run:

1. Real compile verification
2. Bounded real backtest readiness
3. Readiness review packet creation

The rehearsal stops after producing the readiness review packet. It produces
no lifecycle steps, candidate steps, baseline steps, robustness steps,
final-holdout steps, production steps, or live-trading steps.

## What the rehearsal is not

- not baseline
- not robustness
- not final holdout
- not candidate evidence
- not lifecycle evidence
- not production evidence
- not live trading evidence
- does not authorise approvals
- does not authorise production promotion
- does not authorise live trading

## Command pattern

```bash
python3 -m automated.research.cli \
    --db <registry.db> \
    implementation real-toolchain-rehearsal \
    <implementation_request_id> \
    --real-compile-config <compile_config.yaml> \
    --real-backtest-readiness-config <backtest_config.yaml> \
    --out-dir <safe_output_dir>
```

All config paths are required and explicit.

`--out-dir` is required and must not be under `automated/strategies/`.

## Expected output files

All written into `--out-dir`:

| File | Contents |
|------|----------|
| `compile_evidence.json` | Normalised compile evidence with `mode`, `status`, `impl_request_id`, `strategy_id`, `version`, `input_digests` |
| `backtest_readiness_evidence.json` | Backtest readiness evidence with `mode`, `status`, `impl_request_id`, `input_digests` |
| `readiness_review_packet.json` | Readiness review packet aggregating both evidence files |
| `real_toolchain_rehearsal_summary.json` | Summary with `status`, `compile_status`, `backtest_readiness_status`, `output_paths`, `forbidden_interpretations` |

## Summary JSON shape

```json
{
  "artifact_type": "real_toolchain_rehearsal_summary",
  "status": "passed" | "failed",
  "impl_request_id": "...",
  "implementation_id": "...",
  "strategy_id": "...",
  "version": "...",
  "compile_status": "passed" | "failed",
  "backtest_readiness_status": "passed" | "failed" | "timed_out" | "",
  "readiness_review_packet_path": "...",
  "output_paths": {
    "compile_evidence": "...",
    "backtest_readiness_evidence": "...",
    "readiness_review_packet": "...",
    "summary": "..."
  },
  "warnings": ["..."],
  "forbidden_interpretations": [
    "not_baseline_evidence",
    "not_robustness_evidence",
    "not_final_holdout_evidence",
    "not_candidate_evidence",
    "not_lifecycle_evidence",
    "not_production_evidence",
    "not_live_trading_evidence"
  ]
}
```

The summary does not contain `proposed_next_action` or `lifecycle_proposal`.

## Failure behaviour

| Condition | Behaviour |
|-----------|-----------|
| Compile verification fails | Write `compile_evidence.json` and summary. Do **not** run backtest readiness. Do **not** create review packet. Summary status is `"failed"`. Exit 1. |
| Backtest readiness fails | Write `backtest_readiness_evidence.json`. Create review packet reflecting failed readiness status. Summary status is `"failed"`. Exit 1. |
| Review packet creation fails | Write compile and backtest evidence if available. Summary status is `"failed"`. Exit 1. |
| All three succeed | Summary status is `"passed"`. Exit 0. |

## What failure means

Failure means: revise the environment (Wine, MT5, config paths) or revise
generated artifacts (`.mq5`, `.conf`, `.set`). Do **not** weaken validation
thresholds to make the rehearsal pass.

## Safety constraints

- No `.mq5` mutation
- No writes or copies into `automated/strategies/`
- No automatic approval
- No automatic baseline, robustness, final holdout
- No lifecycle apply
- No production promotion
- No live trading
- No runner replacement
- No dataset/cost/symbol/timeframe/validation-threshold mutation
