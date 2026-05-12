"""
Strategy D — Vol-Targeted Donchian Breakout (Daily).
See SPEC.md §16 (Primary 55/20) and §17 (Alt variants 20/10, 40/15).

Parametric in (n_entry, n_exit) channel periods.
- Long-only on spot.
- Long+Short on futures.
- No initial/trailing stop loss — channel exit IS the protection.
- No circuit breakers — vol-targeting handles risk adaptively.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

import config
from backtest import indicators as ind
from backtest.position import Order, OrderAction, Position
from strategies.base import BaseStrategy


class StrategyD(BaseStrategy):
    def __init__(self, mode: str, n_entry: int = 55, n_exit: int = 20,
                 variant_label: str = "Primary"):
        allow_short = mode.startswith("futures_")
        name = f"D_Donchian_{n_entry}_{n_exit}_{variant_label}"
        super().__init__(name=name, mode=mode, allow_short=allow_short)
        self.n_entry = n_entry
        self.n_exit = n_exit

    def prepare_data(self, daily_df: pd.DataFrame, four_h_df: pd.DataFrame) -> pd.DataFrame:
        df = daily_df.copy()
        df["donch_high_entry"] = ind.donchian_high(df["high"], self.n_entry)
        df["donch_low_exit"] = ind.donchian_low(df["low"], self.n_exit)
        df["donch_low_entry"] = ind.donchian_low(df["low"], self.n_entry)
        df["donch_high_exit"] = ind.donchian_high(df["high"], self.n_exit)
        df["realized_vol_annual"] = ind.realized_vol(
            df["close"],
            n=config.VOL_LOOKBACK_DAYS,
            annualization_days=config.ANNUALIZATION_DAYS,
        )
        return df

    def _compute_vol_sized_notional(self, bar, equity: float) -> Optional[float]:
        sigma_ann = getattr(bar, "realized_vol_annual", float("nan"))
        if pd.isna(sigma_ann) or sigma_ann <= 0:
            return None
        target_fraction = config.VOL_TARGET_ANNUAL / sigma_ann
        if target_fraction < config.VOL_MIN_NOTIONAL_FRACTION:
            return None
        max_fraction = config.VOL_MAX_NOTIONAL_FRACTION[self.mode]
        fraction = min(target_fraction, max_fraction)
        return fraction * equity

    def evaluate(self, bar, position: Optional[Position],
                 equity_state: dict) -> Optional[Order]:
        # Warmup check (need all 4 donchian + vol)
        for col in ("donch_high_entry", "donch_low_exit", "donch_low_entry",
                    "donch_high_exit", "realized_vol_annual"):
            if pd.isna(getattr(bar, col, float("nan"))):
                return None

        equity = equity_state["equity"]

        if position is not None:
            # Exit checks (channel-based, close vs prior-period extreme)
            if position.is_long and bar.close < bar.donch_low_exit:
                return Order(action=OrderAction.CLOSE, reason="donch_exit_long")
            if position.is_short and bar.close > bar.donch_high_exit:
                return Order(action=OrderAction.CLOSE, reason="donch_exit_short")
            return None

        # Entry checks — exit takes precedence is implicit (we only reach here if no position)
        # LONG entry: close > 55-day high
        if bar.close > bar.donch_high_entry:
            notional = self._compute_vol_sized_notional(bar, equity)
            if notional is None:
                return None
            return Order(
                action=OrderAction.OPEN_LONG,
                notional=notional,
                atr_at_signal=0.0,  # no ATR-based stop
                initial_stop=0.0,
                reason="donch_breakout_long",
            )

        # SHORT entry (futures only): close < 55-day low
        if self.allow_short and bar.close < bar.donch_low_entry:
            notional = self._compute_vol_sized_notional(bar, equity)
            if notional is None:
                return None
            return Order(
                action=OrderAction.OPEN_SHORT,
                notional=notional,
                atr_at_signal=0.0,
                initial_stop=0.0,
                reason="donch_breakout_short",
            )

        return None
