"""
Technical indicators implemented inline with pandas/numpy.

Replaces pandas-ta dependency (which had numpy 2.x conflict with pandas 2.2).
All functions return pd.Series aligned with input index.

Wilder's smoothing (RMA) approximated as `ewm(alpha=1/N, adjust=False).mean()`.
This is the standard simplification used by most TA libraries.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ============================================================
# Moving averages
# ============================================================

def sma(s: pd.Series, n: int) -> pd.Series:
    """Simple moving average over the last N values."""
    return s.rolling(window=n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    """Exponential moving average. adjust=False matches traditional TA EMA."""
    return s.ewm(span=n, adjust=False).mean()


def rma(s: pd.Series, n: int) -> pd.Series:
    """Wilder's RMA — exponential smoothing with alpha=1/N."""
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


# ============================================================
# Volatility
# ============================================================

def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """TR = max(high-low, |high - prev_close|, |low - prev_close|)."""
    prev_close = close.shift(1)
    return pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """Average True Range (Wilder's smoothing)."""
    return rma(true_range(high, low, close), n)


# ============================================================
# Momentum
# ============================================================

def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder's smoothing)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = rma(gain, n)
    avg_loss = rma(loss, n)
    # Avoid division by zero
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # When avg_loss is 0 → all gains → RSI = 100
    out = out.where(avg_loss > 0, 100.0)
    # When both avg_gain and avg_loss are 0 → no movement → RSI = 50 (neutral)
    out = out.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    return out


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """Average Directional Index. Returns ADX series (0-100)."""
    high_diff = high.diff()
    low_diff = -low.diff()  # positive when low decreases (downward move)

    plus_dm = pd.Series(0.0, index=high.index)
    minus_dm = pd.Series(0.0, index=high.index)
    plus_dm[(high_diff > low_diff) & (high_diff > 0)] = high_diff[
        (high_diff > low_diff) & (high_diff > 0)
    ]
    minus_dm[(low_diff > high_diff) & (low_diff > 0)] = low_diff[
        (low_diff > high_diff) & (low_diff > 0)
    ]

    atr_n = rma(true_range(high, low, close), n)
    atr_safe = atr_n.replace(0.0, np.nan)

    plus_di = 100.0 * rma(plus_dm, n) / atr_safe
    minus_di = 100.0 * rma(minus_dm, n) / atr_safe

    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    return rma(dx.fillna(0.0), n)


# ============================================================
# Donchian channels
# ============================================================

def donchian_high(high: pd.Series, n: int) -> pd.Series:
    """Rolling N-bar maximum of high, EXCLUDING current bar (shift 1)."""
    return high.rolling(window=n, min_periods=n).max().shift(1)


def donchian_low(low: pd.Series, n: int) -> pd.Series:
    """Rolling N-bar minimum of low, EXCLUDING current bar (shift 1)."""
    return low.rolling(window=n, min_periods=n).min().shift(1)


# ============================================================
# Volatility (for D vol-targeting)
# ============================================================

def log_returns(close: pd.Series) -> pd.Series:
    """Log returns: ln(close[t] / close[t-1])."""
    ratio = close / close.shift(1)
    # Guard against non-positive prices (shouldn't happen but be safe)
    ratio = ratio.where(ratio > 0, np.nan)
    return np.log(ratio)


def realized_vol(close: pd.Series, n: int = 30, annualization_days: int = 365) -> pd.Series:
    """Rolling N-day annualized realized vol from log returns. Sample stdev (ddof=1)."""
    rets = log_returns(close)
    sigma_daily = rets.rolling(window=n, min_periods=n).std(ddof=1)
    return sigma_daily * np.sqrt(annualization_days)
