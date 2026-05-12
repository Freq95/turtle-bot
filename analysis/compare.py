"""
Master CSV writer + equity curves overlay. See SPEC.md §22.1, §22.2.
"""

from __future__ import annotations

import csv
import logging
from typing import Iterable

import matplotlib

matplotlib.use("Agg")  # no display
import matplotlib.pyplot as plt

import config
from backtest.runner import BacktestRecord

log = logging.getLogger(__name__)


CSV_COLUMNS = [
    "strategy", "mode", "direction", "leverage", "purpose", "period",
    "period_initial_usd", "period_final_usd",
    "total_return_pct", "cagr_pct",
    "max_dd_pct", "avg_dd_pct", "dd_duration_days",
    "sharpe", "sortino", "calmar",
    "total_trades", "win_rate_pct", "avg_win_pct", "avg_loss_pct", "profit_factor",
    "best_trade_pct", "worst_trade_pct", "avg_hold_days",
    "time_in_market_pct", "avg_position_size_pct",
    "total_fees_usd", "total_slippage_usd", "total_funding_usd", "net_vs_gross_pct",
    "annual_2020_pct", "annual_2021_pct", "annual_2022_pct", "annual_2023_pct", "annual_2024_pct",
]


def _row_for_record(rec: BacktestRecord, period_label: str, metrics: dict) -> dict:
    return {
        "strategy": rec.strategy_key,
        "mode": rec.mode,
        "direction": rec.direction,
        "leverage": rec.leverage,
        "purpose": rec.purpose,
        "period": period_label,
        **{k: metrics.get(k, "") for k in CSV_COLUMNS if k not in
           {"strategy", "mode", "direction", "leverage", "purpose", "period"}},
    }


def write_master_csv(records: Iterable[BacktestRecord], path: str) -> None:
    rows = []
    for rec in records:
        rows.append(_row_for_record(rec, "full", rec.metrics_full))
        if rec.metrics_is is not None:
            rows.append(_row_for_record(rec, "in_sample", rec.metrics_is))
        if rec.metrics_oos is not None:
            rows.append(_row_for_record(rec, "out_of_sample", rec.metrics_oos))

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            # Format floats to 4 decimals; keep ints/strings as-is
            out_row = {}
            for k, v in row.items():
                if isinstance(v, float):
                    out_row[k] = f"{v:.4f}"
                else:
                    out_row[k] = v
            writer.writerow(out_row)

    log.info("Wrote %d rows → %s", len(rows), path)


def write_equity_overlay(records: list[BacktestRecord], path: str) -> None:
    """Overlay equity curves of all 'main' strategies + 'benchmark', log Y."""
    fig, ax = plt.subplots(figsize=(16, 10))

    # Color palette: distinct colors for each strategy family
    color_map = {
        "A": "#d62728",          # red
        "B": "#2ca02c",          # green
        "C": "#ff7f0e",          # orange
        "D-Primary": "#1f77b4",  # blue
        "BnH": "#000000",        # black
    }
    style_by_mode = {
        "spot_1x": "-",
        "futures_1x": "--",
        "futures_2x": ":",
    }

    for rec in records:
        if rec.purpose == "robustness":
            continue
        color = color_map.get(rec.strategy_key, "#888888")
        style = style_by_mode.get(rec.mode, "-")
        label = f"{rec.strategy_key} ({rec.mode})"
        ec = rec.result.equity_curve
        if rec.strategy_key == "BnH":
            ax.plot(ec.index, ec.values, label=label, color=color, linewidth=2.5,
                    linestyle="-", alpha=0.9)
        else:
            ax.plot(ec.index, ec.values, label=label, color=color,
                    linestyle=style, linewidth=1.5, alpha=0.85)

    ax.set_yscale("log")
    ax.set_xlabel("Date")
    ax.set_ylabel("Equity (USD, log scale)")
    ax.set_title("BTC Backtest — Equity Curves Overlay")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(loc="upper left", ncol=2, fontsize=9)

    # IS / OOS boundary
    import pandas as pd
    boundary = pd.Timestamp(config.OUT_OF_SAMPLE_START, tz="UTC")
    ax.axvline(boundary, color="gray", linestyle="--", alpha=0.5, linewidth=1)
    ax.text(boundary, ax.get_ylim()[1] * 0.7, " OOS →", color="gray", fontsize=9)

    fig.tight_layout()
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    log.info("Wrote equity overlay → %s", path)
