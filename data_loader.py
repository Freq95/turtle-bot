"""
Data loader: fetch BTC/USDT OHLCV from Binance via ccxt, validate, cache in SQLite, export CSV backup.

See SPEC.md §5 (Data Pipeline) and §6 (SQLite Cache Schema).

Run as: `python data_loader.py`
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import ccxt
import pandas as pd

import config

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


TIMEFRAME_MS = {
    "1d": 86_400_000,
    "4h": 14_400_000,
}


# ============================================================
# Cache (SQLite)
# ============================================================

def _ensure_cache_dir() -> None:
    Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)


def init_cache(db_path: str = config.CACHE_DB_PATH) -> sqlite3.Connection:
    _ensure_cache_dir()
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            PRIMARY KEY (symbol, timeframe, timestamp)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ohlcv_lookup
        ON ohlcv (symbol, timeframe, timestamp)
    """)
    conn.commit()
    return conn


def cache_has_range(conn: sqlite3.Connection, symbol: str, timeframe: str,
                    start_ms: int, end_ms: int) -> tuple[bool, int | None, int | None]:
    """
    Returns (is_complete, min_ts_in_cache, max_ts_in_cache).
    is_complete=True if cache contains data spanning the entire range with no gaps.
    """
    row = conn.execute(
        "SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM ohlcv "
        "WHERE symbol = ? AND timeframe = ? AND timestamp BETWEEN ? AND ?",
        (symbol, timeframe, start_ms, end_ms),
    ).fetchone()
    if row is None or row[0] is None:
        return False, None, None
    min_ts, max_ts, count = row
    expected = ((end_ms - start_ms) // TIMEFRAME_MS[timeframe]) + 1
    # Tolerance: ±2 bars (per SPEC §5.5)
    is_complete = (
        abs(count - expected) <= 2
        and min_ts <= start_ms + TIMEFRAME_MS[timeframe]
        and max_ts >= end_ms - TIMEFRAME_MS[timeframe]
    )
    return is_complete, min_ts, max_ts


def write_bars_to_cache(conn: sqlite3.Connection, symbol: str, timeframe: str,
                        bars: Iterable[list]) -> int:
    rows = [
        (symbol, timeframe, int(ts), float(o), float(h), float(l), float(c), float(v))
        for ts, o, h, l, c, v in bars
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO ohlcv "
        "(symbol, timeframe, timestamp, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def load_from_cache(conn: sqlite3.Connection, symbol: str, timeframe: str,
                    start_ms: int, end_ms: int) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM ohlcv "
        "WHERE symbol = ? AND timeframe = ? AND timestamp BETWEEN ? AND ? "
        "ORDER BY timestamp ASC",
        conn,
        params=(symbol, timeframe, start_ms, end_ms),
    )
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    return df


# ============================================================
# Fetch (ccxt)
# ============================================================

def fetch_ohlcv_paginated(exchange, symbol: str, timeframe: str,
                          start_ms: int, end_ms: int) -> list[list]:
    """
    Paginated OHLCV fetch from Binance. Returns list of [ts, o, h, l, c, v].
    Binance limit: 1000 bars per call.
    """
    all_bars: list[list] = []
    since = start_ms
    tf_ms = TIMEFRAME_MS[timeframe]
    seen_ts: set[int] = set()

    while since <= end_ms:
        try:
            bars = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        except ccxt.NetworkError as e:
            log.warning("Network error, retry in 5s: %s", e)
            time.sleep(5)
            continue
        except ccxt.ExchangeError as e:
            log.error("Exchange error: %s", e)
            break

        if not bars:
            break

        new_bars = [b for b in bars if int(b[0]) not in seen_ts and int(b[0]) <= end_ms]
        if not new_bars:
            break

        for b in new_bars:
            seen_ts.add(int(b[0]))
        all_bars.extend(new_bars)

        last_ts = int(bars[-1][0])
        if last_ts <= since:
            log.warning("No progress in pagination, stopping at ts=%d", last_ts)
            break
        since = last_ts + tf_ms

        log.info("Fetched %d bars (total: %d), last_ts=%s",
                 len(new_bars), len(all_bars),
                 datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc).isoformat())

        # Respect rate limit
        time.sleep(exchange.rateLimit / 1000)

    return all_bars


# ============================================================
# Validation
# ============================================================

