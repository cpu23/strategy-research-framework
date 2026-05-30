# Phase 15D: Queue Validation Audit

Generated: 2026-05-13
Scope: `queue.py` validation logic for all 14 generated task types

---

## 1. Task Validation Matrix

### Legend

| Field | Meaning |
|-------|---------|
| Required payload | Fields that must be non-empty in the queue item dict |
| Required permissions | `allow_*` that must be `true` |
| Forbidden permissions | `allow_*` that must be `false` (raises validation error) |
| Budget requirements | `max_*` constraints enforced at validation time |
| Runner execution | Whether `allow_runner_execution` is required, forbidden, or optional |
| Approval required | Whether a human approval record is needed (checked at execution time) |
| Lifecycle apply | Always forbidden for generated task types |
| Production writes | Always forbidden |
| Artifact creation | Whether the task produces new artifacts (reports, packets, etc.) |

### Row Per Task Type

| Task Type | Required Payload Fields | Required Permissions | Forbidden Permissions | Budget Requirements | Runner Exec | Approval | Lifecycle Apply | Prod Writes | Artifacts |
|-----------|------------------------|---------------------|-----------------------|---------------------|-------------|----------|-----------------|-------------|-----------|
| `implementation_request` | queue_id, priority, hypothesis_id, strategy_id, requested_by, created_at | (none) | (via FORBIDDEN defaults) | (none) | optional | no | forbidden | forbidden | request artifact |
| `implementation_compile_check` | queue_id, priority, hypothesis_id, strategy_id, requested_by, created_at | (none) | (via FORBIDDEN defaults) | (none) | optional | no | forbidden | forbidden | mq5 path |
| `implementation_review` | queue_id, priority, hypothesis_id, strategy_id, requested_by, created_at | (none) | (via FORBIDDEN defaults) | (none) | optional | no | forbidden | forbidden | diff review |
| `hypothesis_generation` | queue_id, priority, requested_by, created_at, research_theme, symbol, timeframe, market_regime, strategy_family | (none) | (via FORBIDDEN defaults) | (none) | optional | no | forbidden | forbidden | hypothesis files |
| `strategy_spec_generation` | queue_id, priority, requested_by, created_at, strategy_id, hypothesis_id | (none) | (via FORBIDDEN defaults) | (none) | optional | no | forbidden | forbidden | spec file |
| `implementation_materialization` | queue_id, priority, requested_by, created_at, strategy_id, generated_spec_path, strategy_version | (none) | (via FORBIDDEN defaults) | (none) | optional | no | forbidden | forbidden | impl request, sandbox files |
| `research_review_packet` | queue_id, priority, requested_by, created_at, strategy_id, strategy_version | (none) | (via FORBIDDEN defaults) | (none) | optional | no | forbidden | forbidden | review packet |
| `generated_baseline_experiment` | queue_id, priority, strategy_id, strategy_version, requested_by, created_at, hypothesis_id, dataset_id, implementation_request_id | `allow_runner_execution=true` | `allow_final_holdout=false`, `allow_lifecycle_apply=false` | `max_experiments >= 1` | required | approval usage at exec | forbidden | forbidden | experiment, usage record |
| `generated_baseline_review` | queue_id, priority, strategy_id, strategy_version, requested_by, created_at, parent_experiment_id | (none beyond defaults) | (none beyond defaults) | (none) | optional | no | forbidden | forbidden | review packet |
| `generated_robustness_sweep` | queue_id, priority, strategy_id, strategy_version, requested_by, created_at, implementation_request_id, baseline_experiment_id, hypothesis_id | `allow_runner_execution=true` | `allow_final_holdout=false`, `allow_lifecycle_apply=false` | `max_sweeps >= 1`, `max_child_experiments` 1-12 | required | eligibility at exec | forbidden | forbidden | sweep config, experiments |
| `generated_robustness_review` | queue_id, priority, strategy_id, strategy_version, requested_by, created_at, parent_experiment_id | (none beyond defaults) | (none beyond defaults) | (none) | optional | no | forbidden | forbidden | review packet |
| `generated_candidate_decision_packet` | queue_id, priority, strategy_id, strategy_version, requested_by, created_at | (none) | `allow_lifecycle_apply=false`, `allow_final_holdout=false`, `allow_runner_execution=false` | (none) | forbidden | no | forbidden | forbidden | decision packet |
| `generated_final_holdout_experiment` | queue_id, priority, strategy_id, strategy_version, requested_by, created_at, hypothesis_id, dataset_id, implementation_request_id, approval_id | `allow_final_holdout=true` | `allow_lifecycle_apply=false` | `max_experiments >= 1` | optional | scope approval at exec | forbidden | forbidden | experiment, usage record |
| `generated_final_holdout_review` | queue_id, priority, strategy_id, strategy_version, requested_by, created_at, parent_experiment_id | (none beyond defaults) | (none beyond defaults) | (none) | optional | no | forbidden | forbidden | review packet |

