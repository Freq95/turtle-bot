"""
Main entry point. Run all 15 backtests, generate reports.

Usage: python main.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import config
from analysis.compare import write_master_csv, write_equity_overlay
from analysis.reports import generate_all_html_reports, generate_summary_html
from backtest.runner import run_all
from data_loader import load_or_fetch

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def main() -> int:
    log.info("=" * 60)
    log.info("BTC Backtest Framework — Run started")
    log.info("=" * 60)

    # Ensure output dir exists
    Path(config.REPORTS_DIR).mkdir(parents=True, exist_ok=True)

    # Load data
    log.info("Loading data...")
    daily = load_or_fetch(config.SYMBOL, "1d", config.DATA_START, config.DATA_END)
    four_h = load_or_fetch(config.SYMBOL, "4h", config.DATA_START, config.DATA_END)
    log.info("Daily: %d bars. 4h: %d bars.", len(daily), len(four_h))

    # Run all 15 backtests
    log.info("Running backtest matrix (15 backtests)...")
    records = run_all(daily, four_h)

    # Generate outputs
    log.info("Writing master CSV...")
    csv_path = f"{config.REPORTS_DIR}/master.csv"
    write_master_csv(records, csv_path)

    log.info("Writing equity curves overlay...")
    overlay_path = f"{config.REPORTS_DIR}/equity_curves.png"
    write_equity_overlay(records, overlay_path)

    log.info("Generating HTML reports...")
    generate_all_html_reports(records, config.REPORTS_DIR)
    generate_summary_html(records, config.REPORTS_DIR)

    log.info("=" * 60)
    log.info("DONE. Reports in: %s/", config.REPORTS_DIR)
    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
