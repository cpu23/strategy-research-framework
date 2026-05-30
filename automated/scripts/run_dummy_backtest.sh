#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$SCRIPT_DIR/run_backtest.sh" "$SCRIPT_DIR/../runs/dummy_xauusd_m5.conf"
