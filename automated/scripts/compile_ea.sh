#!/usr/bin/env bash
set -euo pipefail

if [[ "${MT5_USE_XVFB:-1}" != "0" && -z "${MT5_XVFB_ACTIVE:-}" ]]; then
   if command -v xvfb-run >/dev/null 2>&1; then
      export MT5_XVFB_ACTIVE=1
      exec xvfb-run -a "$0" "$@"
   else
      echo "warning: xvfb-run not found; MetaEditor/Wine may open visible windows. Set MT5_USE_XVFB=0 to silence this warning." >&2
   fi
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/run_config.sh
source "$SCRIPT_DIR/lib/run_config.sh"

load_run_config "${1:-}"
prepare_mt5_paths

install -D "$EA_SRC" "$EA_DST"

set +e
WINEPREFIX="$WINE_PREFIX" wine "$MT5_LINK/MetaEditor64.exe" \
   "/compile:$MT5_LINK/MQL5/Experts/Automated/$EA_NAME.mq5" \
   /log
WINE_EXIT=$?
set -e

if [[ -f "$EA_LOG" ]]; then
   iconv -f UTF-16LE -t UTF-8 "$EA_LOG" > "$COMPILE_LOG"
fi

tail -n 5 "$COMPILE_LOG"

if grep -q "Result: 0 errors, 0 warnings" "$COMPILE_LOG"; then
   exit 0
fi

echo "MetaEditor exited with code $WINE_EXIT and compile log did not show a clean build." >&2
exit 1
