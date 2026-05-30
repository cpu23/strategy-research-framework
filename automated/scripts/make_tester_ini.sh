#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/run_config.sh
source "$SCRIPT_DIR/lib/run_config.sh"

load_run_config "${1:-}"
prepare_mt5_paths

REPORT_PATH="$(windows_path "$REPORT_DIR/mt5_report.htm")"
EXPERT_PARAMETERS_LINE=""

if [[ -n "${EA_SET_FILE:-}" ]]; then
   if [[ "$EA_SET_FILE" == /* ]]; then
       SET_SRC="$EA_SET_FILE"
       EA_SET_BASENAME="$(basename "$EA_SET_FILE")"
   else
       SET_SRC="$ROOT_DIR/runs/sets/$EA_SET_FILE"
       EA_SET_BASENAME="$EA_SET_FILE"
   fi
   SET_DST="$MT5_DIR/MQL5/Profiles/Tester/$EA_SET_BASENAME"
   if [[ ! -f "$SET_SRC" ]]; then
      echo "EA set file not found: $SET_SRC" >&2
      exit 2
   fi
   cp "$SET_SRC" "$SET_DST"
   EXPERT_PARAMETERS_LINE="ExpertParameters=$EA_SET_BASENAME"
fi

cat > "$CONFIG_PATH" <<EOF
[Common]
ProxyEnable=0
NewsEnable=0
CertInstall=1

[Experts]
AllowLiveTrading=0
AllowDllImport=0
Enabled=1
Account=0
Profile=0

[Tester]
Expert=$MT5_EXPERT
$EXPERT_PARAMETERS_LINE
Symbol=$SYMBOL
Period=$MT5_PERIOD
Deposit=$DEPOSIT
Currency=$CURRENCY
Leverage=$LEVERAGE
Model=${MODEL:-1}
ExecutionMode=${EXECUTION_MODE:-0}
Optimization=${OPTIMIZATION:-0}
FromDate=$DATE_FROM
ToDate=$DATE_TO
ForwardMode=${FORWARD_MODE:-0}
Report=$REPORT_PATH
ReplaceReport=1
ShutdownTerminal=1
Visual=${VISUAL:-0}
UseLocal=${USE_LOCAL:-1}
UseRemote=${USE_REMOTE:-0}
UseCloud=${USE_CLOUD:-0}
EOF

echo "$CONFIG_PATH"
