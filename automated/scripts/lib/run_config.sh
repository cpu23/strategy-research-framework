#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

load_run_config() {
   if [[ $# -ne 1 || -z "${1:-}" ]]; then
      echo "usage: $0 automated/runs/<run>.conf" >&2
      exit 2
   fi

   RUN_CONFIG="$1"
   if [[ "$RUN_CONFIG" != /* ]]; then
      RUN_CONFIG="$ROOT_DIR/${RUN_CONFIG#automated/}"
   fi

   if [[ ! -f "$RUN_CONFIG" ]]; then
      echo "run config not found: $RUN_CONFIG" >&2
      exit 2
   fi

   set -a
   # shellcheck source=/dev/null
   source "$RUN_CONFIG"
   set +a

   : "${RUN_ID:?missing RUN_ID}"
   : "${STRATEGY_ID:?missing STRATEGY_ID}"
   : "${EA_NAME:?missing EA_NAME}"
   : "${EA_SOURCE:?missing EA_SOURCE}"
   : "${MT5_EXPERT:?missing MT5_EXPERT}"
   : "${SYMBOL:?missing SYMBOL}"
   : "${TIMEFRAME:?missing TIMEFRAME}"
   : "${DATE_FROM:?missing DATE_FROM}"
   : "${DATE_TO:?missing DATE_TO}"
   : "${DEPOSIT:?missing DEPOSIT}"
   : "${CURRENCY:?missing CURRENCY}"
   : "${LEVERAGE:?missing LEVERAGE}"

   WINE_PREFIX="${WINE_PREFIX:-$HOME/.mt5-oanda}"
   MT5_DIR="${MT5_DIR:-$WINE_PREFIX/drive_c/Program Files/MetaTrader 5}"
   MT5_LINK="${MT5_LINK:-/tmp/mt5oanda}"
   REPORT_DIR="$ROOT_DIR/reports/$RUN_ID"
   CONFIG_PATH="$ROOT_DIR/runs/$RUN_ID.ini"
   EA_SRC="$ROOT_DIR/$EA_SOURCE"
   EA_DST="$MT5_DIR/MQL5/Experts/Automated/$EA_NAME.mq5"
   EA_LOG="$MT5_LINK/MQL5/Experts/Automated/$EA_NAME.log"
   COMPILE_LOG="$REPORT_DIR/compile.log"
   RUN_LOG="$REPORT_DIR/terminal_run.log"
   MT5_PERIOD="$TIMEFRAME"
   MQL_PERIOD="PERIOD_$TIMEFRAME"
}

windows_path() {
   local path="$1"
   path="${path//\//\\}"
   printf 'Z:%s' "$path"
}

prepare_mt5_paths() {
   mkdir -p "$REPORT_DIR"
   mkdir -p "$MT5_DIR/MQL5/Experts/Automated"
   mkdir -p "$MT5_DIR/MQL5/Profiles/Tester"
   ln -sfn "$MT5_DIR" "$MT5_LINK"
}
