from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any

from . import registry
from .schemas import REPO_ROOT, SchemaValidationError, load_yaml


VALID_FREQUENCIES = {"daily", "raw"}
MIN_CORRELATION_OBSERVATIONS = 3
MIN_TAIL_OBSERVATIONS = 3


@dataclass(frozen=True)
class ReturnStream:
    experiment_id: str
    status: str
    returns: dict[str, float]
    metadata: dict[str, Any]
    reason: str | None = None
    warnings: tuple[str, ...] = ()


def _repo_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else REPO_ROOT / path


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = value.strip().replace("+00:00", "")
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def _status(value: Any, availability: str = "available", reason: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"value": value, "availability": availability, "reason": reason, **extra}


def _not_available(reason: str) -> dict[str, Any]:
    return _status(None, "not_available", reason)


def _artifact_by_type(artifacts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        latest[artifact["artifact_type"]] = artifact
    return latest


def _equity_artifact_path(db_path: str | Path, experiment_id: str) -> Path | None:
    artifacts = _artifact_by_type(registry.list_artifacts(db_path, experiment_id))
    artifact = artifacts.get("equity_curve")
    if not artifact:
        return None
    return _repo_path(artifact["path"])


def inspect_equity_csv(path: str | Path) -> dict[str, Any]:
    equity_path = _repo_path(path)
    if not equity_path.is_file():
        return {"status": "not_available", "reason": f"equity.csv not found: {equity_path}"}
    with equity_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)
    fieldnames = reader.fieldnames or []
    timestamp_column = _detect_timestamp_column(fieldnames)
    value_column = _detect_value_column(fieldnames)
    times = [_parse_time(row.get(timestamp_column)) for row in rows] if timestamp_column else []
    clean_times = [item for item in times if item is not None]
    missing_timestamps = len(rows) - len(clean_times)
    deltas = [(current - previous).total_seconds() for previous, current in zip(clean_times, clean_times[1:]) if current >= previous]
    values = [_float(row.get(value_column)) for row in rows] if value_column else []
    clean_values = [item for item in values if item is not None]
    symbols = sorted({row.get("symbol", "") for row in rows if row.get("symbol")})
    return {
        "status": "available",
        "path": str(equity_path),
        "timestamp_column": timestamp_column,
        "equity_or_balance_column": value_column,
        "value_semantics": _infer_value_semantics(value_column, clean_values),
        "sampling_frequency": _describe_frequency(deltas),
        "row_count": len(rows),
        "missing_timestamps": missing_timestamps,
        "multiple_symbols_represented": len(symbols) > 1,
        "symbols": symbols,
        "timezone": "not_inferable_from_equity_csv",
        "first_timestamp": clean_times[0].isoformat(sep=" ") if clean_times else None,
        "last_timestamp": clean_times[-1].isoformat(sep=" ") if clean_times else None,
    }


def _detect_timestamp_column(fieldnames: list[str]) -> str | None:
    lowered = {name.lower(): name for name in fieldnames}
    for candidate in ["time", "timestamp", "datetime", "date"]:
        if candidate in lowered:
            return lowered[candidate]
    return None


def _detect_value_column(fieldnames: list[str]) -> str | None:
    lowered = {name.lower(): name for name in fieldnames}
    for candidate in ["equity", "balance", "account_equity", "cumulative_pnl", "cum_pnl", "pnl"]:
        if candidate in lowered:
            return lowered[candidate]
    return None


def _infer_value_semantics(value_column: str | None, values: list[float]) -> str:
    if not value_column:
        return "not_available"
    lowered = value_column.lower()
    if lowered in {"cumulative_pnl", "cum_pnl", "pnl"}:
        return "cumulative_pnl"
    if lowered in {"equity", "balance", "account_equity"} and values and min(values) > 0:
        return "absolute_account_value"
    return "ambiguous"


def _describe_frequency(deltas: list[float]) -> dict[str, Any]:
    if not deltas:
        return {"status": "not_available", "reason": "fewer than two parseable timestamps"}
    median_seconds = median(deltas)
    if median_seconds >= 86400:
        label = f"{median_seconds / 86400:.2f} days"
    elif median_seconds >= 3600:
        label = f"{median_seconds / 3600:.2f} hours"
    elif median_seconds >= 60:
        label = f"{median_seconds / 60:.2f} minutes"
    else:
        label = f"{median_seconds:.0f} seconds"
    return {
        "status": "available",
        "median_seconds": median_seconds,
        "description": label,
        "irregular_samples_detected": len({round(item) for item in deltas}) > 1,
    }


