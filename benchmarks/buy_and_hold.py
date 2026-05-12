"""
Buy & Hold benchmark. See SPEC.md §18.

Lump-sum buy at first available daily open of backtest period.
Hold through end of period. Fee + slippage applied on entry only (no exit costs —
the "hold forever" assumption).
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

import config
from backtest.costs import apply_entry_slippage, entry_fee_cost
from backtest.engine import BacktestResult
from backtest.position import ExitReason, Trade


def run_buy_and_hold(daily_df: pd.DataFrame,
                     backtest_start: datetime,
                     backtest_end: datetime) -> BacktestResult:
    bt_start_ts = pd.Timestamp(backtest_start, tz="UTC")
    bt_end_ts = pd.Timestamp(backtest_end, tz="UTC") + pd.Timedelta(days=1)
    df = daily_df.loc[(daily_df.index >= bt_start_ts) & (daily_df.index < bt_end_ts)].copy()
    if df.empty:
        raise ValueError("No daily bars in backtest range for Buy & Hold")

    initial_capital = config.INITIAL_CAPITAL
    first_bar = df.iloc[0]
    first_time = df.index[0]
    entry_price = apply_entry_slippage(first_bar["open"], "long")
    fee = entry_fee_cost(initial_capital, "spot_1x")
    cash_after_buy = initial_capital - fee
    units = cash_after_buy / entry_price

    # Equity curve: units × close at each bar (cash effectively all in BTC)
    equity_curve = units * df["close"]
    equity_curve.name = "equity"

    last_time = df.index[-1]
    last_close = float(df["close"].iloc[-1])
    final_equity = float(equity_curve.iloc[-1])

    # Synthetic trade: one buy, never sold (hold forever assumption). Treat
    # exit as "EOB" at last close but with NO exit cost.
    trade = Trade(
        trade_id=1,
        side="long",
        entry_time=first_time.to_pydatetime(),
        entry_price=entry_price,
        exit_time=last_time.to_pydatetime(),
        exit_price=last_close,                # no exit slippage applied
        units=units,
        notional_at_entry=units * entry_price,
        mode="spot_1x",
        pnl_usd=final_equity - initial_capital,
        pnl_pct=(final_equity - initial_capital) / initial_capital,
        fees_paid_usd=fee,
        funding_paid_usd=0.0,
        slippage_cost_usd=initial_capital * config.SLIPPAGE,
        exit_reason=ExitReason.END_OF_BACKTEST,
        equity_at_entry=initial_capital,
        equity_at_exit=final_equity,
        hold_bars=len(df) - 1,
        hold_days=(last_time - first_time).total_seconds() / 86400.0,
    )

    return BacktestResult(
        strategy_name="BuyAndHold",
        mode="spot_1x",
        equity_curve=equity_curve,
        trades=[trade],
        final_equity=final_equity,
        initial_equity=initial_capital,
        n_bars=len(df),
        n_funding_events=0,
        n_liquidations=0,
        n_circuit_breakers=0,
        total_fees_paid_usd=fee,
        total_funding_paid_usd=0.0,
        total_slippage_usd=initial_capital * config.SLIPPAGE,
    )
