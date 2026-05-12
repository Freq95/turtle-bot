"""
Runs the full matrix of 15 backtests. See SPEC.md §19.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

import config
from backtest.engine import BacktestResult, run_backtest
from backtest.metrics import compute_all_metrics
from benchmarks.buy_and_hold import run_buy_and_hold
from strategies.strategy_a import StrategyA
from strategies.strategy_b import StrategyB
from strategies.strategy_c import StrategyC
from strategies.strategy_d import StrategyD
from strategies.strategy_d_stopcapped import StrategyDStopCapped

log = logging.getLogger(__name__)


@dataclass
class BacktestRecord:
    """Single backtest result + metrics for full/IS/OOS periods."""
    label: str               # human-readable, e.g., "A_ComplexMultiLayer_spot_1x"
    strategy_key: str        # 'A', 'B', 'C', 'D-Primary', 'D-Alt-Short', 'D-Alt-Med', 'BnH'
    mode: str
    direction: str           # 'long' or 'long+short'
    leverage: float
    purpose: str             # 'main' or 'robustness' or 'benchmark'
    result: BacktestResult
    metrics_full: dict
    metrics_is: Optional[dict] = None
    metrics_oos: Optional[dict] = None


# Test matrix — order matters for reporting (matches SPEC §19)
TEST_MATRIX = [
    # (strategy_key, mode, direction, purpose)
    ("A",            "spot_1x",    "long",        "main"),
    ("A",            "futures_1x", "long+short",  "main"),
    ("A",            "futures_2x", "long+short",  "main"),
    ("B",            "spot_1x",    "long",        "main"),
    ("B",            "futures_1x", "long",        "main"),
    ("B",            "futures_2x", "long",        "main"),
    ("C",            "spot_1x",    "long",        "main"),
    ("C",            "futures_1x", "long",        "main"),
    ("C",            "futures_2x", "long",        "main"),
    ("D-Primary",    "spot_1x",    "long",        "main"),
    ("D-Primary",    "futures_1x", "long+short",  "main"),
    ("D-Primary",    "futures_2x", "long+short",  "main"),
    ("D-Alt-Short",  "spot_1x",    "long",        "main"),
    ("D-Alt-Med",    "spot_1x",    "long",        "main"),
    ("D-StopCapped", "spot_1x",    "long",        "main"),
    ("D-StopCapped", "futures_1x", "long+short",  "main"),
    ("D-StopCapped", "futures_2x", "long+short",  "main"),
    ("BnH",          "spot_1x",    "long",        "benchmark"),
]


def _instantiate(strategy_key: str, mode: str):
    if strategy_key == "A":
        return StrategyA(mode)
    if strategy_key == "B":
        return StrategyB(mode)
    if strategy_key == "C":
        return StrategyC(mode)
    if strategy_key == "D-Primary":
        return StrategyD(mode, n_entry=55, n_exit=20, variant_label="Primary")
    if strategy_key == "D-Alt-Short":
        return StrategyD(mode, n_entry=20, n_exit=10, variant_label="AltShort")
    if strategy_key == "D-Alt-Med":
        return StrategyD(mode, n_entry=40, n_exit=15, variant_label="AltMed")
    if strategy_key == "D-StopCapped":
        return StrategyDStopCapped(mode)
    raise ValueError(f"Unknown strategy key: {strategy_key}")


def run_all(daily_df: pd.DataFrame, four_h_df: pd.DataFrame) -> list[BacktestRecord]:
    """Runs all 15 backtests. Returns list of BacktestRecord."""
    records: list[BacktestRecord] = []

    for idx, (skey, mode, direction, purpose) in enumerate(TEST_MATRIX, start=1):
        leverage = config.get_leverage_for_mode(mode)
        label = f"{skey}_{mode}"

        log.info("[%d/%d] Running %s ...", idx, len(TEST_MATRIX), label)

        if skey == "BnH":
            result = run_buy_and_hold(daily_df, config.BACKTEST_START, config.BACKTEST_END)
        else:
            strat = _instantiate(skey, mode)
            result = run_backtest(strat, daily_df, four_h_df, mode,
                                  config.BACKTEST_START, config.BACKTEST_END)

        # Compute metrics
        metrics_full = compute_all_metrics(
            result.equity_curve, result.trades,
            initial_equity=result.initial_equity,
            period_start=config.BACKTEST_START, period_end=config.BACKTEST_END,
        )

        # IS / OOS only for main eval and benchmark
        metrics_is = None
        metrics_oos = None
        if purpose in ("main", "benchmark"):
            metrics_is = compute_all_metrics(
                result.equity_curve, result.trades,
                initial_equity=result.initial_equity,
                period_start=config.BACKTEST_START, period_end=config.IN_SAMPLE_END,
            )
            metrics_oos = compute_all_metrics(
                result.equity_curve, result.trades,
                initial_equity=result.initial_equity,
                period_start=config.OUT_OF_SAMPLE_START, period_end=config.BACKTEST_END,
            )

        records.append(BacktestRecord(
            label=label,
            strategy_key=skey,
            mode=mode,
            direction=direction,
            leverage=leverage,
            purpose=purpose,
            result=result,
            metrics_full=metrics_full,
            metrics_is=metrics_is,
            metrics_oos=metrics_oos,
        ))

        log.info("  → final=$%.2f  trades=%d  CAGR=%.2f%%  Sharpe=%.2f  MaxDD=%.2f%%",
                 result.final_equity, len(result.trades),
                 metrics_full.get("cagr_pct", 0.0),
                 metrics_full.get("sharpe", 0.0),
                 metrics_full.get("max_dd_pct", 0.0))

    return records