### Notes on Permissions

- All task types inherit `FORBIDDEN_PERMISSION_DEFAULTS`: `allow_mql5_edits`, `allow_dataset_changes`, `allow_validation_threshold_changes`, `allow_lifecycle_apply`, `allow_final_holdout` are all `false` by default.
- Setting any of these to `true` requires a valid `human_override` or generates a blocker (except `allow_lifecycle_apply` which is always blocked at queue level).
- `allow_runner_execution` is NOT in `FORBIDDEN_PERMISSION_DEFAULTS` — it is explicitly checked per block.
- `allow_lifecycle_propose` defaults to `true` but is irrelevant for generated task types (no lifecycle proposal is set on generated queue items).

---

## 2. Duplication Map

### 2.1 Required Field Checks (6 locations)

Every generated-task block builds a key list and iterates with `item.get(key) in (None, "")`:

| Block | Lines | Pattern |
|-------|-------|---------|
| INTAKE_TASK_TYPES | 317-319 | `for key in intake_keys: if item.get(key) in (None, ""): raise` |
| generated_baseline_* | 387-389 | `for key in gb_keys: if item.get(key) in (None, ""): raise` |
| generated_robustness_* | 456-458 | `for key in gr_keys: if item.get(key) in (None, ""): raise` |
| generated_candidate | 553-555 | `for key in gc_keys: if item.get(key) in (None, ""): raise` |
| generated_final_holdout_* | 618-620 | `for key in gfh_keys: if item.get(key) in (None, ""): raise` |
| legacy fallthrough | 678-680 | `for key in [...] if item.get(key) in (None, ""): raise` |

**Difference**: INTAKE block omits `for {task_type}` in error message; all others include it.

### 2.2 Permission Checks (5 blocks with per-type assertions)

| Block | Lines | Checks |
|-------|-------|--------|
| generated_baseline_experiment | 398-404 | `allow_final_holdout=false`, `allow_lifecycle_apply=false`, `allow_runner_execution=true` |
| generated_robustness_sweep | 467-472 | `allow_final_holdout=false`, `allow_lifecycle_apply=false`, `allow_runner_execution=true` |
| generated_candidate_decision_packet | 562-567 | `allow_lifecycle_apply=false`, `allow_final_holdout=false`, `allow_runner_execution=false` |
| generated_final_holdout_experiment | 628-633 | `allow_final_holdout=true`, `allow_lifecycle_apply=false` |

**Key insight**: Polarity differs per task type. Same pattern with different `must_be` values.

### 2.3 Common Tail (6 locations, ~20 lines each)

Each block ends with the same pattern:

```
roles validation
required_outputs validation
validated dict construction
status validation
registry upsert (if persist)
append to validated_items
```

The `sweep_config` key differs: `None` for all generated types except GR (uses real sweep_config).

### 2.4 Budget Checks

