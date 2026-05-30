# Real compile verification

## Scope

Real compile verification checks that generated sandbox `.mq5` implementations can be compiled through the configured MT5/Wine toolchain.

It does not authorize baseline, robustness, final holdout, production promotion, lifecycle apply, or live trading.

## Allowed

- read generated strategy spec
- read generated sandbox implementation
- call configured compiler
- collect compiler logs
- write compile-check evidence

## Not allowed

- write or copy into `automated/strategies/`
- replace the runner
- mutate dataset, cost, symbol, timeframe, or validation thresholds
- create lifecycle transitions
- propose production/live/promotion actions
- place trades

## Command pattern

Real compile verification is invoked through the implementation compile-check CLI:

```
python3 -m automated.research.cli \
    --db <registry.db> \
    implementation compile-check \
    <implementation_request_id> \
    --real-compile-config <path/to/config.yaml>
```

`--mock` and `--real-compile-config` are mutually exclusive. If both are provided, the command exits with an error.

Mock compile remains the default for queue execution and for tests.

## Required config fields

Create a YAML config file with these fields:

```yaml
mode: real_compile
wine_binary: /usr/bin/wine
wine_prefix: /home/user/wine              # optional, omit or set null if not needed
metaeditor_path: /path/to/metaeditor64.exe # optional, defaults to metaeditor64.exe in wine path
terminal_data_dir: /path/to/MT5/data       # optional
timeout_seconds: 120                       # optional, default 120
```

- `mode` must be `"real_compile"`.
- `wine_binary` is the Wine executable path. In tests this can be a fake compiler script.
- `wine_prefix`, `metaeditor_path`, and `terminal_data_dir` are optional; omit the key or set to `null` if not needed.
- `timeout_seconds` must be a positive integer.

## Expected compile-check evidence fields

After a real compile run, the compile-check result contains:

| Field | Description |
|-------|-------------|
| `mode` | Always `"real_compile"` |
| `status` | `"passed"` or `"failed"` |
| `exit_code` | Compiler process exit code, or `null` on timeout/config error |
| `stdout` | Compiler standard output text, if captured |
| `stderr` | Compiler standard error text, if captured |
| `compiler_log_path` | Path to compiler log file, if available |
| `started_at` | ISO-8601 timestamp of compile start |
| `finished_at` | ISO-8601 timestamp of compile finish |
| `duration_seconds` | Wall-clock duration of compile process |
| `errors` | List of error messages (empty on success) |
| `input_digests` | Object with SHA-256 hex digests of input files (see below) |

The `status` field maps directly to the implementation's `compile_status` — `"passed"` for success, `"failed"` for any failure.

### Input digests

When the compile-config path is available, `input_digests` contains:

| Key | Description |
|-----|-------------|
| `generated_mq5` | SHA-256 hex digest of the generated `.mq5` file |
| `compile_config` | SHA-256 hex digest of the real compile config YAML file (only present when config path is provided) |

**Rules:**
- Digests are audit metadata only.
- Digest match does not authorize baseline, robustness, final holdout, lifecycle, production, or live trading.
- Digest mismatch means rerun readiness verification or investigate input drift.
- Digests are computed using `hashing.file_sha256()` (SHA-256, 1 MB chunked reads).
- If the `.mq5` file is missing at check time, the digest is omitted from `input_digests`.

## Common failure modes

| Failure | Symptom | Resolution |
|---------|---------|------------|
| Missing config file | `FileNotFoundError: Real compile config not found` | Verify config path |
| Wrong mode | `ValueError: Expected mode 'real_compile', got ...` | Set `mode: real_compile` |
| Compiler binary not found | Compile evidence status `"failed"`, error contains "not found" | Check `wine_binary` path |
| Invalid Wine prefix | Compile evidence failure, stderr from Wine | Verify `wine_prefix` is a valid Wine environment |
| MetaEditor path missing | Compile evidence failure, non-zero exit code | Verify `metaeditor_path` |
| Compiler timeout | Evidence status `"failed"`, error: "Compile timed out" | Increase `timeout_seconds` or check toolchain health |
| MQL5 compile error | Compile evidence failure, non-zero exit code, stderr has MQL errors | Fix `.mq5` source; re-run after changes |
| Target path under `automated/strategies/` | Evidence status `"failed"`, "Path must be under" sandbox error | Only sandbox paths are allowed |
| Target path outside sandbox | Evidence status `"failed"`, sandbox path assertion error | Only `automated/generated_strategies/<id>/<version>/` paths are allowed |

## Recommended sequence

1. Confirm generated sandbox path.
2. Confirm declared runner config and `.set` files exist.
3. Create a real compile config YAML file.
4. Run compile-check with `--real-compile-config`.
5. Store compiler output as compile-check evidence.
6. Run diff-review after compile-check completes.
7. Produce or update the research review packet.
8. Stop for manual review.

## Related: real-toolchain rehearsal

The real-toolchain rehearsal (`implementation real-toolchain-rehearsal`)
automates this compile verification as the first step of a three-step
operator-controlled sequence that also runs backtest readiness and builds
a readiness review packet.

See [real_toolchain_rehearsal_runbook.md](real_toolchain_rehearsal_runbook.md).

## Safety reminder

Real compile verification does not authorize:

- baseline runs
- robustness sweeps
- final holdout experiments
- lifecycle transitions
- production promotion
- copy to `automated/strategies/`
- live trading

Compile failure is treated as compile-check evidence only. It does not approve anything.

Input digests are audit metadata only:
- Digest match does not authorize baseline, robustness, final holdout, lifecycle, production, or live trading.
- Digest mismatch is not an automatic blocker — it signals that input drift may have occurred and the operator should investigate before proceeding.
- The readiness review packet (see `readiness_review_packet.md`) aggregates digest evidence for operator review.
- Digests do not replace operator judgment or the existing approval process.