def validate_portfolio_config(data: dict[str, Any]) -> dict[str, Any]:
    for key in ["portfolio_id", "name", "experiments"]:
        if not data.get(key):
            raise SchemaValidationError(f"portfolio.{key} is required")
    if not isinstance(data["experiments"], list) or not all(isinstance(item, str) and item for item in data["experiments"]):
        raise SchemaValidationError("portfolio.experiments must be a non-empty list of experiment ids")
    frequency = data.get("frequency", "daily")
    if frequency not in VALID_FREQUENCIES:
        raise SchemaValidationError(f"portfolio.frequency must be one of {sorted(VALID_FREQUENCIES)}")
    correlation = data.setdefault("correlation", {})
    windows = correlation.setdefault("rolling_windows", [30, 90])
    if not isinstance(windows, list) or any(not isinstance(item, int) or item < 2 for item in windows):
        raise SchemaValidationError("portfolio.correlation.rolling_windows must contain integers >= 2")
    quantile = correlation.setdefault("tail_threshold_quantile", 0.20)
    if not isinstance(quantile, (int, float)) or not 0 < float(quantile) < 1:
        raise SchemaValidationError("portfolio.correlation.tail_threshold_quantile must be between 0 and 1")
    thresholds = data.setdefault("promotion_thresholds", {})
    for key, default in [
        ("max_average_abs_corr", 0.70),
        ("max_tail_corr", 0.80),
        ("max_drawdown_overlap", 0.75),
    ]:
        value = thresholds.setdefault(key, default)
        if not isinstance(value, (int, float)) or float(value) < 0:
            raise SchemaValidationError(f"portfolio.promotion_thresholds.{key} must be a non-negative number")
    return data


def load_portfolio_config(path: str | Path) -> dict[str, Any]:
    data = load_yaml(path)
    return validate_portfolio_config(data)


def extract_return_stream(
    *,
    experiment_id: str,
    equity_path: str | Path | None,
    frequency: str = "daily",
    equity_value_type: str = "auto",
    base_capital: float | None = None,
) -> ReturnStream:
    if frequency not in VALID_FREQUENCIES:
        return ReturnStream(experiment_id, "not_available", {}, {}, f"unsupported frequency: {frequency}")
    if not equity_path:
        return ReturnStream(experiment_id, "not_available", {}, {}, "equity_curve artifact is missing")
    path = _repo_path(equity_path)
    inspection = inspect_equity_csv(path)
    if inspection["status"] != "available":
        return ReturnStream(experiment_id, "not_available", {}, {"source_artifact": str(path)}, inspection["reason"])
    timestamp_column = inspection.get("timestamp_column")
    value_column = inspection.get("equity_or_balance_column")
    if not timestamp_column or not value_column:
        return ReturnStream(
            experiment_id,
            "not_available",
            {},
            {"source_artifact": str(path), "equity_format": inspection},
            "equity.csv lacks a timestamp column or equity/balance column",
        )
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    points: list[tuple[datetime, float]] = []
    for row in rows:
        timestamp = _parse_time(row.get(timestamp_column))
        value = _float(row.get(value_column))
        if timestamp is not None and value is not None:
            points.append((timestamp, value))
    points.sort(key=lambda item: item[0])
    if len(points) < 2:
        return ReturnStream(
            experiment_id,
            "not_available",
            {},
            {"source_artifact": str(path), "equity_format": inspection},
            "equity curve has fewer than two parseable samples",
        )
    value_type = _resolve_equity_value_type(equity_value_type, value_column, [value for _, value in points])
    if value_type is None:
        return ReturnStream(
            experiment_id,
            "not_available",
            {},
            {"source_artifact": str(path), "equity_format": inspection, "return_type": "inferred"},
            "equity value semantics are ambiguous; configure equity_value_type as absolute_equity or cumulative_pnl",
        )
    raw_returns: list[tuple[datetime, float]] = []
    if value_type == "absolute_equity":
        for (previous_ts, previous), (current_ts, current) in zip(points, points[1:]):
            if previous <= 0:
                continue
            raw_returns.append((current_ts, (current - previous) / previous))
        return_type = "pct_return"
    else:
        capital = base_capital or abs(points[0][1])
        if not capital:
            return ReturnStream(
                experiment_id,
                "not_available",
                {},
                {"source_artifact": str(path), "equity_format": inspection},
                "cumulative PnL return extraction requires base_capital",
            )
        for (previous_ts, previous), (current_ts, current) in zip(points, points[1:]):
            raw_returns.append((current_ts, (current - previous) / capital))
        return_type = "pnl_return"
    normalized = _normalize_returns(raw_returns, frequency)
    if not normalized:
        return ReturnStream(
            experiment_id,
            "not_available",
            {},
            {"source_artifact": str(path), "equity_format": inspection, "return_type": return_type},
            "no return intervals could be computed from equity.csv",
        )
    metadata = {
        "start_ts": points[0][0].isoformat(sep=" "),
        "end_ts": points[-1][0].isoformat(sep=" "),
        "sample_count": len(normalized),
        "frequency": frequency,
        "source_artifact": str(path),
        "return_type": return_type,
        "equity_value_type": value_type,
        "equity_format": inspection,
    }
    warnings: list[str] = []
    if inspection.get("missing_timestamps"):
        warnings.append(f"{inspection['missing_timestamps']} equity rows lacked parseable timestamps")
    if inspection.get("timezone") == "not_inferable_from_equity_csv":
        warnings.append("equity.csv does not encode timezone; timestamps are treated as exported tester time")
    return ReturnStream(experiment_id, "available", normalized, metadata, warnings=tuple(warnings))


