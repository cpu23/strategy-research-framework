# Generated Research Campaign Meta-Analysis

## Purpose

Campaign meta-analysis aggregates research outcomes across hypotheses, edge families, assets, timeframes, similarity clusters, and validation stages. It helps future research focus on stronger mechanisms and weaker failure points.

It is advisory analysis only. It does not score anything for production and does not authorize state changes.

## Supported Inputs

Inputs may include dictionaries or JSON/YAML artifacts from:

- campaign plans
- generated hypothesis batches
- similarity reports
- validation reports
- generated baseline reviews
- generated robustness reviews
- generated final-holdout reviews
- candidate decision packets

Partial records are allowed. Missing fields are grouped as `unknown`.

## Grouping Dimensions

Reports group by:

- edge family
- edge ID
- source ID
- hypothesis ID
- similarity cluster ID
- symbol
- timeframe
- strategy family
- entry logic type
- exit logic type
- risk model type
- validation status
- robustness status
- final-holdout status

## Outcome Categories

Canonical outcomes:

- `not_started`
- `pre_baseline_rejected`
- `baseline_failed`
- `baseline_warned`
- `baseline_passed`
- `robustness_failed`
- `robustness_warned`
- `robustness_passed`
- `final_holdout_failed`
- `final_holdout_warned`
- `final_holdout_passed`
- `manual_review_required`
- `insufficient_evidence`

There are no production or live outcome categories.

## Failure-Mode Taxonomy

Failure modes include compile failure, diff-review failure, baseline failure, robustness instability, final-holdout failure, similarity redundancy, insufficient sample size, and unknown.

## Advisory Scoring

Research priority score is transparent:

```yaml
components:
  evidence_quality: 0.35
  cross_asset_consistency: 0.20
  timeframe_consistency: 0.15
  robustness_quality: 0.20
  diversity_value: 0.10
```

Recommendations are limited to research actions such as reject, revise thesis, revise mutation recipe, run more prebaseline research, request manual baseline review, request manual final-holdout review, or defer.

## Feedback Loop

Reports guide later source review and mutation planning by identifying which edge families show promise, which assets/timeframes express the mechanism, which variants fail repeatedly, and which similarity clusters are redundant.

## Non-Goals

- No production scoring.
- No automatic approval.
- No lifecycle apply.
- No live trading authority.
