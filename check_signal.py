"""
Daily signal checker — D-Alt-Med (40/15) Donchian breakout on BTC/USDT.

Self-contained: fetches data directly via ccxt without depending on local SQLite cache.
Uses Kraken by default (works in GitHub Actions cloud; Binance blocks US IPs with 451).
Override with env var SIGNAL_EXCHANGE if needed.

Usage:
    python check_signal.py                          # Console output only
    python check_signal.py --telegram               # Console + Telegram on signal
    python check_signal.py --telegram --always-send # Console + Telegram EVERY day (verification mode)

Environment variables:
    TELEGRAM_BOT_TOKEN   — from @BotFather
    TELEGRAM_CHAT_ID     — your chat id from getUpdates
    SIGNAL_EXCHANGE      — ccxt exchange id (default: kraken). Try: kraken, coinbase, bitstamp, okx
    SIGNAL_SYMBOL        — symbol to track (default: BTC/USDT)
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import ccxt
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
VOL_MAX = 1.00  # spot 1x
ANNUALIZATION = 365


# ============================================================
# Telegram helper
# ============================================================

def send_telegram(message: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[telegram] Skipped — TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID env var not set.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }
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
# Data fetch (cloud-friendly: uses Kraken by default)
# ============================================================

def fetch_recent_daily(symbol: str = "BTC/USDT", days: int = 120) -> pd.DataFrame:
    """
    Fetch recent daily OHLCV. Uses Kraken by default (works globally including
    GitHub Actions runners). Override via SIGNAL_EXCHANGE env var.
    """
    exchange_id = os.getenv("SIGNAL_EXCHANGE", "kraken").lower()
    print(f"Source: {exchange_id} / {symbol} / last ~{days} days")
    try:
        exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    except AttributeError:
        raise RuntimeError(f"Unknown exchange: {exchange_id}")

    since_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    bars = exchange.fetch_ohlcv(symbol, "1d", since=since_ms, limit=days + 50)
    if not bars:
        raise RuntimeError(f"No bars returned from {exchange_id} for {symbol}")

    df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    return df


# ============================================================
# Indicator math (inline, no external deps)
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
# Signal check
# ============================================================

def check_signal(send_alert: bool = False, always_send: bool = False) -> dict:
    now_utc = datetime.now(timezone.utc)

    print(f"\n{'=' * 60}")
    print(f"D-Alt-Med (40/15) Signal Check — {now_utc.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'=' * 60}\n")

    symbol = os.getenv("SIGNAL_SYMBOL", "BTC/USDT")
    df = fetch_recent_daily(symbol=symbol, days=120)
    df = compute_indicators(df)

    if len(df) < N_ENTRY + VOL_LOOKBACK:
        msg = f"ERROR: insufficient data ({len(df)} bars, need at least {N_ENTRY + VOL_LOOKBACK})."
        print(msg)
        if send_alert:
            send_telegram(f"⚠️ {msg}")
        return {"error": "insufficient_data"}

    # Use last fully-closed daily bar.
    # If latest bar's date == today UTC, that bar may not be closed yet.
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

    target_fraction = VOL_TARGET / sigma_annual if sigma_annual > 0 else 0
    capped_fraction = 0 if target_fraction < VOL_MIN else min(target_fraction, VOL_MAX)

    long_signal = close > donch_high
    exit_signal = close < donch_low

    # Console output
    print(f"Bar:                  {last_time.strftime('%Y-%m-%d')} (UTC)")
    print(f"Close:                ${close:,.2f}")
    print(f"Donchian-{N_ENTRY} High:     ${donch_high:,.2f}  (entry trigger)")
    print(f"Donchian-{N_EXIT} Low:      ${donch_low:,.2f}  (exit trigger)")
    print(f"Realized vol (ann.):  {sigma_annual*100:.1f}%")
    print(f"Vol-target fraction:  {capped_fraction*100:.1f}% of equity")
    print()

    dist_to_high = (donch_high / close - 1) * 100
    dist_to_low = (close / donch_low - 1) * 100

    if long_signal:
        status_line = f">>> LONG ENTRY SIGNAL — Close is +{(close/donch_high-1)*100:.2f}% above {N_ENTRY}-day high"
    elif exit_signal:
        status_line = f">>> EXIT SIGNAL — Close is {(close/donch_low-1)*100:.2f}% below {N_EXIT}-day low"
    else:
        status_line = (f"--- NO SIGNAL ---\n"
                       f"   Distance to entry trigger: +{dist_to_high:.2f}%\n"
                       f"   Distance to exit trigger:  -{dist_to_low:.2f}%")
        if dist_to_high < 2.0:
            status_line += "\n   [!] CLOSE to entry signal (within 2%)"
        if dist_to_low < 2.0:
            status_line += "\n   [!] CLOSE to exit signal (within 2%)"
    print(status_line)
    print()

    # Build Telegram message
    msg = None
    if long_signal:
        msg = (
            f"🟢 *D-Alt-Med LONG ENTRY*\n"
            f"BTC/USDT — {last_time.strftime('%Y-%m-%d')}\n\n"
            f"Close: ${close:,.2f}\n"
            f"{N_ENTRY}d High broken: ${donch_high:,.2f}\n"
            f"Vol annual: {sigma_annual*100:.1f}%\n"
            f"Target size: {capped_fraction*100:.1f}% of equity\n\n"
            f"Action: BUY at next bar open"
        )
    elif exit_signal:
        msg = (
            f"🔴 *D-Alt-Med EXIT*\n"
            f"BTC/USDT — {last_time.strftime('%Y-%m-%d')}\n\n"
            f"Close: ${close:,.2f}\n"
            f"{N_EXIT}d Low broken: ${donch_low:,.2f}\n\n"
            f"Action: SELL at next bar open"
        )
    elif always_send:
        # Verification-mode message (no signal but always send)
        warn_lines = []
        if dist_to_high < 2.0:
            warn_lines.append(f"   ⚠️ CLOSE to entry ({dist_to_high:.2f}% away)")
        if dist_to_low < 2.0:
            warn_lines.append(f"   ⚠️ CLOSE to exit ({dist_to_low:.2f}% away)")
        warn = ("\n" + "\n".join(warn_lines)) if warn_lines else ""
        msg = (
            f"📊 *D-Alt-Med Daily Check*\n"
            f"BTC/USDT — {last_time.strftime('%Y-%m-%d')}\n\n"
            f"Close: ${close:,.2f}\n"
            f"{N_ENTRY}d High: ${donch_high:,.2f}\n"
            f"{N_EXIT}d Low: ${donch_low:,.2f}\n"
            f"Vol annual: {sigma_annual*100:.1f}%\n"
            f"Target size: {capped_fraction*100:.1f}%\n\n"
            f"--- NO SIGNAL ---\n"
            f"Distance to entry: +{dist_to_high:.2f}%\n"
            f"Distance to exit: -{dist_to_low:.2f}%{warn}"
        )

    if msg and send_alert:
        send_telegram(msg)

    return {
        "date": last_time.strftime("%Y-%m-%d"),
        "close": close,
        "donch_high": donch_high,
        "donch_low": donch_low,
        "sigma_annual": sigma_annual,
        "target_fraction": capped_fraction,
        "long_signal": long_signal,
        "exit_signal": exit_signal,
    }


# ============================================================
# Entry point
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="D-Alt-Med daily signal check")
    parser.add_argument("--telegram", action="store_true",
                        help="Send Telegram alert (needs TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env vars)")
    parser.add_argument("--always-send", action="store_true",
                        help="Always send daily Telegram message (verification mode, first week)")
    args = parser.parse_args()

    try:
        check_signal(send_alert=args.telegram, always_send=args.always_send)
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        # Notify on failure too so we know automation broke
        if args.telegram:
            send_telegram(f"⚠️ *D-Alt-Med daily check FAILED*\n```\n{e}\n```")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