def _resolve_equity_value_type(configured: str, value_column: str, values: list[float]) -> str | None:
    if configured in {"absolute_equity", "cumulative_pnl"}:
        return configured
    if configured != "auto":
        return None
    inferred = _infer_value_semantics(value_column, values)
    if inferred == "absolute_account_value":
        return "absolute_equity"
    if inferred == "cumulative_pnl":
        return "cumulative_pnl"
    return None


def _normalize_returns(raw_returns: list[tuple[datetime, float]], frequency: str) -> dict[str, float]:
    if frequency == "raw":
        return {timestamp.isoformat(sep=" "): value for timestamp, value in raw_returns}
    grouped: dict[str, list[float]] = {}
    for timestamp, value in raw_returns:
        grouped.setdefault(timestamp.date().isoformat(), []).append(value)
    normalized: dict[str, float] = {}
    for date_key, values in grouped.items():
        compound = 1.0
        for value in values:
            compound *= 1.0 + value
        normalized[date_key] = compound - 1.0
    return dict(sorted(normalized.items()))


def _annual_periods(frequency: str) -> int | None:
    return 252 if frequency == "daily" else None


def _series_metrics(stream: ReturnStream) -> dict[str, Any]:
    if stream.status != "available":
        return {"status": "not_available", "reason": stream.reason}
    values = list(stream.returns.values())
    periods = _annual_periods(stream.metadata["frequency"])
    cumulative = _cumulative_curve(values)
    max_dd = _max_drawdown_from_curve(cumulative)
    volatility = pstdev(values) if len(values) > 1 else None
    annualized_volatility = volatility * math.sqrt(periods) if volatility is not None and periods else None
    sharpe = None
    if volatility and volatility > 0:
        sharpe = mean(values) / volatility * (math.sqrt(periods) if periods else 1.0)
    annualized_return = None
    if periods and cumulative and cumulative[-1] > 0:
        annualized_return = cumulative[-1] ** (periods / len(values)) - 1.0
    return {
        "status": "available",
        "annualized_return": annualized_return,
        "annualized_volatility": annualized_volatility,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "skew": _skew(values),
        "start_ts": stream.metadata["start_ts"],
        "end_ts": stream.metadata["end_ts"],
        "sample_count": stream.metadata["sample_count"],
        "return_type": stream.metadata["return_type"],
        "source_artifact": stream.metadata["source_artifact"],
    }


