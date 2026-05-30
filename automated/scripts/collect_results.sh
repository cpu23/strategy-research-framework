#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/run_config.sh
source "$SCRIPT_DIR/lib/run_config.sh"

load_run_config "${1:-}"
prepare_mt5_paths

TRADES_SRC="$(find "$MT5_DIR/Tester" -path "*/MQL5/Files/${STRATEGY_ID}_${SYMBOL}_${MQL_PERIOD}_trades.csv" -type f -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"
EQUITY_SRC="$(find "$MT5_DIR/Tester" -path "*/MQL5/Files/${STRATEGY_ID}_${SYMBOL}_${MQL_PERIOD}_equity.csv" -type f -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"
BARS_SRC="$(find "$MT5_DIR/Tester" -path "*/MQL5/Files/${STRATEGY_ID}_${SYMBOL}_${MQL_PERIOD}_bars.csv" -type f -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"
AGENT_LOG_SRC="$(find "$MT5_DIR/Tester" -path "*/logs/*.log" -type f -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"

if [[ -n "$TRADES_SRC" ]]; then
   cp "$TRADES_SRC" "$REPORT_DIR/trades.csv"
fi

if [[ -n "$EQUITY_SRC" ]]; then
   cp "$EQUITY_SRC" "$REPORT_DIR/equity.csv"
fi

if [[ -n "$BARS_SRC" ]]; then
   cp "$BARS_SRC" "$REPORT_DIR/bars.csv"
fi

if [[ -n "$AGENT_LOG_SRC" ]]; then
   iconv -f UTF-16LE -t UTF-8 "$AGENT_LOG_SRC" > "$REPORT_DIR/tester_agent.log" || true
fi

find "$REPORT_DIR" -maxdepth 1 -type f -printf '%f\n' | sort
