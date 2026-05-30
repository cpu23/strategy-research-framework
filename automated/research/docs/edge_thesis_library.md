# Edge Thesis Library

## Purpose

The edge thesis library records research-backed market mechanism claims in a structured form. It supports source material such as academic papers, case studies, practitioner writeups, internal observations, post-trade reviews, and manual notes.

The library does not generate strategy code. It preserves traceable research inputs and extracts testable edge theses that later tools can use for bounded hypothesis mutation.

## Research Source Schema

Required fields:

```yaml
source_id: SRC_CASE_VOL_BREAKOUT_001
source_type: case_study
title: Volatility Compression Breakout Notes
authors: []
published_date: null
url_or_reference: Internal research note, 2026-05-15
summary: Low realized volatility may precede directional expansion.
markets_discussed: [metals, fx]
timeframes_discussed: [M30, H1, H4]
key_claims:
  - Compression can precede expansion when liquidity is coiled.
limitations:
  - False breakouts remain common in mean-reverting regimes.
extraction_status: pending
created_at: "2026-05-15T00:00:00Z"
```

Optional fields include `doi`, `venue`, `tags`, and `notes`.

`url_or_reference` may be a URL or plain citation text.

## Edge Thesis Schema

Required fields:

```yaml
edge_id: EDGE_VOL_COMPRESSION_BREAKOUT_001
source_ids: [SRC_CASE_VOL_BREAKOUT_001]
edge_family: volatility_expansion
mechanism: Volatility compression may precede expansion when liquidity is coiled.
testable_prediction: Breakouts after low realized volatility should have positive forward skew.
asset_classes: [metals, fx, equity_indices]
candidate_symbols: [XAUUSD, EURUSD, NAS100]
candidate_timeframes: [M30, H1, H4]
market_regimes: [range_compression]
mutation_axes: [compression_measure, breakout_trigger, exit_model, stop_model]
implementation_constraints:
  - Use closed-bar confirmation before baseline testing.
failure_modes:
  - False breakouts in mean-reverting regimes.
risk_warnings:
  - Spreads can widen around session transitions.
evidence_strength: medium
status: active
created_at: "2026-05-15T00:00:00Z"
```

`edge_family` is constrained to research families such as `trend`, `mean_reversion`, `volatility_expansion`, `liquidity`, `session`, `carry`, `cross_asset`, `news_drift`, `calendar`, `microstructure`, and `other`.

## Extraction Report

Extraction reports are advisory research-library artifacts:

```yaml
schema_version: edge_thesis_extraction_report_v1
artifact_type: edge_thesis_extraction_report
source_id: SRC_CASE_VOL_BREAKOUT_001
source_digest: <stable content digest>
extracted_edge_ids:
  - EDGE_VOL_COMPRESSION_BREAKOUT_001
edge_count: 1
warnings: []
authority:
  research_library_only: true
  may_generate_strategy_implementation: false
  baseline_decision_authority: false
  final_holdout_decision_authority: false
  state_transition_authority: false
  may_promote_to_live: false
```

The report intentionally has no `proposed_next_action`, `lifecycle_proposal`, approval field, or execution field.

## Later Mutation Flow

Edge theses can feed later hypothesis mutation by providing mechanism, prediction, candidate symbols, candidate timeframes, mutation axes, failure modes, and implementation constraints. Later phases may create traceable generated hypotheses, but that lineage remains research planning metadata until the existing manual review and approval path accepts downstream work.

## Non-Goals

- No implementation generation.
- No baseline approval.
- No final-holdout approval.
- No lifecycle apply.
- No production authority.
- No live trading authority.
