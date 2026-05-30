# Dummy Breakout

This EA is not intended as an edge. It exists to prove the automation loop:

- compile MQL5
- run one backtest
- open positions with shared risk sizing
- close with fixed RR
- write machine-readable logs
- preserve the native MT5 report

The strategy uses a simple previous-bars breakout:

- long if price breaks the previous `LookbackBars` high
- short if price breaks the previous `LookbackBars` low
- stop on the opposite side of the range
- target at `RiskReward` times the stop distance
- one open position per symbol/magic
- optional hour window using broker server time
