# Real backtest readiness verification

## Scope

Real backtest readiness verification checks that the configured MT5/Wine terminal
can execute a tiny bounded backtest using existing generated sandbox strategy
artifacts and produce evidence logs/reports.

It does **not** authorize baseline, robustness, final holdout, production
promotion, lifecycle apply, or live trading.

## Allowed

- read generated strategy spec
- read generated sandbox implementation
- read generated runner `.conf` and `.set` files
- call the existing runner (`automated/scripts/run_backtest.sh`)
- collect runner logs and reports
- write readiness evidence to an explicit output directory
- emit JSON readiness evidence to stdout

## Not allowed

- write or copy into `automated/strategies/`
- replace the runner
- mutate dataset, cost, symbol, timeframe, or validation thresholds
- create lifecycle transitions
- propose production/live/promotion actions
- place trades
- generate baseline, robustness, final holdout, or candidate evidence
- perform compile within the readiness command (compile must precede readiness)

## Command pattern

```bash
python3 -m automated.research.cli \
    --db <registry.db> \
    implementation backtest-readiness \
    <implementation_request_id> \
    --real-backtest-readiness-config <path/to/config.yaml>
```

Optional overrides:

```bash
python3 -m automated.research.cli \
    --db <registry.db> \
    implementation backtest-readiness \
    <implementation_request_id> \
    --real-backtest-readiness-config <path/to/config.yaml> \
    --runner-conf-path <path/to/override.conf> \
    --set-file-path <path/to/override.set>
```

The config is mandatory. The command does **not** support queue execution and
is **not** called automatically from baseline, robustness, final holdout, or
candidate code.

## Required config fields

Create a YAML config file:

```yaml
mode: real_backtest_readiness
wine_binary: wine
wine_prefix: /home/user/wine              # optional, omit or set null if not needed
terminal_path: /path/to/terminal64.exe    # optional
terminal_data_dir: /path/to/MT5/data      # optional
timeout_seconds: 180
max_duration_seconds: 180
expected_symbol: XAUUSD
expected_timeframe: H4
expected_dataset_id: DATA_XAUUSD_H4_0DB89C617104  # optional
runner_conf_path: /path/to/generated.conf
set_file_path: /path/to/generated.set
output_dir: /path/to/readiness-output
```

- `mode` must be `"real_backtest_readiness"`.
- `timeout_seconds` and `max_duration_seconds` must be positive integers ≤ 600.
- `runner_conf_path` must point to an existing generated `.conf` file.
- `set_file_path` must point to an existing generated `.set` file.
- `output_dir` must not be under `automated/strategies/`.
- `wine_binary`, `wine_prefix`, `terminal_path`, `terminal_data_dir` are
  optional; omit the key or set to `null` if not needed.

## Expected readiness evidence fields

After running the readiness verification, the command emits JSON to stdout:

| Field | Description |
|-------|-------------|
| `mode` | Always `"real_backtest_readiness"` |
| `status` | `"passed"`, `"failed"`, or `"timed_out"` |
| `implementation_id` | Implementation record ID |
| `impl_request_id` | Implementation request ID |
| `strategy_id` | Strategy identifier |
| `version` | Strategy version |
| `runner_conf_path` | Resolved path to generated `.conf` file |
| `set_file_path` | Resolved path to generated `.set` file |
| `command_display` | The command executed (redacted for display) |
| `exit_code` | Runner process exit code, or `null` on timeout/config error |
| `stdout` | Runner standard output text, if captured |
| `stderr` | Runner standard error text, if captured |
| `report_paths` | List of collected report file paths |
| `log_paths` | List of collected log file paths |
| `started_at` | ISO-8601 timestamp of readiness start |
| `finished_at` | ISO-8601 timestamp of readiness finish |
| `duration_seconds` | Wall-clock duration of the runner process |
| `timeout_seconds` | Configured timeout value |
| `errors` | List of error messages (empty on success) |
| `input_digests` | Object with SHA-256 hex digests of input files (see below) |

The `status` field is one of:
- `"passed"` — runner exited 0, evidence was collected
- `"failed"` — runner exited non-zero, or a safety validation failed
- `"timed_out"` — runner exceeded `timeout_seconds`

### Input digests

`input_digests` contains SHA-256 digests for the input files used during the readiness run:

| Key | Description |
|-----|-------------|
| `generated_mq5` | SHA-256 hex digest of the generated `.mq5` file |
| `generated_conf` | SHA-256 hex digest of the generated runner `.conf` file |
| `generated_set` | SHA-256 hex digest of the generated `.set` parameter file |
| `readiness_config` | SHA-256 hex digest of the readiness config YAML file (only present when config path is provided) |

