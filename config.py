"""Global configuration for BTC backtest framework. See SPEC.md §4."""

from datetime import datetime

# ============================================================
# DATA
# ============================================================
EXCHANGE = "binance"
SYMBOL = "BTC/USDT"
TIMEFRAMES = ["1d", "4h"]

# Backtest range (trades counted)
# Extended test 2018-2025 (Binance BTC/USDT has data from Aug 2017).
BACKTEST_START = datetime(2018, 1, 1)
BACKTEST_END = datetime(2025, 12, 31)

# Data fetch range (request earliest possible from Binance — gets Aug 2017 onwards).
DATA_START = datetime(2015, 1, 1)
DATA_END = datetime(2025, 12, 31)

# In-sample / out-of-sample split
# IS: 2018-2023 (6 years). OOS: 2024-2025 (2 years).
IN_SAMPLE_END = datetime(2023, 12, 31)
OUT_OF_SAMPLE_START = datetime(2024, 1, 1)

# ============================================================
# CAPITAL
# ============================================================
INITIAL_CAPITAL = 1_000.0  # USD

# ============================================================
# COSTS
# ============================================================
SPOT_FEE = 0.001          # 0.1% per side
FUTURES_FEE = 0.0005      # 0.05% per side
SLIPPAGE = 0.0005         # 0.05% per execution
FUTURES_FUNDING_DAILY = 0.0001  # 0.01% per day, applied at 00:00 UTC

MAINTENANCE_MARGIN = 0.005   # 0.5%
LIQUIDATION_PENALTY = 0.01   # extra 1% adverse on liquidation

# ============================================================
# RISK / SIZING — ATR-based (A, B, C)
# ============================================================
RISK_PER_TRADE = 0.01
MAX_NOTIONAL_FRACTION = {
    "spot_1x":    0.50,
    "futures_1x": 0.50,
    "futures_2x": 1.00,
}

# ============================================================
# RISK / SIZING — Vol-targeting (D)
# ============================================================
VOL_TARGET_ANNUAL = 0.30
VOL_LOOKBACK_DAYS = 30
VOL_MIN_NOTIONAL_FRACTION = 0.05
VOL_MAX_NOTIONAL_FRACTION = {
    "spot_1x":    1.00,
    "futures_1x": 1.00,
    "futures_2x": 2.00,
}
ANNUALIZATION_DAYS = 365  # crypto = 24/7

# ============================================================
# BACKTEST MODES
# ============================================================
MODES = ["spot_1x", "futures_1x", "futures_2x"]
RISK_FREE_RATE = 0.0

# ============================================================
# OUTPUT
# ============================================================
REPORTS_DIR = "reports"
DATA_DIR = "data"
LOG_LEVEL = "INFO"

# Derived paths
CACHE_DB_PATH = f"{DATA_DIR}/cache.sqlite"


def get_fee_for_mode(mode: str) -> float:
    """Returns fee per side for a given mode."""
    return SPOT_FEE if mode == "spot_1x" else FUTURES_FEE


def get_leverage_for_mode(mode: str) -> float:
    return {"spot_1x": 1.0, "futures_1x": 1.0, "futures_2x": 2.0}[mode]


def is_futures(mode: str) -> bool:
    return mode.startswith("futures_")
