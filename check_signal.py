"""
Daily signal checker — D-Alt-Med (40/15) Donchian breakout on BTC/USDT spot.

Replaces TradingView paid alerts. Run manually or via Windows Task Scheduler.

Usage:
    python check_signal.py                  # Console output only
    python check_signal.py --telegram       # Also send Telegram alert (needs env vars)

Telegram setup (optional, free):
    1. Open Telegram → search @BotFather → /newbot → get bot token
    2. Send /start to your new bot, then visit:
       https://api.telegram.org/bot<TOKEN>/getUpdates
       Find "chat":{"id":<NUMBER>} → that's your chat_id
    3. Set environment variables:
       Windows PowerShell:
         $env:TELEGRAM_BOT_TOKEN="123456:abc..."
         $env:TELEGRAM_CHAT_ID="123456789"
       Then run:
         python check_signal.py --telegram

Windows daily auto-run (free):
    1. Open Task Scheduler → Create Basic Task
    2. Trigger: Daily, 00:30 UTC (after daily bar close)
    3. Action: Start a program
       Program: python.exe (or full path C:\\Users\\Paul\\AppData\\Local\\Programs\\Python\\Python312\\python.exe)
       Arguments: D:\\m-trade\\check_signal.py --telegram
       Start in: D:\\m-trade
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import pandas as pd

import config
from backtest import indicators as ind
from data_loader import load_or_fetch


# ============================================================
# Strategy parameters (D-Alt-Med 40/15)
# ============================================================
N_ENTRY = 40
N_EXIT = 15
VOL_TARGET = 0.30
VOL_LOOKBACK = 30
VOL_MIN = 0.05
VOL_MAX = 1.00  # spot 1x


# ============================================================
# Telegram helper (free, optional)
# ============================================================

def send_telegram(message: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[telegram] Skipped — TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID env var not set.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    params = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }
    data = urllib.parse.urlencode(params).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = resp.status == 200
            if ok:
                print("[telegram] Message sent.")
            else:
                print(f"[telegram] HTTP {resp.status}")
            return ok
    except Exception as e:
        print(f"[telegram] Error: {e}")
        return False


# ============================================================
# Signal check
# ============================================================

def check_signal(send_alert: bool = False) -> dict:
    # Fetch fresh data (force refresh to get today's daily bar if available)
    today = datetime.now(timezone.utc)
    data_end = today + timedelta(days=1)  # try to include today's bar

    print(f"\n{'=' * 60}")
    print(f"D-Alt-Med (40/15) Signal Check — {today.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'=' * 60}\n")

    print("Fetching latest BTC daily data from Binance...")
    df = load_or_fetch(
        symbol=config.SYMBOL,
        timeframe="1d",
        start_dt=datetime(2024, 1, 1),  # need ~6 months for indicators
        end_dt=data_end,
        force_refresh=True,
    )

    if len(df) < N_ENTRY + 10:
        print(f"ERROR: insufficient data ({len(df)} bars).")
        return {"error": "insufficient_data"}

    # Compute indicators
    df["donch_high_entry"] = df["high"].rolling(N_ENTRY).max().shift(1)
    df["donch_low_exit"] = df["low"].rolling(N_EXIT).min().shift(1)
    df["log_ret"] = ind.log_returns(df["close"])
    df["sigma_daily"] = df["log_ret"].rolling(VOL_LOOKBACK).std(ddof=1)
    df["sigma_annual"] = df["sigma_daily"] * (config.ANNUALIZATION_DAYS ** 0.5)

    # Latest completed bar (don't trust unconfirmed today bar)
    # If today's bar exists but hasn't closed (now < bar_close_time), use yesterday
    last_bar = df.iloc[-1]
    last_time = df.index[-1]
    last_bar_close_time = last_time + pd.Timedelta(days=1)
    if today < last_bar_close_time.to_pydatetime():
        print(f"NOTE: Latest bar {last_time.date()} not yet closed (closes at {last_bar_close_time}).")
        print(f"      Using previous confirmed bar instead.\n")
        last_bar = df.iloc[-2]
        last_time = df.index[-2]

    close = float(last_bar["close"])
    donch_high = float(last_bar["donch_high_entry"])
    donch_low = float(last_bar["donch_low_exit"])
    sigma_annual = float(last_bar["sigma_annual"])

    # Vol-target sizing (assume $1000 equity for display — adjust if needed)
    target_fraction = VOL_TARGET / sigma_annual if sigma_annual > 0 else 0
    capped_fraction = (
        0 if target_fraction < VOL_MIN
        else min(target_fraction, VOL_MAX)
    )

    # Signal check
    long_signal = close > donch_high
    exit_signal = close < donch_low

    # Print status
    print(f"Latest confirmed bar: {last_time.strftime('%Y-%m-%d')} (UTC)")
    print(f"Close:                ${close:,.2f}")
    print(f"Donchian-40 High:     ${donch_high:,.2f}  (entry trigger if close > this)")
    print(f"Donchian-15 Low:      ${donch_low:,.2f}  (exit trigger if close < this)")
    print(f"Realized vol (ann.):  {sigma_annual*100:.1f}%")
    print(f"Vol-target fraction:  {capped_fraction*100:.1f}% of equity")
    print()

    # Distances
    if long_signal:
        print(f">>> LONG ENTRY SIGNAL — Close is +{(close/donch_high-1)*100:.2f}% above 40-day high")
    elif exit_signal:
        print(f">>> EXIT SIGNAL — Close is {(close/donch_low-1)*100:.2f}% below 15-day low")
    else:
        dist_to_high = (donch_high / close - 1) * 100
        dist_to_low = (close / donch_low - 1) * 100
        print(f"--- NO SIGNAL ---")
        print(f"   Distance to entry trigger (40d high): +{dist_to_high:.2f}%")
        print(f"   Distance to exit trigger (15d low):   -{dist_to_low:.2f}%")
        if dist_to_high < 2.0:
            print(f"   [!] CLOSE to entry signal (within 2%)")
        if dist_to_low < 2.0:
            print(f"   [!] CLOSE to exit signal (within 2%)")
    print()

    # Build alert message
    msg = None
    if long_signal:
        msg = (
            f"🟢 *D-Alt-Med LONG ENTRY*\n"
            f"BTC/USDT spot — {last_time.strftime('%Y-%m-%d')}\n\n"
            f"Close: ${close:,.2f}\n"
            f"40d High broken: ${donch_high:,.2f}\n"
            f"Vol annual: {sigma_annual*100:.1f}%\n"
            f"Target size: {capped_fraction*100:.1f}% of equity\n\n"
            f"Action: BUY at next bar open"
        )
    elif exit_signal:
        msg = (
            f"🔴 *D-Alt-Med EXIT*\n"
            f"BTC/USDT spot — {last_time.strftime('%Y-%m-%d')}\n\n"
            f"Close: ${close:,.2f}\n"
            f"15d Low broken: ${donch_low:,.2f}\n\n"
            f"Action: SELL at next bar open"
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
                        help="Send Telegram alert if signal fires (requires TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env vars)")
    args = parser.parse_args()

    try:
        check_signal(send_alert=args.telegram)
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
