"""
Strategy D-StopCapped — D-Primary rules + hard 1% account-loss stop per trade.

Inherits D-Primary (55/20 Donchian + vol-targeting) and overrides evaluate()
to attach a hard stop_loss that caps any single trade's loss at 1% of equity.

The stop distance is computed at signal time from position size: with vol-targeting
producing ~30-50% positions, this translates to roughly 2-3% adverse move from entry.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from backtest.position import Order, OrderAction, Position
from strategies.strategy_d import StrategyD


class StrategyDStopCapped(StrategyD):
    MAX_LOSS_FRACTION = 0.01  # max 1% of equity loss per trade

    def __init__(self, mode: str):
        super().__init__(mode, n_entry=55, n_exit=20, variant_label="StopCapped")

    def evaluate(self, bar, position: Optional[Position],
                 equity_state: dict) -> Optional[Order]:
        order = super().evaluate(bar, position, equity_state)
        if order is None:
            return None
        if order.action not in (OrderAction.OPEN_LONG, OrderAction.OPEN_SHORT):
            return order
        if order.notional <= 0:
            return order

        equity = equity_state["equity"]
        max_loss_usd = self.MAX_LOSS_FRACTION * equity
        units = order.notional / bar.close  # approximate (entry fills next bar open)
        if units <= 0:
            return order
        stop_distance_per_unit = max_loss_usd / units

        if order.action == OrderAction.OPEN_LONG:
            order.initial_stop = bar.close - stop_distance_per_unit
        else:  # short
            order.initial_stop = bar.close + stop_distance_per_unit
        return order
