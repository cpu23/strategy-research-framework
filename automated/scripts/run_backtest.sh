#!/usr/bin/env bash
set -euo pipefail

if [[ "${MT5_USE_XVFB:-1}" != "0" && -z "${MT5_XVFB_ACTIVE:-}" ]]; then
   if command -v xvfb-run >/dev/null 2>&1; then
      export MT5_XVFB_ACTIVE=1
      exec xvfb-run -a "$0" "$@"
   else
      echo "warning: xvfb-run not found; MT5/Wine may open visible windows. Set MT5_USE_XVFB=0 to silence this warning." >&2
   fi
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/run_config.sh
source "$SCRIPT_DIR/lib/run_config.sh"

load_run_config "${1:-}"
prepare_mt5_paths

"$SCRIPT_DIR/compile_ea.sh" "$RUN_CONFIG"
"$SCRIPT_DIR/make_tester_ini.sh" "$RUN_CONFIG" >/dev/null

MT5_LOCKFILE="${MT5_LOCKFILE:-/tmp/mt5oanda_backtest.lock}"

set +e
(
  flock -x 200
  WINEPREFIX="$WINE_PREFIX" wine "$MT5_LINK/terminal64.exe" "/config:$(windows_path "$CONFIG_PATH")" > "$RUN_LOG" 2>&1
) 200>"$MT5_LOCKFILE"
WINE_EXIT=$?
set -e

"$SCRIPT_DIR/collect_results.sh" "$RUN_CONFIG" >/dev/null

if [[ -f "$REPORT_DIR/trades.csv" && -f "$REPORT_DIR/equity.csv" ]]; then
   "$SCRIPT_DIR/summarize_run.sh" "$RUN_CONFIG" >/dev/null
fi

echo "terminal64 exited with code $WINE_EXIT"
echo "run log: $RUN_LOG"
echo "report dir: $REPORT_DIR"

find "$REPORT_DIR" -maxdepth 1 -type f -printf '%f\n' | sort

exit "$WINE_EXIT"