def validate_bars(bars: list[list], timeframe: str) -> list[str]:
    """
    Returns list of warning/error strings. Empty list = clean.
    Raises ValueError on fatal errors (data corruption).
    """
    issues: list[str] = []
    if not bars:
        return ["empty bars list"]

    tf_ms = TIMEFRAME_MS[timeframe]

    # Sort by timestamp just in case
    bars_sorted = sorted(bars, key=lambda b: b[0])

    prev_close: float | None = None
    prev_ts: int | None = None

    for i, (ts, o, h, l, c, v) in enumerate(bars_sorted):
        # Fatal: high < low
        if h < l:
            raise ValueError(f"Bar {i} ts={ts}: high ({h}) < low ({l}) — corrupt data")
        # Fatal: high < max(open, close) or low > min(open, close)
        if h < max(o, c) - 1e-9:
            raise ValueError(f"Bar {i} ts={ts}: high < max(open, close)")
        if l > min(o, c) + 1e-9:
            raise ValueError(f"Bar {i} ts={ts}: low > min(open, close)")

        # Warning: zero volume
        if v == 0:
            issues.append(f"Bar {i} ts={ts}: volume=0")

        # Warning: price anomaly (>50% bar-to-bar)
        if prev_close is not None and prev_close > 0:
            pct = abs(c - prev_close) / prev_close
            if pct > 0.5:
                issues.append(f"Bar {i} ts={ts}: price change {pct*100:.1f}% (close {prev_close}→{c})")

        # Gap check
        if prev_ts is not None:
            expected_diff = tf_ms
            actual_diff = ts - prev_ts
            if actual_diff != expected_diff:
                issues.append(f"Gap at index {i}: prev_ts={prev_ts}, this_ts={ts}, "
                              f"diff={actual_diff}ms (expected {expected_diff}ms)")

        prev_close = c
        prev_ts = ts

    return issues


# ============================================================
# CSV backup
# ============================================================

def write_csv_backup(df: pd.DataFrame, path: str) -> None:
    df_out = df.reset_index()
    df_out["timestamp"] = df_out["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    df_out.to_csv(path, index=False)
    log.info("CSV backup written: %s (%d rows)", path, len(df_out))


# ============================================================
# Main entry
# ============================================================

def load_or_fetch(symbol: str, timeframe: str,
                  start_dt: datetime, end_dt: datetime,
                  force_refresh: bool = False) -> pd.DataFrame:
    """
    Returns DataFrame indexed by UTC timestamp, columns: open, high, low, close, volume.
    Fetches from exchange if cache is incomplete; otherwise reads from cache.
    """
    start_ms = int(start_dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(end_dt.replace(tzinfo=timezone.utc).timestamp() * 1000)

    conn = init_cache()
    try:
        if not force_refresh:
            complete, _, _ = cache_has_range(conn, symbol, timeframe, start_ms, end_ms)
            if complete:
                log.info("Cache hit for %s %s [%s → %s]",
                         symbol, timeframe, start_dt.date(), end_dt.date())
                return load_from_cache(conn, symbol, timeframe, start_ms, end_ms)

        log.info("Fetching %s %s [%s → %s] from %s...",
                 symbol, timeframe, start_dt.date(), end_dt.date(), config.EXCHANGE)
        exchange = getattr(ccxt, config.EXCHANGE)({"enableRateLimit": True})
        bars = fetch_ohlcv_paginated(exchange, symbol, timeframe, start_ms, end_ms)

        if not bars:
            raise RuntimeError(f"No bars fetched for {symbol} {timeframe}")

        log.info("Validating %d bars...", len(bars))
        issues = validate_bars(bars, timeframe)
        for issue in issues[:20]:
            log.warning("Validation: %s", issue)
        if len(issues) > 20:
            log.warning("... and %d more issues", len(issues) - 20)

        n = write_bars_to_cache(conn, symbol, timeframe, bars)
        log.info("Wrote %d bars to cache", n)

        return load_from_cache(conn, symbol, timeframe, start_ms, end_ms)
    finally:
        conn.close()


def main() -> int:
    symbol = config.SYMBOL
    start = config.DATA_START
    end = config.DATA_END

    for tf in config.TIMEFRAMES:
        log.info("=" * 60)
        log.info("Loading %s %s [%s → %s]", symbol, tf, start.date(), end.date())
        log.info("=" * 60)
        df = load_or_fetch(symbol, tf, start, end)
        log.info("Loaded %d bars. Range: %s → %s",
                 len(df), df.index[0], df.index[-1])

        # Export CSV backup
        sym_safe = symbol.replace("/", "")
        csv_path = f"{config.DATA_DIR}/{sym_safe}_{tf}_{start.year}_{end.year}.csv"
        write_csv_backup(df, csv_path)

    log.info("Data pipeline complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
