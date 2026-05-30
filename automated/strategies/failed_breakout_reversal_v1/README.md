# Failed Breakout Reversal V1

Mechanical version of the failed breakout reversal idea for chop/range regimes.

First baseline run:

```bash
automated/scripts/run_backtest.sh automated/runs/fbr_xauusd_h4_baseline.conf
```

Diagnostics run with identical inputs but clearer exit labels:

```bash
automated/scripts/run_backtest.sh automated/runs/fbr_xauusd_h4_exit_diagnostics.conf
```

Current one-variable experiments:

```bash
automated/scripts/run_backtest.sh automated/runs/fbr_xauusd_h4_no_adx_filter.conf
automated/scripts/run_backtest.sh automated/runs/fbr_xauusd_h4_no_atr_percentile_filter.conf
automated/scripts/run_backtest.sh automated/runs/fbr_xauusd_h4_min_rr_1_0.conf
automated/scripts/run_backtest.sh automated/runs/fbr_xauusd_h4_min_break_0_05.conf
automated/scripts/run_backtest.sh automated/runs/fbr_xauusd_h4_min_break_0_10.conf
automated/scripts/run_backtest.sh automated/runs/fbr_xauusd_h4_min_break_0_15.conf
automated/scripts/run_backtest.sh automated/runs/fbr_xauusd_h4_min_break_0_20.conf
automated/scripts/run_backtest.sh automated/runs/fbr_xauusd_h4_boundary_touches_2.conf
```

Initial market/timeframe choice:

- Symbol: `XAUUSD`
- Timeframe: `H4`
- Date range: `2024.01.01` to `2025.12.31`
- Risk: `0.50%` of starting balance per trade

Development notes and experiment results live in `research_log.md`.
