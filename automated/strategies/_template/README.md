# Strategy Template

Copy this folder when starting a new MT5 EA strategy.

The template already includes:

- one-symbol, one-timeframe execution
- risk sizing from fixed money or percent of starting balance
- RR-based order placement
- time filter inputs
- one-position-at-a-time guard
- EA CSV trade/equity logging

Fill in `BuildSignal()` with the strategy-specific rules.

Copy `research_log.md` into each new strategy folder and log every experiment immediately after running it.
