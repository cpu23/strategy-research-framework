# VWAP Reversion 1H

Mean-reversion strategy for balanced sessions where stretched moves away from VWAP rotate back.

Status: first mechanical baseline is implemented for `H1`/`H4`. The initial run uses OANDA `US500` on `H1` as an ES proxy with daily/session VWAP.

Use this run config first:

```bash
automated/scripts/run_backtest.sh automated/runs/vwap_us500_h1_baseline.conf
```

Implementation notes:

- `InpVwapAnchorMode=0` uses a daily/session VWAP anchored at `InpSessionStartHour`.
- `InpVwapAnchorMode=1` uses a weekly anchored VWAP for 24h markets or 4H charts.
- VWAP uses typical price weighted by MT5 tick volume.
- The adverse ATR rule is implemented as a close-based rule exit; position sizing uses the same ATR distance as a virtual risk distance rather than placing a hard intrabar stop.

Development notes live in `research_log.md`.
