# Readiness review packet

## Scope

The readiness review packet aggregates real compile evidence and real backtest
readiness evidence into a single operator-reviewable JSON document.

It is **operator review material only**. It does **not** count as candidate
evidence, baseline evidence, robustness evidence, final holdout evidence,
lifecycle evidence, production evidence, or live trading evidence.

## Command pattern

```bash
python3 -m automated.research.cli \
    --db <registry.db> \
    implementation readiness-review \
    <implementation_request_id> \
    --compile-evidence <compile.json> \
    --backtest-readiness-evidence <backtest.json> \
    --out <packet.json>
```

- `<compile.json>` must be a JSON file produced by `implementation compile-check --real-compile-config`.
- `<backtest.json>` must be a JSON file produced by `implementation backtest-readiness --real-backtest-readiness-config`.
- `--out` is optional. If omitted, the packet is printed to stdout.
- `--out` must **not** be under `automated/strategies/`.
- `--strategy-id` and `--version` are optional; they default to values found in the compile evidence.

### Example: stdout output

```bash
python3 -m automated.research.cli \
    implementation readiness-review \
    IMPL_REQ_20260513_EXAMPLE \
    --compile-evidence /tmp/compile_20260513.json \
    --backtest-readiness-evidence /tmp/bt_20260513.json
```

### Example: file output

```bash
python3 -m automated.research.cli \
    implementation readiness-review \
    IMPL_REQ_20260513_EXAMPLE \
    --compile-evidence /tmp/compile_20260513.json \
    --backtest-readiness-evidence /tmp/bt_20260513.json \
    --out /tmp/readiness_review_20260513.json
```

## Required inputs

1. **Compile evidence JSON** — must have `mode: "real_compile"` and a recognized `status` (`"passed"` or `"failed"`).
2. **Backtest readiness evidence JSON** — must have `mode: "real_backtest_readiness"` and a recognized `status` (`"passed"`, `"failed"`, or `"timed_out"`).

The packet builder validates the evidence shapes, checks consistency of
`impl_request_id`, `strategy_id`, `version`, and `implementation_id` between
the two evidence files, and checks path safety (no production paths touched).

## Expected packet fields

| Field | Description |
|-------|-------------|
| `artifact_type` | Always `"generated_readiness_review"` |
| `schema_version` | Schema identifier (`"generated_readiness_review_v1"`) |
| `review_packet_id` | Unique review packet identifier |
| `generated_at` | ISO-8601 timestamp of packet creation |
| `strategy_id` | Strategy identifier |
| `version` | Strategy version |
| `impl_request_id` | Implementation request ID |
| `implementation_id` | Implementation ID |
| `compile_readiness` | Object: `status`, `evidence_path`, `input_digests` |
| `backtest_readiness` | Object: `status`, `evidence_path`, `input_digests` |
| `path_safety` | Object: `generated_sandbox_only`, `production_paths_touched` |
| `warnings` | List of consistency or safety warnings |
| `proposed_next_manual_action` | One of the allowed safe values (see below) |
| `forbidden_interpretations` | List of strings stating what this packet is NOT |

### Allowed `proposed_next_manual_action` values

Only these values are permitted:

- `manual_review_only` — default. Operator should review the evidence manually.
- `defer` — defer the readiness decision.
- `revise_environment` — environment configuration needs revision.
- `revise_generated_artifacts` — generated `.mq5`, `.conf`, or `.set` files need revision.

The following are **never** produced by the readiness review:

- `approve_baseline`
- `approve_final_holdout`
- `request_human_review_for_final_holdout`
- `promote_to_production`
- `production_candidate`
- `live_trading_candidate`

### Forbidden interpretations

The packet includes a `forbidden_interpretations` field that lists what the
packet is NOT:

- `not_baseline_evidence`
- `not_robustness_evidence`
- `not_final_holdout_evidence`
- `not_candidate_evidence`
- `not_lifecycle_evidence`
- `not_production_evidence`
- `not_live_trading_evidence`

## Input digests

The `compile_readiness.input_digests` and `backtest_readiness.input_digests`
fields contain SHA-256 hex digests of input files:

**Compile:**
- `generated_mq5` — SHA-256 of generated `.mq5`
- `compile_config` — SHA-256 of compile config YAML (when available)

**Backtest readiness:**
- `generated_mq5` — SHA-256 of generated `.mq5`
- `generated_conf` — SHA-256 of generated runner `.conf`
- `generated_set` — SHA-256 of generated `.set` parameter file
- `readiness_config` — SHA-256 of readiness config YAML (when available)

## Important rules

1. **Digests are audit metadata only.** A matching digest between a compile run
   and a backtest readiness run indicates the same files were used. It does
   **not** authorize anything.

2. **Digest mismatch means rerun or investigate.** If the `generated_mq5` digest
   differs between compile and readiness evidence, the `.mq5` file changed
   between runs. The operator should rerun both checks or investigate the drift.

3. **The readiness review packet is not candidate evidence.** It must not be
   consumed by `generated_candidate.py`, `generated_baseline.py`,
   `generated_robustness.py`, `generated_final_holdout.py`, `queue.py`, or any
   lifecycle, production, or live-trading code.

4. **Evidence paths must be explicit.** The CLI requires explicit paths to
   evidence files. The packet module does not search directories automatically.

5. **No side effects.** The readiness review creates no `scope_approvals`,
   `lifecycle_transitions`, `experiments`, or any other registry records. It may
   write only the requested packet output file.

6. **No approval automation.** The readiness review does not call approval code,
   does not create approval records, and does not modify approval status.

## In the rehearsal context

The real-toolchain rehearsal (`implementation real-toolchain-rehearsal`)
calls the same `build_readiness_review_packet` function to produce the packet
from compile and backtest readiness evidence, without any lifecycle,
candidate, baseline, robustness, or production integration.

The rehearsal writes the packet to `readiness_review_packet.json` in the
designated output directory alongside the raw evidence files and summary.

## Error handling

| Condition | Behavior |
|-----------|----------|
| Compile evidence file missing | `ValueError` / CLI exit code 1 |
| Backtest readiness evidence file missing | `ValueError` / CLI exit code 1 |
| Compile evidence has wrong `mode` | `ValueError` / CLI exit code 1 |
| Readiness evidence has wrong `mode` | `ValueError` / CLI exit code 1 |
| `impl_request_id` mismatch (CLI vs evidence) | Warning in packet, packet still built |
| `strategy_id` mismatch (compile vs readiness) | Warning in packet, packet still built |
| `version` mismatch (compile vs readiness) | Warning in packet, packet still built |
| `--out` under `automated/strategies/` | `ValueError` / CLI exit code 1 |
| Output path is writable | Packet written to file |
| `--out` omitted | Packet printed to stdout |