def _cumulative_curve(values: list[float]) -> list[float]:
    curve: list[float] = []
    equity = 1.0
    for value in values:
        equity *= 1.0 + value
        curve.append(equity)
    return curve


def _max_drawdown_from_curve(curve: list[float]) -> float | None:
    if not curve:
        return None
    peak = curve[0]
    max_dd = 0.0
    for value in curve:
        peak = max(peak, value)
        if peak > 0:
            max_dd = min(max_dd, value / peak - 1.0)
    return abs(max_dd)


def _skew(values: list[float]) -> float | None:
    if len(values) < 3:
        return None
    avg = mean(values)
    deviation = pstdev(values)
    if deviation == 0:
        return None
    return mean([((value - avg) / deviation) ** 3 for value in values])


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < MIN_CORRELATION_OBSERVATIONS:
        return None
    left_mean = mean(left)
    right_mean = mean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_den = math.sqrt(sum((a - left_mean) ** 2 for a in left))
    right_den = math.sqrt(sum((b - right_mean) ** 2 for b in right))
    denominator = left_den * right_den
    if denominator == 0:
        return None
    return numerator / denominator


def _aligned_pair(left: dict[str, float], right: dict[str, float]) -> tuple[list[str], list[float], list[float]]:
    timestamps = sorted(set(left) & set(right))
    return timestamps, [left[item] for item in timestamps], [right[item] for item in timestamps]


def _correlation_matrix(streams: dict[str, ReturnStream]) -> dict[str, dict[str, Any]]:
    matrix: dict[str, dict[str, Any]] = {}
    for left_id, left in streams.items():
        matrix[left_id] = {}
        for right_id, right in streams.items():
            if left.status != "available" or right.status != "available":
                matrix[left_id][right_id] = _not_available("return stream unavailable")
            elif left_id == right_id:
                matrix[left_id][right_id] = _status(1.0)
            else:
                timestamps, left_values, right_values = _aligned_pair(left.returns, right.returns)
                corr = _pearson(left_values, right_values)
                matrix[left_id][right_id] = (
                    _status(corr, reason=f"{len(timestamps)} overlapping observations")
                    if corr is not None
                    else _not_available(f"fewer than {MIN_CORRELATION_OBSERVATIONS} overlapping non-constant observations")
                )
    return matrix


