"""
Strategy B — Simple Pure Trend (Daily, Long-only, no circuit breakers).
See SPEC.md §14.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

import config
from backtest import indicators as ind
from backtest.position import Order, OrderAction, Position
from strategies.base import BaseStrategy


class StrategyB(BaseStrategy):
    def __init__(self, mode: str):
        super().__init__(name="B_SimpleTrend", mode=mode, allow_short=False)

    def prepare_data(self, daily_df: pd.DataFrame, four_h_df: pd.DataFrame) -> pd.DataFrame:
        df = daily_df.copy()
        df["ema50_d"] = ind.ema(df["close"], 50)
        df["ema200_d"] = ind.ema(df["close"], 200)
        df["atr14_d"] = ind.atr(df["high"], df["low"], df["close"], 14)
        df["vol_sma20_d"] = ind.sma(df["volume"], 20).shift(1)
        return df

    def evaluate(self, bar, position: Optional[Position],
                 equity_state: dict) -> Optional[Order]:
        # Warmup check
        if (pd.isna(getattr(bar, "ema200_d", float("nan"))) or
                pd.isna(getattr(bar, "atr14_d", float("nan"))) or
                pd.isna(getattr(bar, "vol_sma20_d", float("nan")))):
            return None

        if position is not None:
            # Update trailing stop: peak_close - 2 * ATR_current. Ratchet up only.
            peak = max(position.peak_close or position.entry_price, bar.close)
            position.peak_close = peak
            new_trail = peak - 2.0 * bar.atr14_d
            if position.current_stop is None or new_trail > position.current_stop:
                position.current_stop = new_trail
                position.state = "TRAILING"
            # Exit signal: close < EMA50
            if bar.close < bar.ema50_d:
                return Order(action=OrderAction.CLOSE, reason="close_below_ema50")
            return None

        # Entry conditions (all 3 must hold)
        if (bar.ema50_d > bar.ema200_d
                and bar.close > bar.ema50_d
                and bar.volume > 1.2 * bar.vol_sma20_d):
            equity = equity_state["equity"]
            stop_distance_pct = (2.0 * bar.atr14_d) / bar.close
            if stop_distance_pct <= 0:
                return None
            risk_amount = config.RISK_PER_TRADE * equity
            target_notional = risk_amount / stop_distance_pct
            max_notional = config.MAX_NOTIONAL_FRACTION[self.mode] * equity
            notional = min(target_notional, max_notional)
            if notional < 1.0:
                return None
            initial_stop = bar.close - 2.0 * bar.atr14_d
            return Order(
                action=OrderAction.OPEN_LONG,
                notional=notional,
                initial_stop=initial_stop,
                atr_at_signal=bar.atr14_d,
                reason="entry_long_trend",
            )
        return None
