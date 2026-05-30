#!/usr/bin/env python3
import csv
import datetime as dt
import html
import sys
from pathlib import Path


def parse_time(value):
    return dt.datetime.strptime(value, "%Y.%m.%d %H:%M:%S")


def read_rows(path):
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def pick_examples(trades):
    candidates = []
    winners = [trade for trade in trades if float(trade["profit"] or 0) > 0]
    losers = [trade for trade in trades if float(trade["profit"] or 0) < 0]

    if winners:
        candidates.append(("first_win", winners[0]))
        candidates.append(("best_win", max(winners, key=lambda trade: float(trade["profit"] or 0))))
    if losers:
        candidates.append(("first_loss", losers[0]))
        candidates.append(("worst_loss", min(losers, key=lambda trade: float(trade["profit"] or 0))))

    seen = set()
    selected = []
    for label, trade in candidates:
        key = (trade["entry_time"], trade["exit_time"], trade["profit"])
        if key in seen:
            continue
        seen.add(key)
        selected.append((label, trade))
    return selected[:4]


def scale(value, min_value, max_value, lower, upper):
    if max_value == min_value:
        return (lower + upper) / 2
    return lower + (value - min_value) / (max_value - min_value) * (upper - lower)


def render_svg(label, trade, bars, output_path):
    width = 1200
    height = 520
    pad_left = 72
    pad_right = 36
    pad_top = 58
    pad_bottom = 56

    entry_time = parse_time(trade["entry_time"])
    exit_time = parse_time(trade["exit_time"])
    bar_times = [parse_time(row["time"]) for row in bars]
    lows = [float(row["low"]) for row in bars]
    highs = [float(row["high"]) for row in bars]
    price_pad = (max(highs) - min(lows)) * 0.08
    min_price = min(lows) - price_pad
    max_price = max(highs) + price_pad
    x_min = bar_times[0].timestamp()
    x_max = bar_times[-1].timestamp()

    def x_for(time_value):
        return scale(time_value.timestamp(), x_min, x_max, pad_left, width - pad_right)

    def y_for(price):
        return height - pad_bottom - scale(price, min_price, max_price, 0, height - pad_top - pad_bottom)

    plot_left = pad_left
    plot_right = width - pad_right
    plot_top = pad_top
    plot_bottom = height - pad_bottom
    candle_slot = (plot_right - plot_left) / max(len(bars), 1)
    candle_width = max(3.0, min(14.0, candle_slot * 0.62))

    entry_price = float(trade["entry_price"])
    exit_price = float(trade["exit_price"])
    profit = float(trade["profit"])
    title = f"{label}: {trade['direction']} {trade['entry_time']} to {trade['exit_time']} profit {profit:.2f}"

    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" style="background:#ffffff">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
        f'<rect x="{plot_left}" y="{plot_top}" width="{plot_right-plot_left}" height="{plot_bottom-plot_top}" fill="#f8fafc" stroke="#cbd5e1"/>',
        f'<text x="{pad_left}" y="24" font-family="Arial" font-size="16" fill="#0f172a">{html.escape(title)}</text>',
        f'<text x="{pad_left}" y="44" font-family="Arial" font-size="12" fill="#475569">OHLC candlesticks, entry/exit marked from EA trade log</text>',
    ]

    for idx in range(5):
        price = min_price + (max_price - min_price) * idx / 4
        y = y_for(price)
        elements.append(f'<line x1="{plot_left}" y1="{y:.2f}" x2="{plot_right}" y2="{y:.2f}" stroke="#e2e8f0"/>')
        elements.append(f'<text x="8" y="{y+4:.2f}" font-family="Arial" font-size="12" fill="#475569">{price:.3f}</text>')

    for index, row in enumerate(bars):
        candle_time = bar_times[index]
        open_price = float(row["open"])
        high_price = float(row["high"])
        low_price = float(row["low"])
        close_price = float(row["close"])
        x = x_for(candle_time)
        y_open = y_for(open_price)
        y_high = y_for(high_price)
        y_low = y_for(low_price)
        y_close = y_for(close_price)
        body_top = min(y_open, y_close)
        body_height = max(1.5, abs(y_close - y_open))
        bullish = close_price >= open_price
        body_fill = "#16a34a" if bullish else "#dc2626"
        wick_color = "#166534" if bullish else "#991b1b"

        elements.append(
            f'<line x1="{x:.2f}" y1="{y_high:.2f}" x2="{x:.2f}" y2="{y_low:.2f}" stroke="{wick_color}" stroke-width="1.4"/>'
        )
        elements.append(
            f'<rect x="{x-candle_width/2:.2f}" y="{body_top:.2f}" width="{candle_width:.2f}" height="{body_height:.2f}" fill="{body_fill}" stroke="{wick_color}" stroke-width="1"/>'
        )

    entry_x = x_for(entry_time)
    exit_x = x_for(exit_time)
    entry_y = y_for(entry_price)
    exit_y = y_for(exit_price)
    elements.extend([
        f'<line x1="{entry_x:.2f}" y1="{plot_top}" x2="{entry_x:.2f}" y2="{plot_bottom}" stroke="#2563eb" stroke-width="2" stroke-dasharray="5 5"/>',
        f'<circle cx="{entry_x:.2f}" cy="{entry_y:.2f}" r="5" fill="#16a34a"/>',
        f'<text x="{entry_x+8:.2f}" y="{entry_y-8:.2f}" font-family="Arial" font-size="12" fill="#166534">entry</text>',
        f'<line x1="{exit_x:.2f}" y1="{plot_top}" x2="{exit_x:.2f}" y2="{plot_bottom}" stroke="#7c3aed" stroke-width="2" stroke-dasharray="5 5"/>',
        f'<circle cx="{exit_x:.2f}" cy="{exit_y:.2f}" r="5" fill="#dc2626"/>',
        f'<text x="{exit_x+8:.2f}" y="{exit_y-8:.2f}" font-family="Arial" font-size="12" fill="#991b1b">exit</text>',
        "</svg>",
    ])

    output_path.write_text("\n".join(elements) + "\n")


def main():
    if len(sys.argv) != 2:
        print("usage: plot_trade_examples.py automated/reports/<run_id>", file=sys.stderr)
        return 2

    report_dir = Path(sys.argv[1])
    trades_path = report_dir / "trades.csv"
    bars_path = report_dir / "bars.csv"
    output_dir = report_dir / "trade_examples"
    output_dir.mkdir(parents=True, exist_ok=True)

    trades = read_rows(trades_path)
    bars = read_rows(bars_path)
    parsed_bars = [(parse_time(row["time"]), row) for row in bars]

    for label, trade in pick_examples(trades):
        entry_time = parse_time(trade["entry_time"])
        exit_time = parse_time(trade["exit_time"])
        span = max(exit_time - entry_time, dt.timedelta(minutes=30))
        start = entry_time - span
        end = exit_time + span
        window = [row for time_value, row in parsed_bars if start <= time_value <= end]
        if len(window) < 2:
            continue
        render_svg(label, trade, window, output_dir / f"{label}.svg")

    print(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
