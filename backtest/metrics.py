"""
Performance metrics. See SPEC.md §20.

All metrics computed on:
- equity_curve: pd.Series indexed by timestamp (UTC), values = equity USD
- trades: list[Trade] with PnL info
- period: (start, end) datetimes — slice the equity curve to this period

Returns a flat dict of metric names → values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

import config
from backtest.position import Trade


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b not in (0, 0.0) and not math.isnan(b) else default


def _slice_equity(equity: pd.Series,
                  start: Optional[datetime], end: Optional[datetime]) -> pd.Series:
    e = equity.copy()
    if start is not None:
        start_ts = pd.Timestamp(start, tz="UTC")
        e = e[e.index >= start_ts]
    if end is not None:
        end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
        e = e[e.index < end_ts]
    return e


def _slice_trades(trades: list[Trade],
                  start: Optional[datetime], end: Optional[datetime]) -> list[Trade]:
    """A trade is assigned to a period by its EXIT time."""
    out = []
    for t in trades:
        et = t.exit_time
        # Strip tz for comparison if trade is tz-aware but boundary is naive
        if hasattr(et, "tzinfo") and et.tzinfo is not None:
            et = et.replace(tzinfo=None)
        if start is not None and et < start:
            continue
        if end is not None:
            end_inclusive = datetime(end.year, end.month, end.day, 23, 59, 59)
            if et > end_inclusive:
                continue
        out.append(t)
    return out


def compute_drawdown(equity: pd.Series) -> tuple[pd.Series, float, float, int]:
    """
    Returns (drawdown_series, max_dd, avg_dd_during_dd_periods, max_dd_duration_days).
    drawdown values are negative (or 0 at peaks).
    """
    if len(equity) == 0:
        return pd.Series(dtype=float), 0.0, 0.0, 0
    running_peak = equity.cummax()
    dd = (equity - running_peak) / running_peak
    max_dd = float(dd.min())

    # Avg DD during periods in drawdown
    in_dd = dd < -1e-9
    avg_dd = float(dd[in_dd].mean()) if in_dd.any() else 0.0

    # Max drawdown duration: longest stretch from a peak to its recovery (or end of series)
    duration_days = 0
    if max_dd < 0 and len(equity) > 1:
        # Find the trough of max DD
        trough_idx = dd.idxmin()
        # Find the peak preceding the trough
        before_trough = equity.loc[:trough_idx]
        peak_value = before_trough.max()
        peak_idx = before_trough[before_trough == peak_value].index[0]
        # Recovery: first index after trough where equity >= peak_value
        after_trough = equity.loc[trough_idx:]
        recovery_mask = after_trough >= peak_value
        if recovery_mask.any():
            recovery_idx = after_trough[recovery_mask].index[0]
        else:
            recovery_idx = equity.index[-1]
        duration_days = int((recovery_idx - peak_idx).total_seconds() / 86400)

    return dd, max_dd, avg_dd, duration_days


def compute_sharpe(equity: pd.Series, annualization_days: int = 365,
                   risk_free_rate: float = 0.0) -> float:
    """Daily Sharpe annualized."""
    rets = _daily_returns(equity)
    if len(rets) < 2:
        return 0.0
    excess_daily = rets.mean() - (risk_free_rate / annualization_days)
    std_daily = rets.std(ddof=1)
    if std_daily == 0 or math.isnan(std_daily):
        return 0.0
    return float((excess_daily / std_daily) * math.sqrt(annualization_days))


def compute_sortino(equity: pd.Series, annualization_days: int = 365,
                    mar: float = 0.0) -> float:
    """Sortino: same as Sharpe but downside deviation as denominator."""
    rets = _daily_returns(equity)
    if len(rets) < 2:
        return 0.0
    downside = rets[rets < mar]
    if len(downside) < 1:
        return 0.0
    downside_std = downside.std(ddof=1)
    if downside_std == 0 or math.isnan(downside_std):
        return 0.0
    mean_excess = rets.mean() - mar
    return float((mean_excess / downside_std) * math.sqrt(annualization_days))


def _daily_returns(equity: pd.Series) -> pd.Series:
    """Resample equity to daily close-of-day, then compute pct_change."""
    if len(equity) == 0:
        return pd.Series(dtype=float)
    # If already daily (one value per day), just take pct_change
    daily = equity.resample("1D").last().dropna()
    return daily.pct_change().dropna()


def compute_cagr(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    initial = float(equity.iloc[0])
    final = float(equity.iloc[-1])
    if initial <= 0 or final <= 0:
        return 0.0
    span_days = (equity.index[-1] - equity.index[0]).total_seconds() / 86400.0
    years = span_days / 365.25
    if years <= 0:
        return 0.0
    return (final / initial) ** (1.0 / years) - 1.0


def compute_annual_returns(equity: pd.Series) -> dict[int, float]:
    """Per-calendar-year returns (using year start/end equity values)."""
    if len(equity) == 0:
        return {}
    out = {}
    for year in sorted({ts.year for ts in equity.index}):
        slice_year = equity[equity.index.year == year]
        if len(slice_year) < 2:
            continue
        start_eq = float(slice_year.iloc[0])
        end_eq = float(slice_year.iloc[-1])
        if start_eq > 0:
            out[year] = (end_eq / start_eq) - 1.0
    return out


def compute_trade_stats(trades: list[Trade]) -> dict:
    n = len(trades)
    if n == 0:
        return {
            "total_trades": 0, "win_rate_pct": 0.0,
            "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
            "profit_factor": 0.0,
            "best_trade_pct": 0.0, "worst_trade_pct": 0.0,
            "avg_hold_days": 0.0,
        }
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd < 0]
    sum_wins = sum(t.pnl_usd for t in wins)
    sum_losses = abs(sum(t.pnl_usd for t in losses))

    return {
        "total_trades": n,
        "win_rate_pct": (len(wins) / n) * 100.0,
        "avg_win_pct": (sum(t.pnl_pct for t in wins) / len(wins) * 100.0) if wins else 0.0,
        "avg_loss_pct": (sum(t.pnl_pct for t in losses) / len(losses) * 100.0) if losses else 0.0,
        "profit_factor": _safe_div(sum_wins, sum_losses, default=float("inf") if sum_wins > 0 else 0.0),
        "best_trade_pct": max((t.pnl_pct for t in trades), default=0.0) * 100.0,
        "worst_trade_pct": min((t.pnl_pct for t in trades), default=0.0) * 100.0,
        "avg_hold_days": sum(t.hold_days for t in trades) / n,
    }


def compute_exposure(equity: pd.Series, trades: list[Trade]) -> dict:
    """
    time_in_market_pct: fraction of bars during which a position was open.
    avg_position_size_pct: mean of (notional / equity_at_entry) across trades.
    """
    if not trades or len(equity) == 0:
        return {"time_in_market_pct": 0.0, "avg_position_size_pct": 0.0}

    # For each timestamp in equity, check if it falls within any (entry, exit) window
    total_bars = len(equity)
    in_market_bars = 0
    # Sort trades by entry
    sorted_trades = sorted(trades, key=lambda t: t.entry_time)
    trade_idx = 0
    n_trades = len(sorted_trades)
    for ts in equity.index:
        ts_py = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        # advance trade_idx past trades that ended before ts
        while trade_idx < n_trades and sorted_trades[trade_idx].exit_time < ts_py:
            trade_idx += 1
        if trade_idx < n_trades:
            t = sorted_trades[trade_idx]
            if t.entry_time <= ts_py <= t.exit_time:
                in_market_bars += 1

    avg_size = sum(
        t.notional_at_entry / t.equity_at_entry
        for t in trades if t.equity_at_entry > 0
    ) / len(trades) * 100.0

    return {
        "time_in_market_pct": (in_market_bars / total_bars) * 100.0,
        "avg_position_size_pct": avg_size,
    }


def compute_cost_stats(trades: list[Trade]) -> dict:
    if not trades:
        return {
            "total_fees_usd": 0.0, "total_slippage_usd": 0.0,
            "total_funding_usd": 0.0,
        }
    return {
        "total_fees_usd": sum(t.fees_paid_usd for t in trades),
        "total_slippage_usd": sum(t.slippage_cost_usd for t in trades),
        "total_funding_usd": sum(t.funding_paid_usd for t in trades),
    }


def compute_all_metrics(equity_curve: pd.Series, trades: list[Trade],
                        initial_equity: float = config.INITIAL_CAPITAL,
                        period_start: Optional[datetime] = None,
                        period_end: Optional[datetime] = None) -> dict:
    """
    Master metric aggregator. Slices equity_curve and trades to [start, end] if provided.
    """
    eq = _slice_equity(equity_curve, period_start, period_end)
    if len(eq) == 0:
        return {"empty": True}

    # For sliced series, "initial" is the slice start, "final" is slice end
    period_initial = float(eq.iloc[0])
    period_final = float(eq.iloc[-1])

    period_trades = _slice_trades(trades, period_start, period_end)

    dd_series, max_dd, avg_dd, dd_duration = compute_drawdown(eq)
    annual_rets = compute_annual_returns(eq)
    trade_stats = compute_trade_stats(period_trades)
    exposure = compute_exposure(eq, period_trades)
    cost_stats = compute_cost_stats(period_trades)

    total_return = (period_final / period_initial) - 1.0 if period_initial > 0 else 0.0
    cagr = compute_cagr(eq)
    sharpe = compute_sharpe(eq, annualization_days=config.ANNUALIZATION_DAYS,
                            risk_free_rate=config.RISK_FREE_RATE)
    sortino = compute_sortino(eq, annualization_days=config.ANNUALIZATION_DAYS)
    calmar = _safe_div(cagr, abs(max_dd), default=0.0)

    # Gross return (without trade costs) for net_vs_gross
    total_costs = (cost_stats["total_fees_usd"] +
                   cost_stats["total_slippage_usd"] +
                   abs(cost_stats["total_funding_usd"]))
    gross_final = period_final + total_costs
    gross_return = (gross_final / period_initial) - 1.0 if period_initial > 0 else 0.0
    net_vs_gross = total_return - gross_return  # negative = cost dragged us down

    out = {
        "period_initial_usd": period_initial,
        "period_final_usd": period_final,
        "total_return_pct": total_return * 100.0,
        "cagr_pct": cagr * 100.0,
        "max_dd_pct": max_dd * 100.0,
        "avg_dd_pct": avg_dd * 100.0,
        "dd_duration_days": dd_duration,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "net_vs_gross_pct": net_vs_gross * 100.0,
        **trade_stats,
        **exposure,
        **cost_stats,
    }
    for year in (2020, 2021, 2022, 2023, 2024):
        out[f"annual_{year}_pct"] = (annual_rets.get(year, 0.0)) * 100.0
    return out
