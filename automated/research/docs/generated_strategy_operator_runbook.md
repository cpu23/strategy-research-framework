# Generated Strategy Operator Runbook

## Workflow

1. **Generate hypothesis and spec**

   ```
   research generate-hypothesis --theme <theme> --symbol <symbol> --timeframe <tf>
   queue add strategy_spec_generation --strategy-id <id> --hypothesis-id <id>
   ```

2. **Materialize implementation**

   ```
   queue add implementation_materialization --strategy-id <id> --spec-path <path> --version <ver>
   ```

3. **Compile-check**

   For mock compile (queue default):

   ```
   queue add implementation_compile_check --strategy-id <id>
   ```

   For real MT5/Wine compile verification (operator CLI only):

   ```
   python3 -m automated.research.cli --db <path> implementation compile-check \
       <impl_request_id> --real-compile-config <path/to/config.yaml>
   ```

   `--mock` and `--real-compile-config` are mutually exclusive. Queue execution
   remains mock-only; real compile requires explicit operator choice on the CLI.

   Create a real compile config file following the schema in
   [real_compile_verification.md](real_compile_verification.md).

   Real compile verification does not authorize baseline, robustness, final
   holdout, lifecycle apply, production promotion, or live trading.

3.5 **Bounded real backtest readiness (operator CLI only)**

   After compile-check passes or produces `mock_checked`, the operator can
   verify that the MT5/Wine terminal can execute a bounded backtest using
   the generated sandbox strategy artifacts:

   ```
   python3 -m automated.research.cli --db <path> implementation backtest-readiness \
       <impl_request_id> --real-backtest-readiness-config <path/to/config.yaml>
   ```

   Create a real backtest readiness config file following the schema in
   [real_backtest_readiness.md](real_backtest_readiness.md).

   Real backtest readiness does **not** authorize:
   - baseline runs
   - robustness sweeps
   - final holdout experiments
   - lifecycle transitions
   - production promotion
   - copy to `automated/strategies/`
   - live trading
   - dataset/cost/symbol/timeframe mutation
   - runner replacement
   - validation threshold weakening

   The command is not available in the queue. It is an explicit operator
   action that produces readiness evidence only.

3.75 **Real-toolchain rehearsal (operator only)**

   After compile-check and backtest readiness, the operator can run the
   full real-toolchain sequence as a single evidence-only rehearsal:

   ```
   python3 -m automated.research.cli --db <path> implementation real-toolchain-rehearsal \
       <impl_request_id> \
       --real-compile-config <path/to/compile_config.yaml> \
       --real-backtest-readiness-config <path/to/readiness_config.yaml> \
       --out-dir <path/to/safe_output>
   ```

   This runs:
   1. Real compile verification
   2. Bounded real backtest readiness
   3. Readiness review packet creation

   It writes `compile_evidence.json`, `backtest_readiness_evidence.json`,
   `readiness_review_packet.json`, and `real_toolchain_rehearsal_summary.json`
   into `--out-dir`.

   The rehearsal is **evidence-only**. It does not create approvals,
   lifecycle transitions, experiments, baseline records, robustness records,
   final-holdout records, or candidate records. It does not authorise
   production promotion or live trading.

   If compile verification fails, the rehearsal stops before backtest
   readiness. If backtest readiness fails, the review packet is still
   created with the failed status visible.

   See [real_toolchain_rehearsal_runbook.md](real_toolchain_rehearsal_runbook.md).

4. **Diff-review**

   ```
   queue add implementation_review --strategy-id <id>
   ```

   **Warning: Do not run compile-check and diff-review in parallel.**
   Diff-review depends on the latest implementation/check state. Running them
   in parallel can leave the newest implementation record without
   input/spec-match status until diff-review is rerun.

5. **Review research packet**

   ```
   research review-packet --strategy-id <id>
   ```

   Read the packet. Decide whether to proceed.

6. **Approve baseline**

   ```
   research approve-for-baseline --strategy-id <id> --approved-by <name>
   ```

   This creates a single-use `baseline_only` approval in the registry.
   Baseline approval is implementation-record based and does not emit a
   separate `approval_id`.

7. **Run baseline**

   ```
   queue add generated_baseline_experiment --strategy-id <id> --approval-id <id>
   ```

8. **Review baseline**

   ```
   queue add generated_baseline_review --strategy-id <id>
   ```

   Read the review packet. Assess metrics.

9. **Run robustness**

   ```
   queue add generated_robustness_sweep --strategy-id <id>
   ```

10. **Review robustness**

    ```
    queue add generated_robustness_review --strategy-id <id>
    ```

    Read the review packet. Assess sweep results.

11. **Build candidate decision packet**

    ```
    queue add generated_candidate_decision_packet --strategy-id <id>
    ```

    The packet will contain `proposed_next_action`. If it is
    `request_human_review_for_final_holdout` with `lifecycle_proposal`
    `final_holdout_candidate`, proceed. If it is `reconsider` or `drop`,
    stop — do not approve final holdout.

12. **Approve final holdout** (only if packet recommends it)

    ```
    research approve-for-final-holdout --packet-path <path> --approved-by <name>
    ```

    Final holdout approval is packet-bound and emits an `approval_id`.
    The approval is bound to the packet's SHA-256 digest. If the packet
    changes after approval, the digest will not match and final holdout will
    be rejected.

13. **Run final holdout**

    ```
    queue add generated_final_holdout_experiment --strategy-id <id> --approval-id <id>
    ```

14. **Review final holdout**

    ```
    queue add generated_final_holdout_review --strategy-id <id>
    ```

    Read the review packet. Compare with baseline and robustness results.

15. **Rebuild or review decision packet**

    The decision packet is updated with final holdout results. Rebuild it
    if needed, or re-read for the final recommendation.

    ```
    queue add generated_candidate_decision_packet --strategy-id <id>
    ```

## Safety Notes

- **Approvals are single-use by default.** The `used` flag on
  `scope_approvals` prevents re-use. A new approval must be created for a
  second run.

- **Baseline approval and final holdout approval are distinct scopes.**
  Baseline approval is implementation-record based and does not emit a
  separate `approval_id`. Final holdout approval is packet-bound and emits
  an `approval_id`. A baseline approval must never satisfy final holdout
  approval, and final holdout approval must never satisfy baseline approval.

- **Final holdout approval is packet-bound by digest.** The SHA-256 digest
  of the candidate decision packet is recorded at approval time. At
  execution time the on-disk packet is re-hashed. If the digests differ,
  the execution is rejected. This prevents approving one packet and running
  a different one.

- **Queue permissions do not substitute for human approvals.**
  Setting `allow_final_holdout=true` in a queue item does not bypass the
  `scope_approvals` check. Human approval is independently verified at
  execution time.

- **No lifecycle or production action is automatic.** Decision packets
  contain string-only proposals (`proposed_next_action`). No lifecycle
  transition, no promotion, no live trading is ever applied by the system.

- **Production promotion is intentionally not implemented.**
  Generated strategies must be re-implemented manually in
  `automated/strategies/` if they are ever promoted to production. The
  sandbox boundary (`SANDBOX_ROOT` vs `automated/strategies/`) is
  enforced at compile check, diff review, and materialization time.
