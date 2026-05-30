# Real-toolchain artifact taxonomy

This document defines the four artifact types produced by the real-toolchain
readiness workflow. These artifacts are **operator evidence only**. They
must not be consumed as baseline, robustness, final holdout, candidate
decision, lifecycle, production, live-trading, or approval evidence.

Canonical type strings for the four artifacts:

- `real_compile` (mode field in compile evidence)
- `real_backtest_readiness` (mode field in backtest readiness evidence)
- `generated_readiness_review` (artifact_type in readiness review packet)
- `real_toolchain_rehearsal_summary` (artifact_type in rehearsal summary)

---

## 1. `real_compile` evidence

**Producer:** `compiler.run_real_compile()` called via:
- `implementation compile-check --real-compile-config`
- `implementation real-toolchain-rehearsal` (as step 1)

**Required inputs:**
- Generated sandbox `.mq5` file (under `automated/generated_strategies/<id>/<version>/`)
- Real compile config YAML with `mode: real_compile`

**Expected output file (normalised form in rehearsal):** `compile_evidence.json`

**Registry persistence:** Not persisted in registry. The compile result
`compile_status` is stored on the `implementations` record, but the raw
evidence JSON is not attached as a registry artifact. The artifact type
string `"real_compile"` is **not** listed in `contracts.ARTIFACT_TYPES`.

**Allowed use:**
- Operator review of compile status, errors, and input digests
- Input to `build_readiness_review_packet()` for the readiness review packet
- Input to rehearsal summary via `_normalize_compile_evidence()`

**Forbidden use:**
- Must not satisfy baseline evidence requirements
- Must not satisfy robustness evidence requirements
- Must not satisfy final holdout evidence requirements
- Must not satisfy candidate decision evidence requirements
- Must not satisfy lifecycle evidence requirements
- Must not satisfy production evidence requirements
- Must not satisfy live trading evidence requirements
- Must not satisfy approval evidence
- Must not be attached to experiments via registry (rejected by
  `_validate_artifact_type` since not in `contracts.ARTIFACT_TYPES`)

---

## 2. `real_backtest_readiness` evidence

**Producer:** `backtest_readiness.run_real_backtest_readiness()` called via:
- `implementation backtest-readiness --real-backtest-readiness-config`
- `implementation real-toolchain-rehearsal` (as step 2)

**Required inputs:**
- Generated sandbox `.mq5` file
- Generated runner `.conf` file with `BROKER="mock"`
- Generated `.set` parameter file
- Real backtest readiness config YAML with `mode: real_backtest_readiness`

**Expected output file (in rehearsal):** `backtest_readiness_evidence.json`

**Registry persistence:** Not persisted in registry. The evidence JSON is
written to disk only. `"real_backtest_readiness"` is **not** listed in
`contracts.ARTIFACT_TYPES`.

**Allowed use:**
- Operator review of runner exit, evidence collection, and input digests
- Input to `build_readiness_review_packet()` for the readiness review packet
- Input to rehearsal summary

**Forbidden use:**
- Must not satisfy baseline evidence (`generated_baseline_review`)
- Must not satisfy final holdout evidence (`generated_final_holdout_review`)
- Must not satisfy candidate decision evidence (`generated_candidate_decision_packet`)
- Must not satisfy lifecycle, production, or live trading evidence
- Must not satisfy approval evidence
- Must not be attached to experiments via registry

---

## 3. `generated_readiness_review` packet

**Producer:** `readiness_review.build_readiness_review_packet()` called via:
- `implementation readiness-review`
- `implementation real-toolchain-rehearsal` (as step 3)

**Required inputs:**
- `real_compile` evidence JSON (mode `"real_compile"`)
- `real_backtest_readiness` evidence JSON (mode `"real_backtest_readiness"`)
- Optional: `impl_request_id`, `strategy_id`, `version`

**Expected output file (in rehearsal):** `readiness_review_packet.json`

**Field:**
- `artifact_type`: `"generated_readiness_review"`
- `schema_version`: `"generated_readiness_review_v1"`
- `proposed_next_manual_action`: one of `manual_review_only`, `defer`,
  `revise_environment`, `revise_generated_artifacts`
- Does **not** contain `proposed_next_action` or `lifecycle_proposal`

**Registry persistence:** Not persisted in registry. `"generated_readiness_review"`
is **not** listed in `contracts.ARTIFACT_TYPES`.

**Allowed use:**
- Operator review of combined readiness state
- Input to rehearsal summary

**Forbidden use:**
- Must not be consumed as `generated_candidate_decision_packet`
- Must not be consumed as `generated_baseline_review`
- Must not be consumed as `generated_robustness_review`
- Must not be consumed as `generated_final_holdout_review`
- Must not satisfy lifecycle, production, or live trading evidence
- Must not satisfy approval evidence
- Must not be passed to `generated_candidate.py`, `generated_baseline.py`,
  `generated_robustness.py`, `generated_final_holdout.py`, `queue.py`, or
  `lifecycle.py` as a decision or review artifact
- Must not be attached to experiments via registry

---

## 4. `real_toolchain_rehearsal_summary`

**Producer:** `toolchain_rehearsal.run_toolchain_rehearsal()` called via:
- `implementation real-toolchain-rehearsal`

**Required inputs:**
- Implementation request ID
- Real compile config path
- Real backtest readiness config path
- Output directory path (must not be under `automated/strategies/`)

Production internally consumes:
- Compile evidence (from compiler pipeline)
- Backtest readiness evidence (from backtest readiness pipeline)
- Readiness review packet (from readiness review pipeline)

**Expected output file:** `real_toolchain_rehearsal_summary.json`

**Fields:**
- `artifact_type`: `"real_toolchain_rehearsal_summary"`
- `status`, `compile_status`, `backtest_readiness_status`
- `output_paths`, `warnings`, `forbidden_interpretations`
- Does **not** contain `proposed_next_action` or `lifecycle_proposal`

**Registry persistence:** Not persisted in registry. `"real_toolchain_rehearsal_summary"`
is **not** listed in `contracts.ARTIFACT_TYPES`.

**Allowed use:**
- Operator review of the full rehearsal outcome
- Source of evidence paths for manual inspection

**Forbidden use:**
- Must not be consumed as `generated_candidate_decision_packet`
- Must not satisfy baseline, robustness, final holdout evidence
- Must not satisfy lifecycle, production, or live trading evidence
- Must not satisfy approval evidence
- Must not be passed to `generated_candidate.py` or any other
  authority-bearing module as a decision input
- Must not be attached to experiments via registry

---

## Exclusion from `contracts.ARTIFACT_TYPES`

The `contracts.ARTIFACT_TYPES` union in `automated/research/contracts.py`
defines all artifact types that can be attached to registry experiments.
None of the four readiness/rehearsal types are present in any of the
sub-sets that compose `ARTIFACT_TYPES`. This is an intentional boundary:

- Registry `_validate_artifact_type()` rejects attachment of readiness
  evidence, readiness review packets, and rehearsal summaries
- `load_review_artifact()` cannot return a readiness artifact when
  queried for `generated_baseline_review`, `generated_robustness_review`,
  `generated_final_holdout_review`, or `generated_candidate_decision_packet`
- No authority-bearing module (`queue.py`, `generated_candidate.py`,
  `generated_baseline.py`, `generated_robustness.py`,
  `generated_final_holdout.py`, `lifecycle.py`) imports or references
  `readiness_review`, `backtest_readiness`, or `toolchain_rehearsal`

These four artifact types exist solely as filesystem evidence for operator
review. They are not first-class registry objects and cannot drive automated
decisions.