def _average_abs_correlation(matrix: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for experiment_id, row in matrix.items():
        values = [
            abs(item["value"])
            for other_id, item in row.items()
            if other_id != experiment_id and item.get("availability") == "available" and item.get("value") is not None
        ]
        result[experiment_id] = _status(mean(values)) if values else _not_available("at least two overlapping strategies are required")
    return result


def _rolling_correlation_summary(streams: dict[str, ReturnStream], windows: list[int]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    available_ids = [experiment_id for experiment_id, stream in streams.items() if stream.status == "available"]
    for window in windows:
        window_key = f"rolling_{window}d_correlation"
        pairs: dict[str, Any] = {}
        for index, left_id in enumerate(available_ids):
            for right_id in available_ids[index + 1 :]:
                timestamps, left_values, right_values = _aligned_pair(streams[left_id].returns, streams[right_id].returns)
                if len(timestamps) < window:
                    pairs[f"{left_id}__{right_id}"] = _not_available(f"needs at least {window} overlapping observations")
                    continue
                correlations: list[float] = []
                for start in range(0, len(timestamps) - window + 1):
                    corr = _pearson(left_values[start : start + window], right_values[start : start + window])
                    if corr is not None:
                        correlations.append(corr)
                pairs[f"{left_id}__{right_id}"] = (
                    {
                        "availability": "available",
                        "value": {
                            "mean": mean(correlations),
                            "min": min(correlations),
                            "max": max(correlations),
                            "latest": correlations[-1],
                            "max_abs": max(abs(item) for item in correlations),
                            "sample_count": len(correlations),
                        },
                        "reason": f"{len(timestamps)} overlapping observations",
                    }
                    if correlations
                    else _not_available("rolling windows were constant or uncorrelatable")
                )
        summary[window_key] = pairs or {"status": "not_available", "reason": "at least two strategies are required"}
    return summary


def _peer_portfolio_returns(streams: dict[str, ReturnStream], excluded_id: str) -> dict[str, float]:
    peers = [stream for experiment_id, stream in streams.items() if experiment_id != excluded_id and stream.status == "available"]
    timestamps = sorted({timestamp for stream in peers for timestamp in stream.returns})
    portfolio: dict[str, float] = {}
    for timestamp in timestamps:
        values = [stream.returns[timestamp] for stream in peers if timestamp in stream.returns]
        if values:
            portfolio[timestamp] = mean(values)
    return portfolio


def _tail_correlations(streams: dict[str, ReturnStream], quantile: float) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for experiment_id, stream in streams.items():
        if stream.status != "available":
            result[experiment_id] = {"status": "not_available", "reason": stream.reason}
            continue
        peer_returns = _peer_portfolio_returns(streams, experiment_id)
        if not peer_returns:
            result[experiment_id] = {"status": "not_available", "reason": "at least two strategies are required"}
            continue
        result[experiment_id] = {
            "method": "candidate returns correlated to equal-weight peer portfolio excluding the candidate",
            "correlation_when_strategy_down": _conditional_correlation(stream.returns, peer_returns, stream.returns, lambda value: value < 0),
            "correlation_when_portfolio_down": _conditional_correlation(stream.returns, peer_returns, peer_returns, lambda value: value < 0),
            "correlation_when_portfolio_in_bottom_quantile": _bottom_quantile_correlation(stream.returns, peer_returns, quantile),
        }
    return result


def _conditional_correlation(
    candidate: dict[str, float],
    portfolio: dict[str, float],
    condition_source: dict[str, float],
    predicate: Any,
) -> dict[str, Any]:
    timestamps = sorted(set(candidate) & set(portfolio) & set(condition_source))
    filtered = [timestamp for timestamp in timestamps if predicate(condition_source[timestamp])]
    if len(filtered) < MIN_TAIL_OBSERVATIONS:
        return _not_available(f"fewer than {MIN_TAIL_OBSERVATIONS} tail observations")
    corr = _pearson([candidate[item] for item in filtered], [portfolio[item] for item in filtered])
    return _status(corr, reason=f"{len(filtered)} tail observations") if corr is not None else _not_available("tail observations are constant")


def _bottom_quantile_correlation(candidate: dict[str, float], portfolio: dict[str, float], quantile: float) -> dict[str, Any]:
    timestamps = sorted(set(candidate) & set(portfolio))
    if len(timestamps) < MIN_TAIL_OBSERVATIONS:
        return _not_available(f"fewer than {MIN_TAIL_OBSERVATIONS} overlapping observations")
    values = sorted(portfolio[item] for item in timestamps)
    threshold_index = max(0, min(len(values) - 1, math.floor((len(values) - 1) * quantile)))
    threshold = values[threshold_index]
    filtered = [timestamp for timestamp in timestamps if portfolio[timestamp] <= threshold]
    if len(filtered) < MIN_TAIL_OBSERVATIONS:
        return _not_available(f"bottom quantile has fewer than {MIN_TAIL_OBSERVATIONS} observations")
    corr = _pearson([candidate[item] for item in filtered], [portfolio[item] for item in filtered])
    return (
        _status(corr, reason=f"{len(filtered)} bottom-quantile observations at q={quantile}", threshold=threshold)
        if corr is not None
        else _not_available("bottom-quantile observations are constant")
    )


def _drawdown_flags(returns: dict[str, float]) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    equity = 1.0
    peak = 1.0
    for timestamp, value in sorted(returns.items()):
        equity *= 1.0 + value
        peak = max(peak, equity)
        flags[timestamp] = equity < peak
    return flags


def _drawdown_overlap(streams: dict[str, ReturnStream]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for experiment_id, stream in streams.items():
        if stream.status != "available":
            result[experiment_id] = _not_available(stream.reason or "return stream unavailable")
            continue
        peer_returns = _peer_portfolio_returns(streams, experiment_id)
        if not peer_returns:
            result[experiment_id] = _not_available("at least two strategies are required")
            continue
        strategy_flags = _drawdown_flags(stream.returns)
        portfolio_flags = _drawdown_flags(peer_returns)
        timestamps = sorted(set(strategy_flags) & set(portfolio_flags))
        candidate_drawdown_days = [item for item in timestamps if strategy_flags[item]]
        if not candidate_drawdown_days:
            result[experiment_id] = _not_available("strategy has no drawdown observations")
            continue
        overlap = sum(1 for item in candidate_drawdown_days if portfolio_flags[item])
        result[experiment_id] = _status(
            overlap / len(candidate_drawdown_days),
            reason="fraction of strategy drawdown days overlapping peer-portfolio drawdown days",
            overlap_days=overlap,
            strategy_drawdown_days=len(candidate_drawdown_days),
        )
    return result


def _portfolio_metric(values: list[float], frequency: str) -> tuple[float | None, float | None]:
    if len(values) < 2:
        return None, None
    periods = _annual_periods(frequency)
    volatility = pstdev(values)
    annual_vol = volatility * math.sqrt(periods) if periods else volatility
    sharpe = mean(values) / volatility * (math.sqrt(periods) if periods else 1.0) if volatility else None
    return annual_vol, sharpe


def _marginal_contribution(streams: dict[str, ReturnStream], frequency: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    available = {experiment_id: stream for experiment_id, stream in streams.items() if stream.status == "available"}
    for experiment_id, stream in available.items():
        peer = _peer_portfolio_returns(streams, experiment_id)
        timestamps = sorted(set(stream.returns) & set(peer))
        if len(timestamps) < MIN_CORRELATION_OBSERVATIONS:
            result[experiment_id] = {"status": "not_available", "reason": "insufficient overlap with peer portfolio"}
            continue
        full_values = [mean([stream.returns[item], peer[item]]) for item in timestamps]
        peer_values = [peer[item] for item in timestamps]
        full_vol, full_sharpe = _portfolio_metric(full_values, frequency)
        peer_vol, peer_sharpe = _portfolio_metric(peer_values, frequency)
        result[experiment_id] = {
            "status": "available",
            "marginal_volatility_contribution": full_vol - peer_vol if full_vol is not None and peer_vol is not None else None,
            "marginal_sharpe_contribution": full_sharpe - peer_sharpe if full_sharpe is not None and peer_sharpe is not None else None,
            "method": "equal-weight portfolio with candidate minus equal-weight peer portfolio excluding candidate",
            "overlap_count": len(timestamps),
        }
    for experiment_id, stream in streams.items():
        if experiment_id not in result:
            result[experiment_id] = {"status": "not_available", "reason": stream.reason or "at least two strategies are required"}
    return result


def _promotion_gates(
    *,
    average_abs_correlation: dict[str, dict[str, Any]],
    tail_correlation: dict[str, Any],
    drawdown_overlap: dict[str, Any],
    thresholds: dict[str, Any],
    strategy_count: int,
    unavailable_streams: list[str],
) -> tuple[dict[str, Any], list[str], str]:
    warnings: list[str] = []
    gates: dict[str, Any] = {}
    gate_status_on_breach = thresholds.get("breach_status", "warn")
    if gate_status_on_breach not in {"warn", "fail"}:
        gate_status_on_breach = "warn"
    if strategy_count < 2:
        warnings.append("portfolio analytics needs at least two available strategies for correlation gates")
    for experiment_id in unavailable_streams:
        warnings.append(f"return stream unavailable for {experiment_id}")
    for experiment_id, item in average_abs_correlation.items():
        threshold = float(thresholds["max_average_abs_corr"])
        value = item.get("value") if item.get("availability") == "available" else None
        gates.setdefault(experiment_id, {})
        if value is None:
            gates[experiment_id]["average_abs_correlation"] = _not_available(item.get("reason") or "average correlation unavailable")
        elif value > threshold:
            gates[experiment_id]["average_abs_correlation"] = {"status": gate_status_on_breach, "value": value, "threshold": threshold}
            warnings.append(f"{experiment_id} average absolute correlation {value:.3f} exceeds {threshold:.3f}")
        else:
            gates[experiment_id]["average_abs_correlation"] = {"status": "pass", "value": value, "threshold": threshold}
        tail_threshold = float(thresholds["max_tail_corr"])
        tail_values = _available_tail_values(tail_correlation.get(experiment_id, {}))
        max_tail = max((abs(value) for value in tail_values), default=None)
        if max_tail is None:
            gates[experiment_id]["tail_correlation"] = _not_available("tail correlation unavailable")
        elif max_tail > tail_threshold:
            gates[experiment_id]["tail_correlation"] = {"status": gate_status_on_breach, "value": max_tail, "threshold": tail_threshold}
            warnings.append(f"{experiment_id} tail correlation {max_tail:.3f} exceeds {tail_threshold:.3f}")
        else:
            gates[experiment_id]["tail_correlation"] = {"status": "pass", "value": max_tail, "threshold": tail_threshold}
        overlap_threshold = float(thresholds["max_drawdown_overlap"])
        overlap_item = drawdown_overlap.get(experiment_id, {})
        overlap_value = overlap_item.get("value") if overlap_item.get("availability") == "available" else None
        if overlap_value is None:
            gates[experiment_id]["drawdown_overlap"] = _not_available(overlap_item.get("reason") or "drawdown overlap unavailable")
        elif overlap_value > overlap_threshold:
            gates[experiment_id]["drawdown_overlap"] = {"status": gate_status_on_breach, "value": overlap_value, "threshold": overlap_threshold}
            warnings.append(f"{experiment_id} drawdown overlap {overlap_value:.3f} exceeds {overlap_threshold:.3f}")
        else:
            gates[experiment_id]["drawdown_overlap"] = {"status": "pass", "value": overlap_value, "threshold": overlap_threshold}
    statuses = _collect_gate_statuses(gates)
    if "fail" in statuses:
        overall = "fail"
    elif not average_abs_correlation:
        overall = "not_available"
    elif "warn" in statuses or warnings or "not_available" in statuses:
        overall = "warn"
    else:
        overall = "pass"
    return gates, warnings, overall


def _available_tail_values(section: Any) -> list[float]:
    values: list[float] = []
    if isinstance(section, dict):
        if section.get("availability") == "available" and isinstance(section.get("value"), (int, float)):
            values.append(float(section["value"]))
        for value in section.values():
            values.extend(_available_tail_values(value))
    return values


def _collect_gate_statuses(section: Any) -> list[str]:
    statuses: list[str] = []
    if isinstance(section, dict):
        if isinstance(section.get("status"), str):
            statuses.append(section["status"])
        if isinstance(section.get("availability"), str) and section["availability"] == "not_available":
            statuses.append("not_available")
        for value in section.values():
            statuses.extend(_collect_gate_statuses(value))
    elif isinstance(section, list):
        for value in section:
            statuses.extend(_collect_gate_statuses(value))
    return statuses


def _single_experiment_config(experiment_id: str) -> dict[str, Any]:
    return validate_portfolio_config(
        {
            "portfolio_id": f"single_experiment_{experiment_id}",
            "name": f"Single experiment portfolio for {experiment_id}",
            "description": "Compatibility report generated without a portfolio config.",
            "experiments": [experiment_id],
            "frequency": "daily",
            "correlation": {"rolling_windows": [30, 90], "tail_threshold_quantile": 0.20},
            "promotion_thresholds": {
                "max_average_abs_corr": 0.70,
                "max_tail_corr": 0.80,
                "max_drawdown_overlap": 0.75,
            },
        }
    )


def build_portfolio_report(
    db_path: str | Path,
    experiment_id: str | None = None,
    *,
    portfolio_config: dict[str, Any] | None = None,
    candidate_experiment_id: str | None = None,
) -> dict[str, Any]:
    if portfolio_config is None:
        if not experiment_id:
            raise ValueError("experiment_id or portfolio_config is required")
        portfolio_config = _single_experiment_config(experiment_id)
    else:
        portfolio_config = validate_portfolio_config(dict(portfolio_config))
    experiment_ids = list(dict.fromkeys(portfolio_config["experiments"]))
    if candidate_experiment_id and candidate_experiment_id not in experiment_ids:
        experiment_ids.append(candidate_experiment_id)
    frequency = portfolio_config.get("frequency", "daily")
    streams: dict[str, ReturnStream] = {}
    data_availability: dict[str, Any] = {}
    for current_id in experiment_ids:
        experiment = registry.get_experiment(db_path, current_id)
        if not experiment:
            stream = ReturnStream(current_id, "not_available", {}, {}, "experiment not found in registry")
        else:
            equity_path = _equity_artifact_path(db_path, current_id)
            stream = extract_return_stream(
                experiment_id=current_id,
                equity_path=equity_path,
                frequency=frequency,
                equity_value_type=portfolio_config.get("equity_value_type", "auto"),
                base_capital=portfolio_config.get("base_capital"),
            )
        streams[current_id] = stream
        data_availability[current_id] = {
            "status": stream.status,
            "reason": stream.reason,
            "metadata": stream.metadata,
            "warnings": list(stream.warnings),
        }
    available_streams = {experiment_id: stream for experiment_id, stream in streams.items() if stream.status == "available"}
    per_strategy_metrics = {experiment_id: _series_metrics(stream) for experiment_id, stream in streams.items()}
    matrix = _correlation_matrix(streams) if len(streams) >= 2 else {}
    average_abs_corr = _average_abs_correlation(matrix) if matrix else {item: _not_available("at least two strategies are required") for item in experiment_ids}
    windows = portfolio_config.get("correlation", {}).get("rolling_windows", [30, 90])
    rolling = _rolling_correlation_summary(streams, windows)
    tail = _tail_correlations(streams, float(portfolio_config.get("correlation", {}).get("tail_threshold_quantile", 0.20)))
    overlap = _drawdown_overlap(streams)
    marginal = _marginal_contribution(streams, frequency)
    gates, warnings, status = _promotion_gates(
        average_abs_correlation=average_abs_corr,
        tail_correlation=tail,
        drawdown_overlap=overlap,
        thresholds=portfolio_config["promotion_thresholds"],
        strategy_count=len(available_streams),
        unavailable_streams=[experiment_id for experiment_id, stream in streams.items() if stream.status != "available"],
    )
    for stream in streams.values():
        warnings.extend(stream.warnings)
    warnings = sorted(set(warnings))
    return {
        "schema_version": "portfolio_report_v1",
        "portfolio_id": portfolio_config["portfolio_id"],
        "name": portfolio_config.get("name"),
        "description": portfolio_config.get("description"),
        "generated_at": registry.utc_now(),
        "frequency": frequency,
        "experiment_ids": experiment_ids,
        "candidate_experiment_id": candidate_experiment_id,
        "data_availability": data_availability,
        "per_strategy_metrics": per_strategy_metrics,
        "correlation_matrix": matrix,
        "average_abs_correlation": average_abs_corr,
        "rolling_correlation_summary": rolling,
        "tail_correlation": tail,
        "drawdown_overlap": overlap,
        "marginal_contribution": marginal,
        "promotion_gate_summary": {
            "status": status,
            "default_breach_policy": portfolio_config["promotion_thresholds"].get("breach_status", "warn"),
            "thresholds": portfolio_config["promotion_thresholds"],
            "gates": gates,
        },
        "warnings": warnings,
        "status": status,
        "method_notes": [
            "Daily returns are compounded from exported equity samples unless frequency is raw.",
            "Correlation and marginal contribution use overlapping timestamps only.",
            "Tail and drawdown overlap compare each strategy to an equal-weight peer portfolio excluding that strategy.",
            "Phase 4 defaults threshold breaches to warn unless promotion_thresholds.breach_status is set to fail.",
        ],
    }


def write_portfolio_report(db_path: str | Path, experiment_id: str, output_path: str | Path) -> dict[str, Any]:
    report = build_portfolio_report(db_path, experiment_id)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    registry.attach_artifact(db_path, experiment_id, "portfolio_report", path)
    return report


def default_portfolio_report_path(portfolio_config: dict[str, Any]) -> Path:
    return REPO_ROOT / "automated" / "reports" / "portfolios" / portfolio_config["portfolio_id"] / "portfolio_report.json"


def write_configured_portfolio_report(
    db_path: str | Path,
    portfolio_path: str | Path,
    output_path: str | Path | None = None,
    *,
    candidate_experiment_id: str | None = None,
) -> dict[str, Any]:
    config = load_portfolio_config(portfolio_path)
    report = build_portfolio_report(db_path, portfolio_config=config, candidate_experiment_id=candidate_experiment_id)
    path = Path(output_path) if output_path else default_portfolio_report_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    if candidate_experiment_id:
        registry.attach_artifact(db_path, candidate_experiment_id, "portfolio_report", path)
    return report
