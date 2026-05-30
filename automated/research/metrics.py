from __future__ import annotations

import csv
import json
import math
from datetime import datetime
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any


def metric(value: Any, availability: str = "available", reason: str | None = None) -> dict[str, Any]:
    return {"value": value, "availability": availability, "reason": reason}


def unavailable(reason: str) -> dict[str, Any]:
    return metric(None, "unavailable", reason)


def _float(value: str | int | float | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value.replace("+00:00", ""), fmt)
        except ValueError:
            continue
    return None


def read_trades(path: str | Path | None) -> tuple[list[dict[str, str]], str | None]:
    if not path:
        return [], "trade_log artifact is missing"
    trade_path = Path(path)
    if not trade_path.is_file():
        return [], f"trade log not found: {trade_path}"
    with trade_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader), None


def read_equity(path: str | Path | None) -> tuple[list[dict[str, str]], str | None]:
    if not path:
        return [], "equity_curve artifact is missing"
    equity_path = Path(path)
    if not equity_path.is_file():
        return [], f"equity curve not found: {equity_path}"
    with equity_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader), None


def read_summary(path: str | Path | None) -> tuple[dict[str, Any], str | None]:
    if not path:
        return {}, "metrics_json artifact is missing"
    summary_path = Path(path)
    if not summary_path.is_file():
        return {}, f"summary metrics not found: {summary_path}"
    with summary_path.open("r", encoding="utf-8") as handle:
        return json.load(handle), None


def consecutive_losses(profits: list[float]) -> int:
    longest = 0
    current = 0
    for profit in profits:
        if profit < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def compute_sharpe_from_equity(rows: list[dict[str, str]]) -> dict[str, Any]:
    equities = [_float(row.get("equity")) for row in rows]
    clean = [value for value in equities if value is not None and value > 0]
    if len(clean) < 3:
        return unavailable("equity curve has fewer than 3 valid samples")
    returns = []
    for previous, current in zip(clean, clean[1:]):
        if previous <= 0:
            continue
        returns.append((current - previous) / previous)
    if len(returns) < 2:
        return unavailable("equity curve has too few return intervals")
    deviation = pstdev(returns)
    if deviation == 0:
        return unavailable("equity return standard deviation is zero")
    # Bar/tester samples are not guaranteed to be daily. Keep this unannualized.
    return metric(mean(returns) / deviation, reason="unannualized Sharpe from exported equity samples")


