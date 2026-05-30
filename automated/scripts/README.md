# Scripts

Current working scripts:

- `compile_ea.sh <run.conf>` - compiles the EA named by a run config.
- `make_tester_ini.sh <run.conf>` - writes the MT5 tester config.
- `run_backtest.sh <run.conf>` - compiles, runs MT5, collects CSV logs, and writes `run_summary.json`.
- `collect_results.sh <run.conf>` - copies EA output from the MT5 tester agent folder.
- `summarize_run.sh <run.conf>` - parses `trades.csv` and `equity.csv`.
- `plot_trade_examples.py <report_dir>` - writes simple SVG charts for a few example trades.
- `compile_dummy.sh` - compiles `DummyBreakout` with MetaEditor under Wine.
- `make_dummy_tester_ini.sh` - writes the MT5 tester config for the XAUUSD M5 dummy run.
- `run_dummy_backtest.sh` - wrapper around the generic runner.

Example:

```bash
automated/scripts/run_backtest.sh automated/runs/dummy_xauusd_m5.conf
```

By default, `compile_ea.sh` and `run_backtest.sh` run MetaEditor/MT5 under
`xvfb-run` when it is installed. This keeps Wine windows on a virtual display
instead of stealing focus on the desktop. To intentionally show the MT5 GUI for
debugging:

```bash
MT5_USE_XVFB=0 automated/scripts/run_backtest.sh automated/runs/dummy_xauusd_m5.conf
```

Still planned once the pattern is stable:

- Broader parameter sweep wrapper.

These are intentionally not overbuilt yet. The first job is to prove MT5 can run and backtest from this Linux machine.
