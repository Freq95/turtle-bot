"""
Custom backtest engine. Iterates bars, manages position lifecycle, applies costs.

See SPEC.md §7 (Execution Model), §8-10 (Costs), §11-12 (Sizing & Equity).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

import config
from backtest.costs import (
    apply_entry_slippage, apply_exit_slippage,
    compute_funding_charge, entry_fee_cost, exit_fee_cost,
    is_liquidated, liquidation_fill_price, liquidation_price,
    stop_fill_price,
)
from backtest.position import ExitReason, Order, OrderAction, Position, Trade
from strategies.base import BaseStrategy

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    strategy_name: str
    mode: str
    equity_curve: pd.Series           # indexed by bar timestamp
    trades: list[Trade]
    final_equity: float
    initial_equity: float
    n_bars: int
    n_funding_events: int
    n_liquidations: int
    n_circuit_breakers: int
    total_fees_paid_usd: float
    total_funding_paid_usd: float     # positive = paid net, negative = received net
    total_slippage_usd: float
    halted_early: bool = False
    halt_reason: str = ""


def _mark_to_market(cash: float, position: Optional[Position], price: float) -> float:
    if position is None:
        return cash
    if position.is_spot:
        return cash + position.units * price
    # Futures: cash includes margin already; equity = cash + unrealized PnL
    return cash + position.unrealized_pnl(price)


def _open_position(action: OrderAction, order: Order, bar, cash: float,
                   mode: str, trade_id: int, leverage: float) -> tuple[Position, float, float, float]:
    """
    Execute an OPEN order at this bar's open. Returns (position, new_cash, fees_paid, slippage_paid).
    """
    side = "long" if action == OrderAction.OPEN_LONG else "short"
    exec_price = apply_entry_slippage(bar.open, side)
    notional = order.notional
    units = notional / exec_price

    fees = entry_fee_cost(notional, mode)
    slippage_cost = notional * config.SLIPPAGE  # informational only — already in exec_price

    if mode == "spot_1x":
        # Spot: cash funds the full position
        if side == "short":
            raise ValueError("Spot does not support short")
        cash_after = cash - notional - fees
    else:
        # Futures: only fees deducted from cash (margin tracked implicitly)
        cash_after = cash - fees

    liq_price = None
    if mode == "futures_2x":
        liq_price = liquidation_price(exec_price, side, leverage)

    pos = Position(
        trade_id=trade_id,
        side=side,
        units=units,
        entry_price=exec_price,
        entry_time=bar.Index.to_pydatetime() if hasattr(bar.Index, "to_pydatetime") else bar.Index,
        mode=mode,
        notional_at_entry=units * exec_price,
        current_stop=order.initial_stop if order.initial_stop > 0 else None,
        state="INITIAL",
        peak_high=bar.high if side == "long" else None,
        trough_low=bar.low if side == "short" else None,
        peak_close=bar.close if side == "long" else None,
        atr_at_entry=order.atr_at_signal if order.atr_at_signal > 0 else None,
        liquidation_price=liq_price,
    )
    return pos, cash_after, fees, slippage_cost


def _close_position(position: Position, exit_price: float, exit_time: datetime,
                    cash: float, reason: ExitReason, entry_equity: float,
                    fees_paid_in: float, funding_paid_in: float,
                    slippage_paid_in: float, bars_held: int) -> tuple[float, Trade, float, float]:
    """
    Close a position at given exit_price (already slippage-adjusted).
    Returns (new_cash, Trade, exit_fees, exit_slippage).
    """
    units = position.units
    fees = exit_fee_cost(units * exit_price, position.mode)
    slippage_cost = units * exit_price * config.SLIPPAGE

    if position.is_spot:
        # Spot: receive proceeds, pay fee
        cash_after = cash + units * exit_price - fees
    else:
        # Futures: realize PnL, pay fee
        pnl = (exit_price - position.entry_price) * units * position.side_sign
        cash_after = cash + pnl - fees

    final_equity = cash_after
    pnl_usd = final_equity - entry_equity
    pnl_pct = pnl_usd / entry_equity if entry_equity > 0 else 0.0

    hold_seconds = (exit_time - position.entry_time).total_seconds()
    hold_days = hold_seconds / 86400.0

    trade = Trade(
        trade_id=position.trade_id,
        side=position.side,
        entry_time=position.entry_time,
        entry_price=position.entry_price,
        exit_time=exit_time,
        exit_price=exit_price,
        units=units,
        notional_at_entry=position.notional_at_entry,
        mode=position.mode,
        pnl_usd=pnl_usd,
        pnl_pct=pnl_pct,
        fees_paid_usd=fees_paid_in + fees,
        funding_paid_usd=funding_paid_in,
        slippage_cost_usd=slippage_paid_in + slippage_cost,
        exit_reason=reason,
        equity_at_entry=entry_equity,
        equity_at_exit=final_equity,
        hold_bars=bars_held,
        hold_days=hold_days,
    )
    return cash_after, trade, fees, slippage_cost


def run_backtest(strategy: BaseStrategy,
                 daily_df: pd.DataFrame,
                 four_h_df: pd.DataFrame,
                 mode: str,
                 backtest_start: datetime,
                 backtest_end: datetime) -> BacktestResult:
    """
    Main backtest entry. Returns BacktestResult.
    Bars before backtest_start are warmup (no trades). Bars after backtest_end stop iteration.
    """
    bars_df = strategy.prepare_data(daily_df, four_h_df)

    # Filter to range — keep all data from start of bars_df through backtest_end
    # (warmup is at the start of bars_df, before backtest_start)
    bt_start_ts = pd.Timestamp(backtest_start, tz="UTC")
    bt_end_ts = pd.Timestamp(backtest_end, tz="UTC") + pd.Timedelta(days=1)  # inclusive end day
    bars_df = bars_df.loc[bars_df.index < bt_end_ts]

    leverage = config.get_leverage_for_mode(mode)

    cash = config.INITIAL_CAPITAL
    initial_equity = cash
    position: Optional[Position] = None
    pending_order: Optional[Order] = None
    trades: list[Trade] = []

    # Per-trade tracking (carries across the open trade's life)
    trade_fees_so_far = 0.0
    trade_funding_so_far = 0.0
    trade_slippage_so_far = 0.0
    trade_entry_equity = 0.0
    trade_open_bar_idx = 0

    # Equity series
    equity_records: list[tuple[pd.Timestamp, float]] = []

    # Cumulative cost tracking
    total_fees = 0.0
    total_funding = 0.0
    total_slippage = 0.0

    # Equity trackers
    daily_open_equity = cash
    weekly_open_equity = cash
    peak_equity = cash

    # Event counters
    n_funding = 0
    n_liquidations = 0
    n_circuit_breakers = 0

    last_daily_date: Optional[datetime] = None
    last_weekly_monday: Optional[datetime] = None

    for bar_idx, bar in enumerate(bars_df.itertuples(index=True, name="Bar")):
        ts = bar.Index  # pandas Timestamp (UTC)

        # ---- Step 1a: Update daily/weekly equity trackers at 00:00 UTC ----
        if ts.hour == 0 and ts.minute == 0:
            mark_now = _mark_to_market(cash, position, bar.open)
            if last_daily_date != ts.date():
                daily_open_equity = mark_now
                last_daily_date = ts.date()
            if ts.weekday() == 0 and (last_weekly_monday is None or last_weekly_monday != ts.date()):
                weekly_open_equity = mark_now
                last_weekly_monday = ts.date()

        # ---- Step 1b: Apply funding (futures + 00:00 UTC + position open) ----
        if position is not None and position.is_futures and ts.hour == 0 and ts.minute == 0:
            funding = compute_funding_charge(position.notional_at_entry, position.side)
            cash -= funding
            trade_funding_so_far += funding
            total_funding += funding
            n_funding += 1

        # ---- Step 1c: Check liquidation (futures 2x + position open) ----
        if position is not None and position.liquidation_price is not None:
            if is_liquidated(position.side, bar.open, bar.low, bar.high, position.liquidation_price):
                fill = liquidation_fill_price(bar.open, position.liquidation_price, position.side)
                bars_held = bar_idx - trade_open_bar_idx
                cash, trade, _, exit_slip = _close_position(
                    position, fill, ts.to_pydatetime(), cash, ExitReason.LIQUIDATION,
                    trade_entry_equity, trade_fees_so_far, trade_funding_so_far,
                    trade_slippage_so_far, bars_held,
                )
                trades.append(trade)
                total_fees += trade.fees_paid_usd - trade_fees_so_far
                total_slippage += exit_slip
                strategy.on_trade_closed(trade)
                position = None
                pending_order = None
                n_liquidations += 1

        # ---- Step 1d: Check stop fill intra-bar ----
        if position is not None and position.current_stop is not None:
            stop_hit = (
                (position.is_long and bar.low <= position.current_stop) or
                (position.is_short and bar.high >= position.current_stop)
            )
            if stop_hit:
                fill = stop_fill_price(bar.open, position.current_stop, position.side)
                bars_held = bar_idx - trade_open_bar_idx
                # Determine reason: TRAILING if state advanced past INITIAL, else STOP
                reason = ExitReason.TRAILING if position.state != "INITIAL" else ExitReason.STOP
                cash, trade, exit_fee, exit_slip = _close_position(
                    position, fill, ts.to_pydatetime(), cash, reason,
                    trade_entry_equity, trade_fees_so_far, trade_funding_so_far,
                    trade_slippage_so_far, bars_held,
                )
                trades.append(trade)
                total_fees += exit_fee
                total_slippage += exit_slip
                strategy.on_trade_closed(trade)
                position = None
                pending_order = None

        # ---- Step 2: Execute pending order at this bar's open ----
        if pending_order is not None:
            if pending_order.action == OrderAction.CLOSE:
                if position is not None:
                    fill = apply_exit_slippage(bar.open, position.side)
                    bars_held = bar_idx - trade_open_bar_idx
                    cash, trade, exit_fee, exit_slip = _close_position(
                        position, fill, ts.to_pydatetime(), cash,
                        ExitReason(pending_order.reason) if pending_order.reason in [r.value for r in ExitReason] else ExitReason.SIGNAL,
                        trade_entry_equity, trade_fees_so_far, trade_funding_so_far,
                        trade_slippage_so_far, bars_held,
                    )
                    trades.append(trade)
                    total_fees += exit_fee
                    total_slippage += exit_slip
                    strategy.on_trade_closed(trade)
                    position = None
                    if trade.exit_reason == ExitReason.CIRCUIT_BREAKER:
                        n_circuit_breakers += 1
                # If position was None (already closed by stop/liq), discard order silently
            elif pending_order.action in (OrderAction.OPEN_LONG, OrderAction.OPEN_SHORT):
                if position is None:
                    # Check cash sufficiency for spot
                    if mode == "spot_1x":
                        cost = pending_order.notional * (1 + config.SLIPPAGE) + entry_fee_cost(pending_order.notional, mode)
                        if cost > cash:
                            log.debug("Skipping OPEN at %s: insufficient cash (need %.2f, have %.2f)",
                                      ts, cost, cash)
                            pending_order = None
                            # Fall through; nothing opens
                    if pending_order is not None and pending_order.notional > 0:
                        trade_id = len(trades) + 1 if position is None else trades[-1].trade_id + 1
                        # Recompute trade_id properly
                        trade_id = (trades[-1].trade_id + 1) if trades else 1
                        position, cash, entry_fee, entry_slip = _open_position(
                            pending_order.action, pending_order, bar, cash, mode, trade_id, leverage,
                        )
                        trade_entry_equity = _mark_to_market(cash, position, bar.open)
                        trade_fees_so_far = entry_fee
                        trade_funding_so_far = 0.0
                        trade_slippage_so_far = entry_slip
                        trade_open_bar_idx = bar_idx
                        total_fees += entry_fee
                        total_slippage += entry_slip
                else:
                    log.warning("Pending OPEN at %s but position already open; ignoring",
                                ts)
            pending_order = None

        # ---- Step 3: Mark to market at bar close ----
        equity_now = _mark_to_market(cash, position, bar.close)
        if equity_now > peak_equity:
            peak_equity = equity_now

        # Equity sanity: if equity <= 0, halt strategy (rare but possible with futures 2x)
        if equity_now <= 0:
            log.warning("Equity ≤ 0 at %s — halting strategy", ts)
            if position is not None:
                # Force close at bar close
                fill = apply_exit_slippage(bar.close, position.side)
                bars_held = bar_idx - trade_open_bar_idx
                cash, trade, _, _ = _close_position(
                    position, fill, ts.to_pydatetime(), cash, ExitReason.LIQUIDATION,
                    trade_entry_equity, trade_fees_so_far, trade_funding_so_far,
                    trade_slippage_so_far, bars_held,
                )
                trades.append(trade)
                strategy.on_trade_closed(trade)
                position = None
            equity_records.append((ts, max(equity_now, 0.0)))
            # Continue but with cash effectively zero
            cash = max(cash, 0.0)
            return BacktestResult(
                strategy_name=strategy.name, mode=mode,
                equity_curve=pd.Series(dict(equity_records)),
                trades=trades,
                final_equity=cash, initial_equity=initial_equity,
                n_bars=bar_idx + 1,
                n_funding_events=n_funding, n_liquidations=n_liquidations,
                n_circuit_breakers=n_circuit_breakers,
                total_fees_paid_usd=total_fees, total_funding_paid_usd=total_funding,
                total_slippage_usd=total_slippage,
                halted_early=True, halt_reason="equity≤0",
            )

        equity_records.append((ts, equity_now))

        # ---- Step 4: Strategy evaluation at bar close ----
        # Only evaluate if we're in the backtest range (not warmup)
        if ts >= bt_start_ts:
            equity_state = {
                "equity": equity_now,
                "cash": cash,
                "daily_open_equity": daily_open_equity,
                "weekly_open_equity": weekly_open_equity,
                "peak_equity": peak_equity,
                "bar_index": bar_idx,
            }
            try:
                order = strategy.evaluate(bar, position, equity_state)
            except Exception as exc:
                log.exception("Strategy %s raised at bar %s: %s", strategy.name, ts, exc)
                order = None

            if order is not None:
                # Validate
                if order.action in (OrderAction.OPEN_LONG, OrderAction.OPEN_SHORT) and position is not None:
                    log.debug("Strategy returned OPEN while position exists at %s — ignoring", ts)
                elif order.action == OrderAction.CLOSE and position is None:
                    log.debug("Strategy returned CLOSE while no position at %s — ignoring", ts)
                else:
                    pending_order = order
                    if order.action == OrderAction.CLOSE and order.reason == ExitReason.CIRCUIT_BREAKER.value:
                        # Counter incremented when actually closed in next bar
                        pass

    # ---- End of backtest: force-close any open position ----
    if position is not None:
        last_bar = bars_df.iloc[-1]
        last_ts = bars_df.index[-1]
        fill = apply_exit_slippage(last_bar["close"], position.side)
        bars_held = len(bars_df) - 1 - trade_open_bar_idx
        cash, trade, exit_fee, exit_slip = _close_position(
            position, fill, last_ts.to_pydatetime(), cash, ExitReason.END_OF_BACKTEST,
            trade_entry_equity, trade_fees_so_far, trade_funding_so_far,
            trade_slippage_so_far, bars_held,
        )
        trades.append(trade)
        total_fees += exit_fee
        total_slippage += exit_slip
        strategy.on_trade_closed(trade)
        position = None
        equity_records[-1] = (equity_records[-1][0], cash)

    equity_series = pd.Series(
        [v for _, v in equity_records],
        index=pd.DatetimeIndex([t for t, _ in equity_records], tz="UTC"),
    )
    final_equity = float(equity_series.iloc[-1]) if len(equity_series) > 0 else initial_equity

    return BacktestResult(
        strategy_name=strategy.name,
        mode=mode,
        equity_curve=equity_series,
        trades=trades,
        final_equity=final_equity,
        initial_equity=initial_equity,
        n_bars=len(bars_df),
        n_funding_events=n_funding,
        n_liquidations=n_liquidations,
        n_circuit_breakers=n_circuit_breakers,
        total_fees_paid_usd=total_fees,
        total_funding_paid_usd=total_funding,
        total_slippage_usd=total_slippage,
    )
