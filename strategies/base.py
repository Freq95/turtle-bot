"""
BaseStrategy ABC. All strategies inherit from this and the engine drives them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

from backtest.position import Order, Position, Trade


class BaseStrategy(ABC):
    """
    Abstract strategy interface.

    Lifecycle:
    1. prepare_data(daily_df, four_h_df) — once at start; returns DataFrame the engine iterates.
       Strategy attaches indicators as columns. For mixed-timeframe (A), this method merges
       daily indicators into the 4h DataFrame using anti-look-ahead alignment.
    2. evaluate(bar, position, equity_state) — called at close of each bar.
       Returns Order or None. May mutate position state (trailing stop, peak, etc.).
    3. on_trade_closed(trade) — hook after each closed trade; updates strategy-internal state
       like consecutive loss trackers.

    The engine handles: bar iteration, pending order execution at next open, funding, liquidation,
    stop fills, equity tracking, mark-to-market, trade recording.
    """

    def __init__(self, name: str, mode: str, allow_short: bool = False):
        self.name = name
        self.mode = mode
        self.allow_short = allow_short

    @abstractmethod
    def prepare_data(self, daily_df: pd.DataFrame, four_h_df: pd.DataFrame) -> pd.DataFrame:
        """
        Returns the DataFrame the engine iterates over.
        For daily-only strategies: return daily_df (with indicators attached).
        For mixed-TF strategies (A): return 4h_df with daily indicators merged in.
        """
        ...

    @abstractmethod
    def evaluate(self, bar, position: Optional[Position],
                 equity_state: dict) -> Optional[Order]:
        """
        Called at close of each bar.

        Args:
            bar: namedtuple-like with .Index (timestamp), .open, .high, .low, .close, .volume,
                 and indicator columns specific to the strategy.
            position: current open Position or None.
            equity_state: dict with keys {
                'equity': current mark-to-market equity,
                'cash': current cash balance,
                'daily_open_equity': equity at 00:00 UTC of current day,
                'weekly_open_equity': equity at 00:00 UTC of last Monday,
                'peak_equity': all-time peak equity,
            }

        Returns:
            Order to execute at next bar's open, or None.
            Strategy can mutate `position` in place (e.g., update trailing stop).
        """
        ...

    def on_trade_closed(self, trade: Trade) -> None:
        """Called after engine closes a trade. Default: no-op. Override for state updates."""
        pass

    # ============================================================
    # Helpers shared across strategies
    # ============================================================

    def get_size_multiplier(self) -> float:
        """For consecutive-loss size reducer. Default 1.0. A and C override."""
        return 1.0