**Rules:**
- Digests are audit metadata only.
- Digest match does not authorize baseline, robustness, final holdout, lifecycle, production, or live trading.
- Digest mismatch means rerun readiness verification or investigate input drift.
- Digests are computed using `hashing.file_sha256()` (SHA-256, 1 MB chunked reads).
- If any input file is missing at check time, its key is omitted from `input_digests`.

## Path safety checks

Before execution, the following path safety checks are enforced:

1. Generated `.mq5` path must be under `automated/generated_strategies/<id>/<version>/`
2. Generated `.mq5` path must not be under `automated/strategies/`
3. Runner `.conf` `EA_SOURCE` must point to a sandbox `.mq5` file
4. Runner `.conf` `EA_SOURCE` must not be under `automated/strategies/`
5. Runner `.conf` `EA_SET_FILE` must match the provided `set_file_path`
6. Runner `.conf` `BROKER` must be `"mock"`
7. Runner `.conf` `SYMBOL` must match `expected_symbol`
8. Runner `.conf` `TIMEFRAME` must match `expected_timeframe`
9. Output directory must not be under `automated/strategies/`
10. Implementation request must have a prior compile-check with status
    `"mock_checked"` or `"passed"`

## Common failure modes

| Failure | Symptom | Resolution |
|---------|---------|------------|
| Missing config file | `FileNotFoundError: Real backtest readiness config not found` | Verify config path |
| Wrong mode | `ValueError: Expected mode 'real_backtest_readiness', got ...` | Set `mode: real_backtest_readiness` |
| Wine binary not found | Readiness evidence status `"failed"`, error contains "not found" | Check `wine_binary` path |
| Invalid Wine prefix | Readiness evidence failure, stderr from Wine | Verify `wine_prefix` is a valid Wine environment |
| Terminal path missing | Readiness evidence failure, non-zero exit code | Verify `terminal_path` |
| Runner timeout | Evidence status `"timed_out"` | Increase `timeout_seconds` or check toolchain health |
| Missing generated runner config | Evidence status `"failed"`, "Runner config not found" | Verify `runner_conf_path` |
| Missing `.set` file | Evidence status `"failed"`, "Set file not found" | Verify `set_file_path` |
| Path under `automated/strategies/` | Evidence status `"failed"`, path validation error | Only sandbox paths are allowed |
| Path outside sandbox | Evidence status `"failed"`, sandbox assertion error | Only `automated/generated_strategies/<id>/<version>/` paths are allowed |
| MT5 backtest failure | Evidence status `"failed"`, runner exited non-zero | Check `stdout`/`stderr` for terminal errors |
| Missing compile-check | Evidence status `"failed"`, "run compile-check first" | Run `implementation compile-check` first |
| Broker not mock | Evidence status `"failed"`, BROKER validation error | Generated `.conf` must set `BROKER="mock"` |
| Symbol mismatch | Evidence status `"failed"`, SYMBOL validation error | Match `.conf` SYMBOL to `expected_symbol` |
| Timeframe mismatch | Evidence status `"failed"`, TIMEFRAME validation error | Match `.conf` TIMEFRAME to `expected_timeframe` |

## Related: real-toolchain rehearsal

The real-toolchain rehearsal (`implementation real-toolchain-rehearsal`)
automates this backtest readiness verification as the second step of a
three-step operator-controlled sequence. It runs after compile verification
and before building the readiness review packet.

See [real_toolchain_rehearsal_runbook.md](real_toolchain_rehearsal_runbook.md).

## Safety reminder

Real backtest readiness verification does **not** authorize:

- baseline runs
- robustness sweeps
- final holdout experiments
- lifecycle transitions
- production promotion
- copy to `automated/strategies/`
- live trading
- dataset mutation
- cost mutation
- symbol mutation
- timeframe mutation
- runner replacement
- validation threshold weakening

Readiness failure is treated as readiness evidence only. It does not approve
or disapprove anything beyond the bounded backtest execution check.

Input digests are audit metadata only:
- Digest match does not authorize baseline, robustness, final holdout, lifecycle, production, or live trading.
- Digest mismatch is not an automatic blocker — it signals that input drift may have occurred and the operator should investigate before proceeding.
- The readiness review packet (see `readiness_review_packet.md`) aggregates digest evidence for operator review.
- Digests do not replace operator judgment or the existing approval process.
