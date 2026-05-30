# Hypothesis Mutation Engine

## Purpose

The hypothesis mutation engine turns an edge thesis into many traceable strategy hypotheses. It does not create strategy implementations, write `.mq5` files, schedule runs, or approve anything. Its job is to make bounded, lineage-preserving research ideas for manual review and later Research OS paths.

Lineage:

```text
research source -> edge thesis -> mutation recipe -> generated hypothesis -> screening report
```

## Mutation Recipe Schema

```yaml
recipe_id: MUT_VOL_BREAKOUT_001
edge_id: EDGE_VOL_COMPRESSION_BREAKOUT_001
mutation_budget:
  max_hypotheses: 20
  max_per_similarity_cluster: 4
  min_family_diversity: 3
axes:
  compression_measure:
    allowed_values: [atr_percentile, bollinger_width, donchian_range]
  breakout_trigger:
    allowed_values: [close_break, intrabar_break, two_bar_confirm]
  trend_filter:
    allowed_values: [none, ma_slope, higher_timeframe_bias]
  exit_model:
    allowed_values: [fixed_r, atr_trail, time_stop]
  stop_model:
    allowed_values: [range_opposite_side, atr_multiple, structure_swing]
constraints:
  - one_primary_entry_trigger
  - one_primary_exit_model
  - no_unbounded_grid_search
```

Budgets are required. `max_hypotheses` bounds expansion and `max_per_similarity_cluster` allows similar variants while preventing clone spam.

## Generated Hypothesis Batch

```yaml
artifact_type: generated_hypothesis_batch
edge_id: EDGE_VOL_COMPRESSION_BREAKOUT_001
recipe_id: MUT_VOL_BREAKOUT_001
hypotheses:
  - hypothesis_id: HYP_GEN_...
    lineage:
      source_ids: [SRC_CASE_VOL_BREAKOUT_001]
      edge_id: EDGE_VOL_COMPRESSION_BREAKOUT_001
      recipe_id: MUT_VOL_BREAKOUT_001
      mutation_signature: ...
    strategy_family: volatility_expansion
    candidate_symbols: [XAUUSD, EURUSD]
    candidate_timeframes: [M30, H1, H4]
    entry_logic_summary: compression_measure=atr_percentile; breakout_trigger=close_break
    exit_logic_summary: exit_model=fixed_r
    risk_model_summary: stop_model=atr_multiple
    invalidation_rule: Reject if bounded research tests do not support the thesis prediction.
    similarity_cluster_id: SIM_001
    cluster_rank: 1
screening_summary:
  accepted: 12
  rejected: 0
  capped_by_similarity_budget: 5
```

Candidate symbols and timeframes are inherited from the edge thesis unless a later planning phase explicitly narrows them.

## Similarity Budget

Similarity is allowed because close variants can identify which expression of an edge works best. It is capped because unlimited near-duplicates waste research budget.

The initial scorer is deterministic and transparent. It compares edge family, symbols, timeframes, entry summary, exit summary, risk model, and mutation axes. Identical hypotheses score `1.0`; unrelated families score low; hypotheses from the same edge with different exit or risk logic land in the middle.

Cluster IDs are deterministic (`SIM_001`, `SIM_002`, ...). Each cluster receives `cluster_rank` so later campaign planners can intentionally test up to a fixed number of similar variants.

## Screening Report

Screening reports list accepted hypotheses, rejected hypotheses, capped-by-similarity hypotheses, warning reasons, and lineage summary. Capped variants remain visible; they are not silently deleted.

## Non-Goals

- No code generation.
- No strategy implementation materialization.
- No baseline approval.
- No final holdout approval.
- No lifecycle apply.
- No production authority.
- No live trading authority.
