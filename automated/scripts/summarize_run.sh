#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/run_config.sh
source "$SCRIPT_DIR/lib/run_config.sh"

load_run_config "${1:-}"

TRADES_CSV="$REPORT_DIR/trades.csv"
EQUITY_CSV="$REPORT_DIR/equity.csv"
SUMMARY_JSON="$REPORT_DIR/run_summary.json"

if [[ ! -f "$TRADES_CSV" || ! -f "$EQUITY_CSV" ]]; then
   echo "missing trades.csv or equity.csv in $REPORT_DIR" >&2
   exit 1
fi

awk -F'\t' -v strategy="$STRATEGY_ID" -v symbol="$SYMBOL" -v timeframe="$TIMEFRAME" '
   NR == FNR {
      if(FNR > 1) {
         profit = $12 + 0
         trades++
         closed_net += profit

         if(profit > 0) {
            wins++
            gross_profit += profit
            current_losing_streak = 0
         } else if(profit < 0) {
            losses++
            gross_loss += profit
            current_losing_streak++
            if(current_losing_streak > longest_losing_streak) {
               longest_losing_streak = current_losing_streak
            }
         }
      }
      next
   }
   FNR > 1 {
      balance = $2 + 0
      equity = $3 + 0
      if(first_equity == 0) {
         start_balance = balance
         peak = equity
         first_equity = 1
      }
      if(equity > peak) {
         peak = equity
      }
      drawdown = peak - equity
      if(drawdown > max_drawdown) {
         max_drawdown = drawdown
      }
      final_balance = balance
      final_equity = equity
   }
   END {
      win_rate = trades > 0 ? wins / trades * 100 : 0
      account_net = final_balance - start_balance
      end_adjustment = account_net - closed_net
      profit_factor = gross_loss < 0 ? gross_profit / -gross_loss : 0
      avg_win = wins > 0 ? gross_profit / wins : 0
      avg_loss = losses > 0 ? gross_loss / losses : 0
      expectancy = trades > 0 ? account_net / trades : 0
      closed_expectancy = trades > 0 ? closed_net / trades : 0
      max_drawdown_pct = start_balance > 0 ? max_drawdown / start_balance * 100 : 0
      return_over_drawdown = max_drawdown > 0 ? account_net / max_drawdown : 0

      printf "{\n"
      printf "  \"strategy_id\": \"%s\",\n", strategy
      printf "  \"symbol\": \"%s\",\n", symbol
      printf "  \"timeframe\": \"%s\",\n", timeframe
      printf "  \"trades\": %d,\n", trades
      printf "  \"wins\": %d,\n", wins
      printf "  \"losses\": %d,\n", losses
      printf "  \"win_rate_pct\": %.2f,\n", win_rate
      printf "  \"net_profit\": %.2f,\n", account_net
      printf "  \"closed_trade_net_profit\": %.2f,\n", closed_net
      printf "  \"end_of_test_adjustment\": %.2f,\n", end_adjustment
      printf "  \"gross_profit\": %.2f,\n", gross_profit
      printf "  \"gross_loss\": %.2f,\n", gross_loss
      printf "  \"profit_factor\": %.2f,\n", profit_factor
      printf "  \"avg_win\": %.2f,\n", avg_win
      printf "  \"avg_loss\": %.2f,\n", avg_loss
      printf "  \"expectancy\": %.2f,\n", expectancy
      printf "  \"closed_trade_expectancy\": %.2f,\n", closed_expectancy
      printf "  \"longest_losing_streak\": %d,\n", longest_losing_streak
      printf "  \"start_balance\": %.2f,\n", start_balance
      printf "  \"final_balance\": %.2f,\n", final_balance
      printf "  \"final_equity\": %.2f,\n", final_equity
      printf "  \"max_equity_drawdown\": %.2f,\n", max_drawdown
      printf "  \"max_equity_drawdown_pct\": %.2f,\n", max_drawdown_pct
      printf "  \"return_over_drawdown\": %.2f\n", return_over_drawdown
      printf "}\n"
   }
' "$TRADES_CSV" "$EQUITY_CSV" > "$SUMMARY_JSON"

cat "$SUMMARY_JSON"