`_normalize_budget(item)` is called in every block.
`_enforce_precreation_budget` (line 772) is called only for legacy types, but also checks `generated_baseline_experiment` and `generated_robustness_sweep` which are already validated in their own blocks.

---

## 3. Risk Map

### R-1: Per-task permission polarity differs — must not be flattened

- GB: `allow_runner_execution=true` (required)
- GC: `allow_runner_execution=false` (forbidden)
- FH: `allow_final_holdout=true` (required)
- GB/GR/GC: `allow_final_holdout=false` (forbidden)

**Risk**: A common helper that accepts a single `must_be` boolean preserves polarity. But a future refactor that tries to infer polarity from a table could silently invert a check.

### R-2: Execution-time revalidation is defense-in-depth, not duplication

- `_execute_generated_baseline` re-checks `budget["max_experiments"] >= 1`
- `_execute_generated_final_holdout_experiment` re-checks approval existence and digest
- `_execute_generated_robustness_sweep` re-runs eligibility checks

**Risk**: Removing these as "duplication" would create a window where queue validation and execution disagree. These are safety nets.

### R-3: Approval digest mismatch checked at execution time only

Queue validation accepts `approval_id` as a required string field. It does NOT fetch the approval record or verify the digest. The digest comparison happens only in `_execute_generated_final_holdout_experiment`.

**Risk**: Adding digest verification to validation would be a behavior change (currently deferred to execution).

### R-4: Queue permissions must never substitute for human approval

`_human_override_is_valid` (line 208) checks the registry for a human-approved implementation task. This is the only way forbidden permissions can be enabled at queue time.

**Risk**: If this check is ever removed or weakened (e.g., allowing `human_override` with just a flag), an autonomous queue item could enable `allow_final_holdout`, `allow_mql5_edits`, etc. without actual human approval.

### R-5: `allow_lifecycle_apply` is checked twice

In `_normalize_permissions`:
1. In the `FORBIDDEN_PERMISSION_DEFAULTS` loop (line 247-249) — generates blocker unless human override
2. Explicit check at line 250-251 — always generates blocker

The first check theoretically allows a human override; the second unconditionally blocks. This double-coverage is intentional (belt-and-suspenders) but confusing.

### R-6: Budget constraints differ between generated and legacy types

Generated types check `_enforce_precreation_budget` differently:
- `generated_baseline_experiment`: checked inline at line ~632 (FH) and line ~398 (GB)
- `generated_robustness_sweep`: checked inline at lines 474-477
- Legacy types: checked in `_enforce_precreation_budget` at line 724

**Risk**: Moving all budget checks to a shared helper would need to preserve the different entry points.

---

## 4. Minimal Refactor Recommendations

### Extract Now (Phase 15D)

| Helper | Purpose | Risk |
|--------|---------|------|
| `_validate_required_fields(item, keys, task_type)` | Replace 6 repeated key-iteration blocks | None — pure mechanical extraction |
| `_validate_permission_requirement(permissions, key, must_be, task_type)` | Replace 12+ repeated boolean permission checks | None — callers supply polarity |

### Still Deferred

| Refactor | Reason |
|----------|--------|
| Common validation tail (roles/outputs/status/upsert) | Too many per-block differences in dict construction (`sweep_config` varies per block) |
| Full queue validation consolidation (RC-8) | Higher risk than justified — 6 blocks with subtle per-type differences in field sets, permission polarity, budget rules, and execution config |
| `_normalize_permissions` cleanup | Already self-contained; changing it risks authority model |
| `_execute_item` dispatch restructure | Structural change, not a helper extraction |

---

## Summary

- **14 generated task types** analyzed with per-type validation matrix
- **6 duplication sites** identified for required field checks
- **4 blocks** with per-type permission assertion patterns
- **6 risk items** documented with mitigation notes
- **2 small helpers** extracted in Phase 15D
- **Full queue consolidation (RC-8) remains deferred**
