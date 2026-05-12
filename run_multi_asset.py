"""
Multi-asset test: D variants on ETH and SOL.

Tests the SAME D strategies (Primary, Alt-Short, Alt-Med, StopCapped) on different
crypto assets to validate whether the system generalizes beyond BTC.

Outputs:
- reports/eth/  — full report set for ETH/USDT
- reports/sol/  — full report set for SOL/USDT

Does NOT overwrite the existing reports/ (BTC) folder.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

import config
from analysis.compare import write_master_csv, write_equity_overlay
from analysis.reports import generate_all_html_reports, generate_summary_html
from backtest.engine import run_backtest
from backtest.metrics import compute_all_metrics
from backtest.runner import BacktestRecord
from benchmarks.buy_and_hold import run_buy_and_hold
from data_loader import load_or_fetch
from strategies.strategy_d import StrategyD
from strategies.strategy_d_stopcapped import StrategyDStopCapped

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# D variants matrix — same as BTC test
D_MATRIX = [
    ("D-Primary",    "spot_1x",    "long",        "main"),
    ("D-Primary",    "futures_1x", "long+short",  "main"),
    ("D-Primary",    "futures_2x", "long+short",  "main"),
    ("D-Alt-Short",  "spot_1x",    "long",        "robustness"),
    ("D-Alt-Med",    "spot_1x",    "long",        "robustness"),
    ("D-StopCapped", "spot_1x",    "long",        "main"),
    ("D-StopCapped", "futures_1x", "long+short",  "main"),
    ("D-StopCapped", "futures_2x", "long+short",  "main"),
    ("BnH",          "spot_1x",    "long",        "benchmark"),
]


def _instantiate(skey: str, mode: str):
    if skey == "D-Primary":
        return StrategyD(mode, n_entry=55, n_exit=20, variant_label="Primary")
    if skey == "D-Alt-Short":
        return StrategyD(mode, n_entry=20, n_exit=10, variant_label="AltShort")
    if skey == "D-Alt-Med":
        return StrategyD(mode, n_entry=40, n_exit=15, variant_label="AltMed")
    if skey == "D-StopCapped":
        return StrategyDStopCapped(mode)
    raise ValueError(f"Unknown strategy key: {skey}")


def run_for_asset(symbol: str,
                  data_start: datetime, data_end: datetime,
                  backtest_start: datetime, backtest_end: datetime,
                  is_end: datetime, oos_start: datetime,
                  output_subdir: str) -> list[BacktestRecord]:
    log.info("=" * 60)
    log.info("Asset: %s | Backtest range: %s → %s",
             symbol, backtest_start.date(), backtest_end.date())
    log.info("=" * 60)

    # Fetch data
    daily = load_or_fetch(symbol, "1d", data_start, data_end)
    four_h = load_or_fetch(symbol, "4h", data_start, data_end)
    log.info("%s: %d daily bars (%s → %s), %d 4h bars",
             symbol, len(daily), daily.index[0].date(), daily.index[-1].date(), len(four_h))

    # Output dir
    out_dir = Path(config.REPORTS_DIR) / output_subdir
    out_dir.mkdir(parents=True, exist_ok=True)

    records: list[BacktestRecord] = []
    for idx, (skey, mode, direction, purpose) in enumerate(D_MATRIX, start=1):
        leverage = config.get_leverage_for_mode(mode)
        label = f"{skey}_{mode}"
        log.info("[%d/%d] Running %s %s", idx, len(D_MATRIX), symbol, label)

        if skey == "BnH":
            result = run_buy_and_hold(daily, backtest_start, backtest_end)
        else:
            strat = _instantiate(skey, mode)
            result = run_backtest(strat, daily, four_h, mode, backtest_start, backtest_end)

        metrics_full = compute_all_metrics(
            result.equity_curve, result.trades,
            initial_equity=result.initial_equity,
            period_start=backtest_start, period_end=backtest_end,
        )
        metrics_is = None
        metrics_oos = None
        if purpose in ("main", "benchmark"):
            metrics_is = compute_all_metrics(
                result.equity_curve, result.trades,
                initial_equity=result.initial_equity,
                period_start=backtest_start, period_end=is_end,
            )
            metrics_oos = compute_all_metrics(
                result.equity_curve, result.trades,
                initial_equity=result.initial_equity,
                period_start=oos_start, period_end=backtest_end,
            )

        rec = BacktestRecord(
            label=label, strategy_key=skey, mode=mode, direction=direction,
            leverage=leverage, purpose=purpose, result=result,
            metrics_full=metrics_full, metrics_is=metrics_is, metrics_oos=metrics_oos,
        )
        records.append(rec)

        log.info("  → final=$%.2f  trades=%d  CAGR=%.2f%%  Sharpe=%.2f  MaxDD=%.2f%%",
                 result.final_equity, len(result.trades),
                 metrics_full.get("cagr_pct", 0.0),
                 metrics_full.get("sharpe", 0.0),
                 metrics_full.get("max_dd_pct", 0.0))

    # Outputs
    log.info("Writing reports to %s/", out_dir)
    write_master_csv(records, str(out_dir / "master.csv"))
    write_equity_overlay(records, str(out_dir / "equity_curves.png"))
    generate_all_html_reports(records, str(out_dir))
    generate_summary_html(records, str(out_dir))

    return records


def main() -> int:
    log.info("Multi-asset test: D variants on ETH and SOL")
    log.info("Initial capital: $%.0f (per config.py)", config.INITIAL_CAPITAL)

    # ETH: similar to BTC range. Binance ETH/USDT from ~Aug 2017.
    run_for_asset(
        symbol="ETH/USDT",
        data_start=datetime(2015, 1, 1),
        data_end=datetime(2025, 12, 31),
        backtest_start=datetime(2018, 1, 1),
        backtest_end=datetime(2025, 12, 31),
        is_end=datetime(2023, 12, 31),
        oos_start=datetime(2024, 1, 1),
        output_subdir="eth",
    )

    # SOL: Binance SOL/USDT launched Aug 2020.
    # Warmup window 2020-08 to 2020-12 (4-5 months) is enough for D's
    # 55-day Donchian + 30-day vol (no EMA200 needed for D).
    run_for_asset(
        symbol="SOL/USDT",
        data_start=datetime(2020, 8, 1),
        data_end=datetime(2025, 12, 31),
        backtest_start=datetime(2021, 1, 1),
        backtest_end=datetime(2025, 12, 31),
        is_end=datetime(2023, 12, 31),
        oos_start=datetime(2024, 1, 1),
        output_subdir="sol",
    )

    log.info("=" * 60)
    log.info("Multi-asset test complete.")
    log.info("  ETH reports: reports/eth/")
    log.info("  SOL reports: reports/sol/")
    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
