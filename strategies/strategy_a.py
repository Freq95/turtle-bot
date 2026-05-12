"""
Strategy A — Complex Multi-Layer (Mixed Daily+4h).
See SPEC.md §13.

The most complex strategy: 6 layers, mixed timeframes, circuit breakers,
state machine, consecutive-loss tracker, long+short on futures.

Implementation:
- Iterates over 4h bars.
- Daily indicators merged in via merge_asof with shift to enforce anti-look-ahead.
- At each 4h bar's close, evaluates: circuit breakers → regime → 4h entry → state machine.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

import config
from backtest import indicators as ind
from backtest.position import Order, OrderAction, Position, Trade
from strategies.base import BaseStrategy


def _merge_daily_into_4h(four_h_df: pd.DataFrame,
                         daily_df: pd.DataFrame,
                         daily_cols: list[str]) -> pd.DataFrame:
    """
    Merge daily indicator columns into 4h DataFrame with anti-look-ahead alignment.

    For 4h bar at open_time t (closes at t+4h), the most recent COMPLETED daily
    is the one whose close_time <= t+4h. We shift daily index forward by 1 day
    so the row index equals close_time, then shift 4h to close_time, then merge_asof
    backward.
    """
    daily_shifted = daily_df[daily_cols].copy()
    daily_shifted.index = daily_shifted.index + pd.Timedelta(days=1)
    # Rename columns to make merge unambiguous
    daily_shifted = daily_shifted.add_suffix("__merged")

    four_h_close = four_h_df.copy()
    four_h_close.index = four_h_close.index + pd.Timedelta(hours=4)

    merged = pd.merge_asof(
        four_h_close.sort_index(),
        daily_shifted.sort_index(),
        left_index=True,
        right_index=True,
        direction="backward",
    )
    # Restore original 4h open-time index
    merged.index = merged.index - pd.Timedelta(hours=4)
    # Strip suffix
    rename_map = {c + "__merged": c for c in daily_cols}
    merged = merged.rename(columns=rename_map)
    return merged


class StrategyA(BaseStrategy):
    def __init__(self, mode: str):
        allow_short = mode.startswith("futures_")
        super().__init__(name="A_ComplexMultiLayer", mode=mode, allow_short=allow_short)
        # Bot-off state
        self._bot_off_permanent = False
        self._bot_off_until: Optional[pd.Timestamp] = None
        # Loss tracker
        self._consecutive_losses = 0
        self._reduced_size_trades_remaining = 0
        # Transition tracker for long↔short
        self._last_close_time: Optional[pd.Timestamp] = None
        self._last_close_side: Optional[str] = None

    # ============================================================
    # Indicator preparation
    # ============================================================

    def prepare_data(self, daily_df: pd.DataFrame, four_h_df: pd.DataFrame) -> pd.DataFrame:
        # Daily indicators
        d = daily_df.copy()
        d["ema50_d"] = ind.ema(d["close"], 50)
        d["ema200_d"] = ind.ema(d["close"], 200)
        d["adx14_d"] = ind.adx(d["high"], d["low"], d["close"], 14)
        d["close_d"] = d["close"]  # for regime checks at 4h bar level

        daily_cols = ["ema50_d", "ema200_d", "adx14_d", "close_d"]

        # 4h indicators
        h = four_h_df.copy()
        h["ema21_4h"] = ind.ema(h["close"], 21)
        h["rsi14_4h"] = ind.rsi(h["close"], 14)
        h["atr14_4h"] = ind.atr(h["high"], h["low"], h["close"], 14)
        h["vol_sma20_4h"] = ind.sma(h["volume"], 20).shift(1)

        # Merge daily into 4h
        merged = _merge_daily_into_4h(h, d, daily_cols)
        return merged

    # ============================================================
    # State helpers
    # ============================================================

    def get_size_multiplier(self) -> float:
        return 0.5 if self._reduced_size_trades_remaining > 0 else 1.0

    def on_trade_closed(self, trade: Trade) -> None:
        if self._reduced_size_trades_remaining > 0:
            self._reduced_size_trades_remaining -= 1
        if trade.pnl_usd < 0:
            self._consecutive_losses += 1
            if (self._consecutive_losses >= 3
                    and self._reduced_size_trades_remaining == 0):
                self._reduced_size_trades_remaining = 5
                self._consecutive_losses = 0
        else:
            self._consecutive_losses = 0
        # Track last-close for transition rule
        self._last_close_time = pd.Timestamp(trade.exit_time)
        self._last_close_side = trade.side

    def _can_open_new(self, ts: pd.Timestamp) -> bool:
        if self._bot_off_permanent:
            return False
        if self._bot_off_until is not None and ts < self._bot_off_until:
            return False
        return True

    def _can_take_side(self, side: str, ts: pd.Timestamp) -> bool:
        """Transition rule: need ≥1 daily bar gap when flipping direction."""
        if self._last_close_time is None or self._last_close_side is None:
            return True
        if self._last_close_side == side:
            return True
        # Opposite direction — require ≥1 day gap
        return (ts - self._last_close_time) >= pd.Timedelta(days=1)

    # ============================================================
    # Main evaluate
    # ============================================================

    def evaluate(self, bar, position: Optional[Position],
                 equity_state: dict) -> Optional[Order]:
        ts: pd.Timestamp = bar.Index

        # Warmup check
        required_cols = ("ema50_d", "ema200_d", "adx14_d", "close_d",
                         "ema21_4h", "rsi14_4h", "atr14_4h", "vol_sma20_4h")
        for c in required_cols:
            if pd.isna(getattr(bar, c, float("nan"))):
                return None

        equity = equity_state["equity"]
        daily_open = equity_state["daily_open_equity"]
        weekly_open = equity_state["weekly_open_equity"]
        peak_equity = equity_state["peak_equity"]

        # ---- Circuit breakers ----
        if not self._bot_off_permanent:
            # Drawdown check (permanent)
            if peak_equity > 0 and (1.0 - equity / peak_equity) > 0.08:
                self._bot_off_permanent = True
                if position is not None:
                    return Order(action=OrderAction.CLOSE, reason="circuit")
            # Daily loss check
            elif daily_open > 0 and (equity / daily_open - 1.0) < -0.03:
                next_day = (ts.normalize() + pd.Timedelta(days=1)).tz_convert("UTC")
                self._bot_off_until = next_day
                if position is not None:
                    return Order(action=OrderAction.CLOSE, reason="circuit")
            # Weekly loss check
            elif weekly_open > 0 and (equity / weekly_open - 1.0) < -0.05:
                days_ahead = (0 - ts.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7
                next_mon = (ts.normalize() + pd.Timedelta(days=days_ahead)).tz_convert("UTC")
                self._bot_off_until = next_mon
                if position is not None:
                    return Order(action=OrderAction.CLOSE, reason="circuit")

        if self._bot_off_permanent:
            return None
        if self._bot_off_until is not None and ts < self._bot_off_until:
            return None

        # ---- Position management ----
        if position is not None:
            atr_now = bar.atr14_4h
            atr_entry = position.atr_at_entry or atr_now

            if position.is_long:
                # State: INITIAL → BREAKEVEN_TRAIL via 1.5×ATR profit
                if position.state == "INITIAL":
                    if bar.high >= position.entry_price + 1.5 * atr_entry:
                        position.state = "BREAKEVEN_TRAIL"
                        position.current_stop = position.entry_price  # break-even
                        position.peak_high = bar.high
                if position.state == "BREAKEVEN_TRAIL":
                    peak = max(position.peak_high or position.entry_price, bar.high)
                    position.peak_high = peak
                    new_trail = peak - 3.0 * atr_now
                    if new_trail > (position.current_stop or float("-inf")):
                        position.current_stop = new_trail
                # Hard exit: daily regime flipped (close_d < ema50_d while long)
                if bar.close_d < bar.ema50_d:
                    return Order(action=OrderAction.CLOSE, reason="regime_flip")
            else:  # short
                if position.state == "INITIAL":
                    if bar.low <= position.entry_price - 1.5 * atr_entry:
                        position.state = "BREAKEVEN_TRAIL"
                        position.current_stop = position.entry_price
                        position.trough_low = bar.low
                if position.state == "BREAKEVEN_TRAIL":
                    trough = min(position.trough_low or position.entry_price, bar.low)
                    position.trough_low = trough
                    new_trail = trough + 3.0 * atr_now
                    if new_trail < (position.current_stop or float("inf")):
                        position.current_stop = new_trail
                if bar.close_d > bar.ema50_d:
                    return Order(action=OrderAction.CLOSE, reason="regime_flip")
            return None

        # ---- Entry evaluation (no position) ----
        adx_active = bar.adx14_d >= 25
        if not adx_active:
            return None

        long_bias = (bar.ema50_d > bar.ema200_d) and (bar.close_d > bar.ema50_d)
        short_bias = (bar.ema50_d < bar.ema200_d) and (bar.close_d < bar.ema50_d)

        # 4h pullback: bar's low <= EMA21 <= bar's high
        pullback = (bar.low <= bar.ema21_4h <= bar.high)
        volume_ok = bar.volume > bar.vol_sma20_4h

        if long_bias and pullback and volume_ok and bar.close > bar.open:
            if 40.0 <= bar.rsi14_4h <= 55.0 and self._can_take_side("long", ts):
                return self._make_entry_order(bar, equity, "long")

        if (self.allow_short and short_bias and pullback and volume_ok
                and bar.close < bar.open):
            if 45.0 <= bar.rsi14_4h <= 60.0 and self._can_take_side("short", ts):
                return self._make_entry_order(bar, equity, "short")

        return None

    def _make_entry_order(self, bar, equity: float, side: str) -> Optional[Order]:
        atr = bar.atr14_4h
        stop_distance_pct = (2.0 * atr) / bar.close
        if stop_distance_pct <= 0:
            return None
        risk_amount = config.RISK_PER_TRADE * equity
        target_notional = risk_amount / stop_distance_pct
        max_notional = config.MAX_NOTIONAL_FRACTION[self.mode] * equity
        notional = min(target_notional, max_notional) * self.get_size_multiplier()
        if notional < 1.0:
            return None
        if side == "long":
            initial_stop = bar.close - 2.0 * atr
            return Order(
                action=OrderAction.OPEN_LONG,
                notional=notional,
                initial_stop=initial_stop,
                atr_at_signal=atr,
                reason="entry_long_complex",
            )
        else:
            initial_stop = bar.close + 2.0 * atr
            return Order(
                action=OrderAction.OPEN_SHORT,
                notional=notional,
                initial_stop=initial_stop,
                atr_at_signal=atr,
                reason="entry_short_complex",
            )
