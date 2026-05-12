"""
Strategy C — Hybrid (Daily, Long-only, simplified circuit breakers).
See SPEC.md §15.

Differences from B:
- Adds ADX(14) > 25 entry filter
- State machine: INITIAL → TRAILING with break-even trigger at +1.5×ATR
- Trailing uses peak HIGH (not peak close like B), 2×ATR
- Drawdown halt at -8% (permanent for this run)
- Consecutive loss tracker (3 losses → 50% size for 5 trades)
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

import config
from backtest import indicators as ind
from backtest.position import Order, OrderAction, Position, Trade
from strategies.base import BaseStrategy


class StrategyC(BaseStrategy):
    def __init__(self, mode: str):
        super().__init__(name="C_Hybrid", mode=mode, allow_short=False)
        self._halted = False
        self._consecutive_losses = 0
        self._reduced_size_trades_remaining = 0

    def prepare_data(self, daily_df: pd.DataFrame, four_h_df: pd.DataFrame) -> pd.DataFrame:
        df = daily_df.copy()
        df["ema50_d"] = ind.ema(df["close"], 50)
        df["ema200_d"] = ind.ema(df["close"], 200)
        df["adx14_d"] = ind.adx(df["high"], df["low"], df["close"], 14)
        df["atr14_d"] = ind.atr(df["high"], df["low"], df["close"], 14)
        df["vol_sma20_d"] = ind.sma(df["volume"], 20).shift(1)
        return df

    def get_size_multiplier(self) -> float:
        return 0.5 if self._reduced_size_trades_remaining > 0 else 1.0

    def on_trade_closed(self, trade: Trade) -> None:
        # Decrement counter if active
        if self._reduced_size_trades_remaining > 0:
            self._reduced_size_trades_remaining -= 1
        # Track consecutive losses
        if trade.pnl_usd < 0:
            self._consecutive_losses += 1
            if (self._consecutive_losses >= 3
                    and self._reduced_size_trades_remaining == 0):
                self._reduced_size_trades_remaining = 5
                self._consecutive_losses = 0
        else:
            self._consecutive_losses = 0

    def evaluate(self, bar, position: Optional[Position],
                 equity_state: dict) -> Optional[Order]:
        # Warmup
        if any(pd.isna(getattr(bar, c, float("nan")))
               for c in ("ema200_d", "adx14_d", "atr14_d", "vol_sma20_d")):
            return None

        equity_now = equity_state["equity"]
        peak_equity = equity_state["peak_equity"]

        # Drawdown halt check (permanent)
        if not self._halted and peak_equity > 0:
            dd = 1.0 - equity_now / peak_equity
            if dd > 0.08:
                self._halted = True
                if position is not None:
                    return Order(action=OrderAction.CLOSE, reason="dd_halt")

        if self._halted:
            return None

        if position is not None:
            atr_now = bar.atr14_d
            atr_entry = position.atr_at_entry or atr_now

            if position.state == "INITIAL":
                # Break-even trigger: high >= entry + 1.5 × ATR_entry
                if bar.high >= position.entry_price + 1.5 * atr_entry:
                    position.state = "TRAILING"
                    position.current_stop = position.entry_price  # move to break-even
                    position.peak_high = bar.high

            if position.state == "TRAILING":
                peak = max(position.peak_high or position.entry_price, bar.high)
                position.peak_high = peak
                new_trail = peak - 2.0 * atr_now
                if new_trail > (position.current_stop or float("-inf")):
                    position.current_stop = new_trail

            # Exit signal (close-based): close < EMA50
            if bar.close < bar.ema50_d:
                return Order(action=OrderAction.CLOSE, reason="close_below_ema50")
            return None

        # Entry conditions
        if (bar.ema50_d > bar.ema200_d
                and bar.close > bar.ema50_d
                and bar.adx14_d > 25
                and bar.volume > 1.2 * bar.vol_sma20_d):
            stop_distance_pct = (2.0 * bar.atr14_d) / bar.close
            if stop_distance_pct <= 0:
                return None
            risk_amount = config.RISK_PER_TRADE * equity_now
            target_notional = risk_amount / stop_distance_pct
            max_notional = config.MAX_NOTIONAL_FRACTION[self.mode] * equity_now
            notional = min(target_notional, max_notional) * self.get_size_multiplier()
            if notional < 1.0:
                return None
            initial_stop = bar.close - 2.0 * bar.atr14_d
            return Order(
                action=OrderAction.OPEN_LONG,
                notional=notional,
                initial_stop=initial_stop,
                atr_at_signal=bar.atr14_d,
                reason="entry_long_hybrid",
            )
        return None
