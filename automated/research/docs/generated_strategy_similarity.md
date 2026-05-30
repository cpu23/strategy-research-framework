# Generated Strategy Similarity

## Purpose

Similarity reporting compares edge theses, generated hypotheses, strategy spec shapes, and future behavior fingerprints. It quantifies overlap so research campaigns can allow similarity while capping redundancy.

Similarity is advisory. It is not a pass/fail validation gate by itself.

## Why Similarity Is Allowed

Similar variants can show which expression of a market mechanism works best. For example, volatility compression breakouts may need several controlled expressions of compression, trigger, stop, and exit logic.

## Why Similarity Is Capped

Unlimited near-duplicates waste research budget and can crowd out other edge families. Diversity reports keep duplicates and near variants within explicit budgets.

## Scoring Model

Token normalization is deterministic and lowercases scalar, list, and mapping values. Weighted Jaccard scoring compares field-level token overlap.

Thesis similarity compares:

- edge family
- mechanism
- testable prediction
- mutation axes
- asset classes
- regimes
- failure modes

Hypothesis similarity compares:

- edge ID
- strategy family
- entry summary
- exit summary
- risk model
- filters
- symbols and timeframes
- mutation signature and axes

Spec/code-shape similarity is currently a deterministic placeholder over indicator lists, parameter names, entry/exit summaries, and risk model summaries. It does not deeply parse `.mq5`.

## Classification Model

Default thresholds:

```yaml
duplicate: 0.95
near_variant: 0.75
same_family_different_expression: 0.50
different_family: below 0.50
```

Thresholds are configurable in tests and reports.

Recommendations:

- `keep`
- `keep_with_cap`
- `deprioritize`
- `reject_duplicate`
- `manual_review`

## Diversity Budgets

Supported budgets:

```yaml
max_duplicates: 1
max_near_variants_per_cluster: 4
max_same_family_per_campaign: 20
min_distinct_edge_families: 3
```

Diversity output records kept, kept-with-cap, deprioritized, rejected-duplicate, and manual-review decisions with reasons.

## Behavior Similarity Future Extension

Behavior similarity is reserved for future trade-overlap, return-correlation, and drawdown-overlap comparisons. The current report includes a placeholder schema only.

## Non-Goals

- Not a pass/fail validation gate by itself.
- No approval authority.
- No lifecycle authority.
- No production authority.
- No live trading authority.
