"""
Daily signal checker — D-Alt-Med (40/15) Donchian breakout on BTC/USDT (Binance).

Source of truth: global Binance.com BTC/USDT via data-api.binance.vision — Binance's
public market-data host (no API key). Unlike api.binance.com (HTTP 451 from US IPs),
this host is reachable from GitHub Actions US runners and serves the same global data.

State-tracked: maintains state.json with current position, last trade, hypothetical P&L.
After first EXIT, won't fire duplicate exit alerts; only watches for entry. Symmetric.

Usage:
    python check_signal.py                          # Console + state update
    python check_signal.py --telegram               # + Telegram on state change/signal
    python check_signal.py --telegram --always-send # + Telegram every day (verification mode)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd


# ============================================================
# Strategy parameters (D-Alt-Med 40/15)
# ============================================================
N_ENTRY = 40
N_EXIT = 15
VOL_TARGET = 0.30
VOL_LOOKBACK = 30
VOL_MIN = 0.05
VOL_MAX = 1.00
ANNUALIZATION = 365

STATE_FILE = Path(__file__).parent / "state.json"
BINANCE_SYMBOL = "BTCUSDT"  # global Binance.com pair — source of truth
BINANCE_HOST = "https://data-api.binance.vision"  # public market-data host (US-reachable)


# ============================================================
# Telegram helper
# ============================================================

def send_telegram(message: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[telegram] Skipped — env vars not set.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    data = urllib.parse.urlencode(payload).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = resp.status == 200
            print(f"[telegram] {'sent' if ok else f'HTTP {resp.status}'}")
            return ok
    except Exception as e:
        print(f"[telegram] Error: {e}")
        return False


# ============================================================
# Data fetch — global Binance via data-api.binance.vision
# ============================================================

def fetch_binance_daily(days: int = 120) -> pd.DataFrame:
    """
    Fetch daily OHLCV for global Binance.com BTC/USDT via data-api.binance.vision.

    This is Binance's public market-data host (security type NONE — no API key).
    It serves the same global data as api.binance.com but, unlike that host, is
    reachable from GitHub Actions US runners (api.binance.com returns HTTP 451).

    The candle index is each bar's OPEN time (UTC). The most recent bar is the
    still-forming current day; the caller drops it and signals off closed bars.
    """
    print(f"Source: Binance global / {BINANCE_HOST} / {BINANCE_SYMBOL} / last ~{days} days")

    # Ask for one extra bar so a full `days` of closed candles remains after the
    # in-progress current-day bar is dropped downstream. Binance caps at 1000.
    url = (
        f"{BINANCE_HOST}/api/v3/klines"
        f"?symbol={BINANCE_SYMBOL}&interval=1d&limit={min(days + 1, 1000)}"
    )

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "turtle-bot/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            bars = json.loads(resp.read())
    except Exception as e:
        raise RuntimeError(f"Binance fetch failed: {e}")

    if not bars:
        raise RuntimeError("Binance returned empty data")

    # Kline row schema: [openTime, open, high, low, close, volume, closeTime, ...].
    cols = ["open_time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "trades", "tbbav", "tbqav", "ignore"]
    df = pd.DataFrame(bars, columns=cols)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)

    df = df.sort_index()
    df = df[df["close"] > 0]
    return df


# ============================================================
# Indicator computation
# ============================================================

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["donch_high_entry"] = df["high"].rolling(N_ENTRY).max().shift(1)
    df["donch_low_exit"] = df["low"].rolling(N_EXIT).min().shift(1)
    df["log_ret"] = np.log(df["close"] / df["close"].shift(1))
    df["sigma_daily"] = df["log_ret"].rolling(VOL_LOOKBACK).std(ddof=1)
    df["sigma_annual"] = df["sigma_daily"] * np.sqrt(ANNUALIZATION)
    return df


# ============================================================
# State management
# ============================================================

def load_state() -> dict:
    """Load state from state.json. Returns default FLAT state if file missing/invalid."""
    if not STATE_FILE.exists():
        return _default_state()
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
        # Backward compat: ensure required fields exist
        state.setdefault("version", 1)
        state.setdefault("position", "FLAT")
        state.setdefault("entry", None)
        state.setdefault("last_trade", None)
        return state
    except Exception as e:
        print(f"[state] Failed to load ({e}), using default FLAT state.")
        return _default_state()


def _default_state() -> dict:
    return {
        "version": 1,
        "position": "FLAT",
        "entry": None,           # {"date": "...", "price": float, "size_fraction": float}
        "last_trade": None,      # {"entry_date", "entry_price", "exit_date", "exit_price", "pnl_pct"}
        "updated_at": None,
    }


def save_state(state: dict) -> None:
    state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    print(f"[state] Saved to {STATE_FILE.name}")


# ============================================================
# Core signal check
# ============================================================

def check_signal(send_alert: bool = False, always_send: bool = False) -> dict:
    now_utc = datetime.now(timezone.utc)
    print(f"\n{'=' * 60}")
    print(f"D-Alt-Med (40/15) Signal Check — {now_utc.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'=' * 60}\n")

    # Fetch data
    df = fetch_binance_daily(days=120)
    df = compute_indicators(df)

    if len(df) < N_ENTRY + VOL_LOOKBACK:
        msg = f"ERROR: insufficient data ({len(df)} bars)."
        print(msg)
        if send_alert:
            send_telegram(f"⚠️ {msg}")
        return {"error": "insufficient_data"}

    # Use last fully-closed bar
    today_utc_date = now_utc.date()
    last_idx = -1
    if df.index[-1].date() == today_utc_date:
        last_idx = -2
        print(f"NOTE: latest bar {df.index[-1].date()} not yet closed; using previous.\n")

    last_bar = df.iloc[last_idx]
    last_time = df.index[last_idx]

    close = float(last_bar["close"])
    donch_high = float(last_bar["donch_high_entry"])
    donch_low = float(last_bar["donch_low_exit"])
    sigma_annual = float(last_bar["sigma_annual"])

    # Date the active 40-day entry high was set. Use the exact same window the
    # trigger uses — the N_ENTRY bars *before* the reference bar (rolling+shift(1)) —
    # so the reported high always matches donch_high.
    ref_pos = len(df) + last_idx  # absolute index of the last closed bar
    hi_window = df.iloc[ref_pos - N_ENTRY:ref_pos]
    high_date = hi_window["high"].idxmax()
    high_date_str = high_date.strftime("%Y-%m-%d")
    days_since_high = (last_time.date() - high_date.date()).days

    target_fraction = VOL_TARGET / sigma_annual if sigma_annual > 0 else 0
    capped_fraction = 0 if target_fraction < VOL_MIN else min(target_fraction, VOL_MAX)

    # Raw signals (algorithmic conditions)
    raw_long_signal = close > donch_high
    raw_exit_signal = close < donch_low

    # Apply state filter: only relevant signals fire
    state = load_state()
    print(f"Loaded state: position={state['position']}")

    actionable_entry = raw_long_signal and state["position"] == "FLAT"
    actionable_exit = raw_exit_signal and state["position"] == "LONG"

    # ---- Console output ----
    print(f"Bar:                  {last_time.strftime('%Y-%m-%d')} (UTC)")
    print(f"Close:                ${close:,.2f}")
    print(f"Donchian-{N_ENTRY} High:     ${donch_high:,.2f}")
    print(f"  └ high set on:       {high_date_str} ({days_since_high}d ago)")
    print(f"Donchian-{N_EXIT} Low:      ${donch_low:,.2f}")
    print(f"Realized vol (ann.):  {sigma_annual*100:.1f}%")
    print(f"Vol-target fraction:  {capped_fraction*100:.1f}% of equity")
    print(f"Current state:        {state['position']}")

    if state["position"] == "LONG" and state["entry"]:
        days_held = (last_time.date() - datetime.strptime(state["entry"]["date"], "%Y-%m-%d").date()).days
        unreal_pnl_pct = (close / state["entry"]["price"] - 1) * 100
        print(f"Open trade:           entry {state['entry']['date']} @ ${state['entry']['price']:,.2f} ({days_held}d, unreal {unreal_pnl_pct:+.2f}%)")
    elif state["last_trade"]:
        lt = state["last_trade"]
        print(f"Last trade:           {lt['entry_date']} @ ${lt['entry_price']:,.2f} → {lt['exit_date']} @ ${lt['exit_price']:,.2f} ({lt['pnl_pct']:+.2f}%)")
    print()

    dist_to_high = (donch_high / close - 1) * 100
    dist_to_low = (close / donch_low - 1) * 100

    if actionable_entry:
        print(f">>> LONG ENTRY SIGNAL — Close is +{(close/donch_high-1)*100:.2f}% above {N_ENTRY}-day high")
    elif actionable_exit:
        print(f">>> EXIT SIGNAL — Close is {(close/donch_low-1)*100:.2f}% below {N_EXIT}-day low")
    elif state["position"] == "FLAT":
        print(f"--- NO SIGNAL (watching for ENTRY) ---")
        print(f"   Distance to entry trigger: +{dist_to_high:.2f}%")
        if dist_to_high < 2.0:
            print(f"   [!] CLOSE to entry signal (within 2%)")
    else:  # LONG
        print(f"--- NO SIGNAL (watching for EXIT) ---")
        print(f"   Distance to exit trigger: -{dist_to_low:.2f}%")
        if dist_to_low < 2.0:
            print(f"   [!] CLOSE to exit signal (within 2%)")
    print()

    # ---- State update ----
    state_changed = False
    if actionable_entry:
        state["position"] = "LONG"
        state["entry"] = {
            "date": last_time.strftime("%Y-%m-%d"),
            "price": close,
            "size_fraction": capped_fraction,
        }
        state_changed = True
        print(f"[state] FLAT → LONG @ ${close:,.2f}")
    elif actionable_exit:
        entry = state["entry"]
        pnl_pct = (close / entry["price"] - 1) * 100
        state["last_trade"] = {
            "entry_date": entry["date"],
            "entry_price": entry["price"],
            "exit_date": last_time.strftime("%Y-%m-%d"),
            "exit_price": close,
            "pnl_pct": round(pnl_pct, 4),
        }
        state["position"] = "FLAT"
        state["entry"] = None
        state_changed = True
        print(f"[state] LONG → FLAT @ ${close:,.2f} (PnL {pnl_pct:+.2f}%)")

    save_state(state)

    # ---- Build Telegram message ----
    msg = _build_telegram_message(
        state, last_time, close, donch_high, donch_low, sigma_annual, capped_fraction,
        dist_to_high, dist_to_low, actionable_entry, actionable_exit, always_send,
        high_date_str, days_since_high,
    )

    if msg and send_alert:
        send_telegram(msg)

    return {
        "date": last_time.strftime("%Y-%m-%d"),
        "close": close,
        "donch_high": donch_high,
        "donch_low": donch_low,
        "position": state["position"],
        "actionable_entry": actionable_entry,
        "actionable_exit": actionable_exit,
        "state_changed": state_changed,
    }


def _build_telegram_message(state, last_time, close, donch_high, donch_low,
                            sigma_annual, capped_fraction, dist_to_high, dist_to_low,
                            actionable_entry, actionable_exit, always_send,
                            high_date_str, days_since_high) -> str | None:
    """Build the Telegram message. Returns None if nothing to send."""
    date_str = last_time.strftime("%Y-%m-%d")

    if actionable_entry:
        return (
            f"🟢 *D-Alt-Med LONG ENTRY*\n"
            f"BTC/USDT — {date_str}\n\n"
            f"Close: ${close:,.2f}\n"
            f"{N_ENTRY}d High broken: ${donch_high:,.2f}\n"
            f"(high set {high_date_str}, {days_since_high}d ago)\n"
            f"Vol annual: {sigma_annual*100:.1f}%\n"
            f"*Target size: {capped_fraction*100:.1f}% of equity*\n\n"
            f"Action: BUY at next bar open"
        )

    if actionable_exit:
        entry = state["last_trade"]  # state already updated above
        return (
            f"🔴 *D-Alt-Med EXIT*\n"
            f"BTC/USDT — {date_str}\n\n"
            f"Close: ${close:,.2f}\n"
            f"{N_EXIT}d Low broken: ${donch_low:,.2f}\n\n"
            f"Trade: {entry['entry_date']} @ ${entry['entry_price']:,.2f} → "
            f"{entry['exit_date']} @ ${entry['exit_price']:,.2f}\n"
            f"*PnL: {entry['pnl_pct']:+.2f}%*\n\n"
            f"Action: SELL at next bar open"
        )

    if not always_send:
        return None

    # Verification-mode message — no actionable signal
    position = state["position"]

    if position == "FLAT":
        # Show only entry-relevant info
        warn = ""
        if dist_to_high < 2.0:
            warn = f"\n⚠️ CLOSE to entry ({dist_to_high:.2f}% away)"
        last_trade_line = ""
        if state["last_trade"]:
            lt = state["last_trade"]
            last_trade_line = (
                f"\nLast trade: {lt['entry_date']} → {lt['exit_date']} "
                f"({lt['pnl_pct']:+.2f}%)"
            )
        return (
            f"🟡 *D-Alt-Med Daily Check*\n"
            f"BTC/USDT — {date_str}\n\n"
            f"Status: *FLAT* (watching for entry)\n"
            f"Close: ${close:,.2f}\n"
            f"{N_ENTRY}d High (entry trigger): ${donch_high:,.2f}\n"
            f"High set: {high_date_str} ({days_since_high}d ago)\n"
            f"Distance to entry: +{dist_to_high:.2f}%{warn}\n\n"
            f"Vol annual: {sigma_annual*100:.1f}%\n"
            f"Target size if signal: {capped_fraction*100:.1f}%{last_trade_line}"
        )
    else:  # LONG
        # Show only exit-relevant info
        entry = state["entry"]
        days_held = (last_time.date() - datetime.strptime(entry["date"], "%Y-%m-%d").date()).days
        unreal_pnl = (close / entry["price"] - 1) * 100
        warn = ""
        if dist_to_low < 2.0:
            warn = f"\n⚠️ CLOSE to exit ({dist_to_low:.2f}% away)"
        return (
            f"🟡 *D-Alt-Med Daily Check*\n"
            f"BTC/USDT — {date_str}\n\n"
            f"Status: *LONG* ({days_held}d held)\n"
            f"Close: ${close:,.2f}\n"
            f"Entry: ${entry['price']:,.2f} on {entry['date']}\n"
            f"*Unrealized PnL: {unreal_pnl:+.2f}%*\n\n"
            f"{N_EXIT}d Low (exit trigger): ${donch_low:,.2f}\n"
            f"Distance to exit: -{dist_to_low:.2f}%{warn}\n\n"
            f"Vol annual: {sigma_annual*100:.1f}%"
        )


# ============================================================
# Entry point
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="D-Alt-Med daily signal check")
    parser.add_argument("--telegram", action="store_true",
                        help="Send Telegram alert (needs TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env vars)")
    parser.add_argument("--always-send", action="store_true",
                        help="Always send daily Telegram message (verification mode)")
    args = parser.parse_args()

    try:
        check_signal(send_alert=args.telegram, always_send=args.always_send)
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        if args.telegram:
            send_telegram(f"⚠️ *D-Alt-Med daily check FAILED*\n```\n{e}\n```")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
