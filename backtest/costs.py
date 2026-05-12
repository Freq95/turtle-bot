"""
Cost model: fees, slippage, funding, liquidation. See SPEC.md §8, §9, §10.
"""

from __future__ import annotations

import config


# ============================================================
# Fees & slippage
# ============================================================

def fee_for_mode(mode: str) -> float:
    """Per-side fee rate."""
    return config.SPOT_FEE if mode == "spot_1x" else config.FUTURES_FEE


def apply_entry_slippage(price: float, side: str) -> float:
    """
    Entry execution price after slippage.
    Long pays more (price up), short receives less (price down).
    """
    if side == "long":
        return price * (1.0 + config.SLIPPAGE)
    return price * (1.0 - config.SLIPPAGE)


def apply_exit_slippage(price: float, side: str) -> float:
    """
    Exit execution price after slippage.
    Long sells at lower price, short covers at higher price.
    """
    if side == "long":
        return price * (1.0 - config.SLIPPAGE)
    return price * (1.0 + config.SLIPPAGE)


def stop_fill_price(open_price: float, stop_price: float, side: str) -> float:
    """
    Fill price when a stop loss is triggered intra-bar.
    Long: fill at min(open, stop) — gap-down through stop fills at open.
    Short: fill at max(open, stop) — gap-up through stop fills at open.
    Slippage applied adversely.
    """
    if side == "long":
        base = min(open_price, stop_price)
        return base * (1.0 - config.SLIPPAGE)
    base = max(open_price, stop_price)
    return base * (1.0 + config.SLIPPAGE)


def entry_fee_cost(notional: float, mode: str) -> float:
    return notional * fee_for_mode(mode)


def exit_fee_cost(notional: float, mode: str) -> float:
    return notional * fee_for_mode(mode)


# ============================================================
# Funding (futures only)
# ============================================================

def compute_funding_charge(notional: float, side: str) -> float:
    """
    Returns funding charge in USD. Positive = paid by user (cash out).
    Long pays funding when funding rate is positive; short receives it.
    """
    funding = abs(notional) * config.FUTURES_FUNDING_DAILY
    return funding if side == "long" else -funding


# ============================================================
# Liquidation (futures only)
# ============================================================

def liquidation_price(entry_price: float, side: str, leverage: float) -> float:
    """
    Liquidation price assuming entry_price and given leverage.
    Margin fraction = 1/leverage. Liquidation when equity = maintenance margin.

    For leverage=2x, maintenance=0.5%, threshold = 0.5 - 0.005 = 0.495
    Long liquidation = entry * (1 - 0.495) ≈ entry * 0.505
    Short liquidation = entry * (1 + 0.495) ≈ entry * 1.495
    """
    if leverage <= 1.0:
        # No liquidation modeled for spot or 1x futures
        return 0.0 if side == "long" else float("inf")
    margin_fraction = 1.0 / leverage
    threshold = margin_fraction - config.MAINTENANCE_MARGIN
    if side == "long":
        return entry_price * (1.0 - threshold)
    return entry_price * (1.0 + threshold)


def liquidation_fill_price(open_price: float, liq_price: float, side: str) -> float:
    """
    Fill price on a liquidation event, with extra LIQUIDATION_PENALTY adverse.
    """
    penalty = config.LIQUIDATION_PENALTY
    if side == "long":
        base = min(open_price, liq_price)
        return base * (1.0 - penalty)
    base = max(open_price, liq_price)
    return base * (1.0 + penalty)


def is_liquidated(side: str, open_price: float, low_price: float, high_price: float,
                  liq_price: float) -> bool:
    """
    Returns True if the position is liquidated on this bar.
    For long: liquidation if low <= liq_price.
    For short: liquidation if high >= liq_price.
    Bar's open is used to determine fill, but trigger check uses low/high.
    """
    if side == "long":
        return low_price <= liq_price
    return high_price >= liq_price
