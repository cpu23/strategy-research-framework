# Generated Research Campaign Planner

## Purpose

The campaign planner turns accepted generated hypotheses into advisory plans across assets and timeframes. It asks where an edge family may express itself. It does not run baselines, robustness sweeps, final holdouts, lifecycle transitions, or live trading.

## Campaign Config Schema

```yaml
campaign_id: CAMP_VOL_EXPANSION_2026_05
edge_ids:
  - EDGE_VOL_COMPRESSION_BREAKOUT_001
hypothesis_batch_ids:
  - HBATCH_EDGE_VOL_001
asset_timeframe_grid:
  - symbol: XAUUSD
    timeframes: [M30, H1, H4]
controls:
  dataset_policy: frozen_registered_only
  cost_model_policy: frozen_from_strategy_spec
  validation_threshold_policy: frozen
  runner_policy: frozen
budgets:
  max_total_planned_specs: 60
  max_per_edge: 20
  max_per_asset_timeframe: 5
  max_per_similarity_cluster_per_asset_timeframe: 2
similarity_policy:
  allow_similarity: true
  max_per_cluster_total: 6
  max_per_cluster_per_asset_timeframe: 2
outputs:
  ranked_manual_review_queue: true
```

## Asset/Timeframe Matrix

The planner normalizes symbols consistently to uppercase and deduplicates duplicate configured pairs deterministically. It only creates planning targets from configured pairs that are compatible with each hypothesis candidate symbol and timeframe list.

## Dataset Policy

Allowed dataset policies:

- `frozen_registered_only`
- `explicit_dataset_map`
- `manual_resolution_required`

Rejected examples include `auto_download`, `infer_from_symbol`, and `mutate_after_results`. Dataset references must be explicit or policy-bound because the Research OS treats datasets as hashed metadata linked to experiments.

## Budget Policy

Budgets cap total planned specs, plans per edge, plans per asset/timeframe, and plans per similarity cluster per asset/timeframe. Overflow variants are reported as `budget_capped` with a reason.

## Similarity Policy

Similarity is allowed but bounded. `max_per_cluster_total` and `max_per_cluster_per_asset_timeframe` prevent one cluster from consuming the campaign while still allowing controlled near-variant exploration.

## Ranked Manual Review Queue

The ranked manual review queue is advisory only. Entries include hypothesis lineage, target symbol/timeframe, and similarity cluster. They do not contain approval fields or lifecycle proposals.

## Non-Goals

- No automatic baseline.
- No automatic final holdout.
- No lifecycle apply.
- No dataset mutation.
- No cost, threshold, or runner mutation.
- No production authority.
- No live trading authority.