def extract_metrics(
    *,
    trade_log_path: str | Path | None = None,
    equity_curve_path: str | Path | None = None,
    summary_path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    trades, trades_error = read_trades(trade_log_path)
    equity, equity_error = read_equity(equity_curve_path)
    summary, summary_error = read_summary(summary_path)

    metrics: dict[str, dict[str, Any]] = {}
    profits: list[float] = []
    directions: list[str] = []
    entry_times: list[datetime] = []
    exit_times: list[datetime] = []
    if not trades_error:
        for row in trades:
            profit = _float(row.get("profit"))
            if profit is not None:
                profits.append(profit)
            direction = (row.get("direction") or "").lower()
            if direction:
                directions.append(direction)
            entry_time = _parse_time(row.get("entry_time"))
            exit_time = _parse_time(row.get("exit_time"))
            if entry_time:
                entry_times.append(entry_time)
            if exit_time:
                exit_times.append(exit_time)

    if summary:
        start_balance = _float(summary.get("start_balance"))
        net_profit = _float(summary.get("net_profit"))
        if start_balance and net_profit is not None:
            metrics["net_return"] = metric(net_profit / start_balance)
        else:
            metrics["net_return"] = unavailable("run_summary.json lacks start_balance or net_profit")
        for name, key in [
            ("gross_profit", "gross_profit"),
            ("gross_loss", "gross_loss"),
            ("profit_factor", "profit_factor"),
            ("total_trades", "trades"),
            ("win_rate", "win_rate_pct"),
            ("average_trade", "expectancy"),
            ("max_drawdown", "max_equity_drawdown_pct"),
            ("consecutive_losses", "longest_losing_streak"),
        ]:
            value = summary.get(key)
            metrics[name] = metric(value) if value is not None else unavailable(f"run_summary.json lacks {key}")
    else:
        for name in [
            "net_return",
            "gross_profit",
            "gross_loss",
            "profit_factor",
            "total_trades",
            "win_rate",
            "average_trade",
            "max_drawdown",
            "consecutive_losses",
        ]:
            metrics[name] = unavailable(summary_error or "metrics_json artifact unavailable")

    metrics["sharpe"] = compute_sharpe_from_equity(equity) if not equity_error else unavailable(equity_error)

    if profits:
        if metrics.get("total_trades", {}).get("availability") != "available":
            metrics["total_trades"] = metric(len(trades), reason="computed from trade log row count")
        if metrics.get("gross_profit", {}).get("availability") != "available":
            metrics["gross_profit"] = metric(sum(value for value in profits if value > 0), reason="computed from trade log profit column")
        if metrics.get("gross_loss", {}).get("availability") != "available":
            metrics["gross_loss"] = metric(sum(value for value in profits if value < 0), reason="computed from trade log profit column")
        if metrics.get("win_rate", {}).get("availability") != "available":
            metrics["win_rate"] = metric(
                sum(1 for value in profits if value > 0) / len(profits) * 100,
                reason="computed from trade log profit column",
            )
        if metrics.get("average_trade", {}).get("availability") != "available":
            metrics["average_trade"] = metric(mean(profits), reason="computed from trade log profit column")
        metrics["median_trade"] = metric(median(profits))
        metrics["largest_winning_trade"] = metric(max(profits))
        metrics["largest_losing_trade"] = metric(min(profits))
        total_abs_pnl = sum(abs(value) for value in profits)
        largest_abs = max(abs(value) for value in profits)
        metrics["largest_trade_abs_pnl_pct_of_total_abs_pnl"] = metric(largest_abs / total_abs_pnl if total_abs_pnl else None)
        net_pnl = sum(profits)
        if net_pnl == 0:
            metrics["largest_trade_pct_of_total_pnl"] = unavailable("net PnL is zero")
        else:
            metrics["largest_trade_pct_of_total_pnl"] = metric(max(profits) / net_pnl)
        metrics["consecutive_losses_from_trades"] = metric(consecutive_losses(profits))
    else:
        reason = trades_error or "trade log lacks parseable profit values"
        for name in [
            "median_trade",
            "largest_winning_trade",
            "largest_losing_trade",
            "largest_trade_abs_pnl_pct_of_total_abs_pnl",
            "largest_trade_pct_of_total_pnl",
            "consecutive_losses_from_trades",
        ]:
            metrics[name] = unavailable(reason)

    if directions:
        metrics["long_trade_count"] = metric(sum(1 for item in directions if item == "long"))
        metrics["short_trade_count"] = metric(sum(1 for item in directions if item == "short"))
    else:
        reason = trades_error or "trade log lacks direction values"
        metrics["long_trade_count"] = unavailable(reason)
        metrics["short_trade_count"] = unavailable(reason)

    times = entry_times + exit_times
    if times:
        metrics["start_date"] = metric(min(times).isoformat(sep=" "))
        metrics["end_date"] = metric(max(times).isoformat(sep=" "))
    elif equity:
        equity_times = [_parse_time(row.get("time")) for row in equity]
        clean_times = [value for value in equity_times if value is not None]
        if clean_times:
            metrics["start_date"] = metric(min(clean_times).isoformat(sep=" "))
            metrics["end_date"] = metric(max(clean_times).isoformat(sep=" "))
        else:
            metrics["start_date"] = unavailable("no parseable trade or equity timestamps")
            metrics["end_date"] = unavailable("no parseable trade or equity timestamps")
    else:
        reason = trades_error or equity_error or "no timestamp artifacts available"
        metrics["start_date"] = unavailable(reason)
        metrics["end_date"] = unavailable(reason)

    if trades:
        first = trades[0]
        metrics["symbol"] = metric(first.get("symbol")) if first.get("symbol") else unavailable("trade log lacks symbol")
        metrics["timeframe"] = metric(first.get("timeframe")) if first.get("timeframe") else unavailable("trade log lacks timeframe")
    elif summary:
        metrics["symbol"] = metric(summary.get("symbol")) if summary.get("symbol") else unavailable("summary lacks symbol")
        metrics["timeframe"] = metric(summary.get("timeframe")) if summary.get("timeframe") else unavailable("summary lacks timeframe")
    else:
        metrics["symbol"] = unavailable("no trade or summary artifact available")
        metrics["timeframe"] = unavailable("no trade or summary artifact available")

    metrics["costs"] = unavailable("current MT5 artifacts do not itemize fees, spread, or slippage")
    return metrics


def available_value(metrics: dict[str, dict[str, Any]], name: str) -> Any:
    item = metrics.get(name)
    if not item or item.get("availability") != "available":
        return None
    return item.get("value")
