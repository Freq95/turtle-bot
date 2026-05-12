# SPEC — BTC Trading Bot Backtesting Framework

**Versiune:** 1.2
**Data:** 2026-05-11
**Status:** IMPLEMENTED (v1, tests deferred)

**Changelog:**
- v1.2 (2026-05-11): Implementation deviations documented:
  - **pandas-ta removed**: package 0.3.14b0 nu mai e pe PyPI, iar 0.4.71b0 are conflict numpy (cere ≥2.2.6 vs pandas 2.2 cere <2). Înlocuit cu indicatori inline în `backtest/indicators.py` (EMA, SMA, ATR, RSI, ADX, Donchian). Wilder's smoothing aproximat ca `ewm(alpha=1/N, adjust=False)`.
  - **Gap validation relaxat**: SPEC §5.5 spunea "abort la gap". În practică Binance are 2 maintenance windows în 2019-2020 cu 1 bar 4h lipsă fiecare. Relaxat la "warning + continuă" pentru gap-uri ≤1 bar consecutiv. Abort doar pentru gap-uri ≥2 bars consecutiv (date corupte).
  - **End-of-2024 4h bars**: cu `DATA_END = datetime(2024, 12, 31)` (= 00:00 UTC), bars 4h pe 2024-12-31 04:00-20:00 nu sunt incluse. Impact minim: doar Strategia A pierde 5 oportunități de semnal pe ultima zi. Daily complete (force-close EOB folosește close-ul daily 2024-12-31). Recomandare future: trecut DATA_END la `datetime(2024, 12, 31, 23, 59, 59)` pentru completitudine.
- v1.1 (2026-05-11): Custom backtest engine confirmat (§2). Unit tests deferred for v1 (§24 documentat ca referință, dar nu se implementează acum).
- v1.0 (2026-05-11): Initial spec.

Acest document e specificația contractuală completă pentru implementare. Nu e plan, nu e schiță — fiecare detaliu de aici trebuie respectat exact. Orice ambiguitate ulterioară se rezolvă prin clarificare scrisă, nu prin presupunere.

---

## CUPRINS

1. [Overview](#1-overview)
2. [Tech Stack & Dependencies](#2-tech-stack--dependencies)
3. [Project Structure](#3-project-structure)
4. [Configuration (`config.py`)](#4-configuration-configpy)
5. [Data Pipeline](#5-data-pipeline)
6. [SQLite Cache Schema](#6-sqlite-cache-schema)
7. [Execution Model & Anti-Look-Ahead](#7-execution-model--anti-look-ahead)
8. [Cost Model](#8-cost-model)
9. [Liquidation Model (Futures 2x)](#9-liquidation-model-futures-2x)
10. [Funding Rate Model (Futures)](#10-funding-rate-model-futures)
11. [Position Sizing](#11-position-sizing)
12. [Equity Tracking](#12-equity-tracking)
13. [Strategy A — Complex Multi-Layer](#13-strategy-a--complex-multi-layer)
14. [Strategy B — Simple Pure Trend](#14-strategy-b--simple-pure-trend)
15. [Strategy C — Hybrid](#15-strategy-c--hybrid)
16. [Strategy D-Primary — Vol-Targeted Donchian (55/20)](#16-strategy-d-primary--vol-targeted-donchian-5520)
17. [Strategy D-Alt — Robustness Variants](#17-strategy-d-alt--robustness-variants)
18. [Benchmark — Buy & Hold](#18-benchmark--buy--hold)
19. [Test Matrix (15 backtests)](#19-test-matrix-15-backtests)
20. [Metrics & Definitions](#20-metrics--definitions)
21. [In-Sample / Out-of-Sample Protocol](#21-in-sample--out-of-sample-protocol)
22. [Output Deliverables](#22-output-deliverables)
23. [Edge Cases & Conventions](#23-edge-cases--conventions)
24. [Validation & Testing](#24-validation--testing)
25. [Implementation Phases](#25-implementation-phases)
26. [Final Verdict Reporting](#26-final-verdict-reporting)

---

## 1. Overview

### Scop

Framework de backtesting pentru evaluarea empirică a 4 strategii de trading BTC pe perioada 2020-01-01 → 2024-12-31 (5 ani), cu $10,000 capital inițial, pe spot și futures (1x și 2x leverage). Scopul: determinare informată a strategiei optime pentru implementare live.

### Non-scop

- **NU** e un bot live de trading. Doar backtest.
- **NU** garantează performanță viitoare. Output-ul e o decizie informată, nu o profeție.
- **NU** include optimizare automată de parametri. Toți parametrii sunt fixați a priori.

### Deliverables principale

1. Codul complet al framework-ului (rulabil din `main.py`)
2. CSV master cu toate metricile pentru cele 15 backtests
3. HTML reports per-strategie cu equity curves, drawdowns, trade lists
4. Equity curves overlay (un singur PNG comparativ)
5. Verdict final scris cu ranking out-of-sample

---

## 2. Tech Stack & Dependencies

### Runtime
- **Python:** 3.11+ (testat pe 3.11 și 3.12)
- **OS:** Cross-platform (Windows, Linux, macOS)

### Engine de backtest
**Custom engine** (NU `backtesting.py`, NU `vectorbt`). Justificare:
- Strategia A folosește **mixed timeframes** (Daily regime + 4h entry) — nesuportat nativ în `backtesting.py`
- Long+Short pe aceeași strategie cu reguli asimetrice — complicat în librării externe
- Funding rate + liquidation futures — necesită model custom
- Vol-targeting (D) cu rolling window — mai curat în loop propriu
- Circuit breakers cu state complex (A) — mai natural în engine propriu
- Debug clar, no magic, ~600 linii estimate pentru engine complet

### `requirements.txt`

```
ccxt==4.2.15
pandas==2.2.0
numpy==1.26.4
pandas-ta==0.3.14b0
matplotlib==3.8.2
jinja2==3.1.3
python-dateutil==2.8.2
```

(`pytest` adăugat doar când reactivăm §24 — vezi changelog v1.1.)

### Indicatori
**pandas-ta** (explicit, NU ta-lib). Motiv: pure Python, no C compilation pe Windows.

Funcții folosite:
- `ta.ema(close, length=N)` — EMA21, EMA50, EMA200
- `ta.atr(high, low, close, length=14)` — ATR(14)
- `ta.rsi(close, length=14)` — RSI(14)
- `ta.adx(high, low, close, length=14)` — ADX(14) (returnează DataFrame cu coloana `ADX_14`)
- `ta.sma(volume, length=20)` — SMA20(Volume)

Pentru **Donchian channels** (Strategia D), calcul direct cu pandas:
```python
upper_55 = df['high'].rolling(55).max().shift(1)  # exclude current bar
lower_20 = df['low'].rolling(20).min().shift(1)
```

### Date
- **ccxt** pentru descărcare OHLCV de la Binance
- Exchange ID: `binance` (Binance.com global, nu .us)
- Symbol: `BTC/USDT`
- Timeframes folosite: `1d` și `4h`

### Storage
- **SQLite** (built-in Python via `sqlite3`)
- Single file: `data/cache.sqlite`
- Schema în secțiunea 6

### Reports
- **jinja2** pentru template HTML
- **matplotlib** pentru grafice, embedded ca PNG base64 în HTML (no external image files)
- Charts NU folosesc plotly/seaborn (păstrăm minimal dependencies)

### Testing
- **DEFERRED pentru v1.** Unit tests amânate per decizia din 2026-05-11. Specul rămâne documentat în §24 ca referință pentru când vor fi adăugate. Validarea v1 = §24.2 (manual sanity checks) + run end-to-end.

---

## 3. Project Structure

```
m-trade/
├── data/
│   ├── cache.sqlite               # OHLCV cache (gitignored)
│   ├── btc_daily_2019_2024.csv    # Backup CSV (gitignored)
│   └── btc_4h_2019_2024.csv       # Backup CSV (gitignored)
├── strategies/
│   ├── __init__.py
│   ├── base.py                    # BaseStrategy ABC
│   ├── strategy_a.py              # Strategy A
│   ├── strategy_b.py              # Strategy B
│   ├── strategy_c.py              # Strategy C
│   └── strategy_d.py              # Strategy D (Primary + Alt variants)
├── benchmarks/
│   ├── __init__.py
│   └── buy_and_hold.py
├── backtest/
│   ├── __init__.py
│   ├── engine.py                  # Custom backtest loop
│   ├── position.py                # Position dataclass + lifecycle
│   ├── costs.py                   # Fees, slippage, funding, liquidation
│   ├── metrics.py                 # All performance metrics
│   └── runner.py                  # Runs full matrix of 15 backtests
├── analysis/
│   ├── __init__.py
│   ├── compare.py                 # Master CSV generation
│   ├── reports.py                 # HTML report generation
│   └── templates/
│       └── report.html.j2         # Jinja2 template
├── data_loader.py                 # ccxt download + SQLite cache
├── config.py                      # All parameters
├── main.py                        # Entry point
├── tests/                         # DEFERRED for v1 — not created until §24 reactivated
│   ├── __init__.py
│   ├── test_data_loader.py
│   ├── test_indicators.py
│   ├── test_position.py
│   ├── test_costs.py
│   ├── test_metrics.py
│   ├── test_strategy_a.py
│   ├── test_strategy_b.py
│   ├── test_strategy_c.py
│   ├── test_strategy_d.py
│   └── test_engine.py
├── reports/                       # Generated outputs (gitignored)
│   ├── master.csv
│   ├── equity_curves.png
│   ├── strategy_a_spot.html
│   └── ... (per backtest)
├── plan.md                        # Original strategy plan
├── SPEC.md                        # THIS FILE
├── README.md                      # How to run
├── requirements.txt
├── .gitignore
└── pyproject.toml                 # Optional, for tooling config
```

### `.gitignore` content
```
__pycache__/
*.pyc
data/cache.sqlite
data/*.csv
reports/
.pytest_cache/
.venv/
venv/
.env
```

---

## 4. Configuration (`config.py`)

Toți parametrii globali într-un singur fișier. Strategiile pot avea parametri proprii în clasele lor.

```python
# config.py — Global configuration

from datetime import datetime

# ============================================================
# DATA
# ============================================================
EXCHANGE = "binance"
SYMBOL = "BTC/USDT"
TIMEFRAMES = ["1d", "4h"]

# Backtest range (trades counted)
BACKTEST_START = datetime(2020, 1, 1)   # inclusiv
BACKTEST_END = datetime(2024, 12, 31)   # inclusiv (close)

# Data fetch range (with warmup for EMA200)
DATA_START = datetime(2019, 6, 1)
DATA_END = datetime(2024, 12, 31)

# In-sample / out-of-sample split
IN_SAMPLE_END = datetime(2023, 12, 31)
OUT_OF_SAMPLE_START = datetime(2024, 1, 1)

# ============================================================
# CAPITAL
# ============================================================
INITIAL_CAPITAL = 10_000.0  # USD

# ============================================================
# COSTS (per spec, simplified)
# ============================================================
SPOT_FEE = 0.001          # 0.1% per side
FUTURES_FEE = 0.0005      # 0.05% per side
SLIPPAGE = 0.0005         # 0.05% per execution
FUTURES_FUNDING_DAILY = 0.0001  # 0.01% per day applied at 00:00 UTC

# Liquidation (futures only)
MAINTENANCE_MARGIN = 0.005   # 0.5%
LIQUIDATION_PENALTY = 0.01   # Extra 1% fee on liquidation

# ============================================================
# RISK / SIZING (Strategy A/B/C - ATR based)
# ============================================================
RISK_PER_TRADE = 0.01      # 1% of equity at risk per trade
MAX_NOTIONAL_FRACTION = {  # per mode (spot/futures × leverage)
    "spot_1x":    0.50,    # 50% equity max notional
    "futures_1x": 0.50,    # 50% equity max notional
    "futures_2x": 1.00,    # 100% equity max notional (50% × 2)
}

# ============================================================
# RISK / SIZING (Strategy D - vol-targeting)
# ============================================================
VOL_TARGET_ANNUAL = 0.30           # 30% annualized portfolio vol target
VOL_LOOKBACK_DAYS = 30             # rolling window for sigma estimation
VOL_MIN_NOTIONAL_FRACTION = 0.05   # below 5% → skip trade
VOL_MAX_NOTIONAL_FRACTION = {
    "spot_1x":    1.00,
    "futures_1x": 1.00,
    "futures_2x": 2.00,
}
ANNUALIZATION_DAYS = 365  # Crypto = 24/7, no trading-day discount

# ============================================================
# BACKTEST MODES
# ============================================================
MODES = ["spot_1x", "futures_1x", "futures_2x"]

# Risk-free rate for Sharpe
RISK_FREE_RATE = 0.0

# ============================================================
# OUTPUT
# ============================================================
REPORTS_DIR = "reports"
DATA_DIR = "data"
LOG_LEVEL = "INFO"   # DEBUG | INFO | WARNING | ERROR
```

---

## 5. Data Pipeline

### 5.1 Source

- **Exchange:** Binance (binance.com global), accessed via `ccxt.binance({})`
- **Symbol:** `BTC/USDT`
- **Timeframes:**
  - `1d` (daily): bars aligned to 00:00 UTC, close at 23:59:59 UTC of same day
  - `4h`: bars at 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC

### 5.2 Range

- **Fetch:** 2019-06-01 → 2024-12-31 (≈5.5 ani, include 7 luni warmup pentru EMA200)
- **Backtest counted:** 2020-01-01 → 2024-12-31 (5 ani)
- Warmup-ul asigură că la 2020-01-01 toți indicatorii (EMA200, ADX(14), etc.) sunt full warm.

### 5.3 Volume convention

Binance ccxt returnează volumul în **moneda de bază** (BTC pentru BTC/USDT). Toate cifrele de volum din spec se referă la **BTC volume**, nu USDT volume. Asta e consistent cu interpretarea standard a indicatorilor (SMA20 pe BTC volume).

### 5.4 Algoritm de fetch

```python
def fetch_ohlcv(exchange, symbol, timeframe, start_dt, end_dt):
    """
    Fetch all OHLCV bars between start_dt and end_dt.
    Binance limit: 1000 bars per call. Paginate via 'since' timestamp.
    """
    since = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    all_bars = []

    while since < end_ms:
        bars = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not bars:
            break
        all_bars.extend(bars)
        last_ts = bars[-1][0]
        if last_ts <= since:  # no progress, avoid infinite loop
            break
        since = last_ts + 1  # next bar
        # Respect rate limits
        time.sleep(exchange.rateLimit / 1000)

    return all_bars  # list of [timestamp_ms, o, h, l, c, v]
```

### 5.5 Validare date

După fetch, înainte de stocare, rulează:

**Validare 1 — No gaps:**
- Pentru `1d`: timestamp-urile consecutive trebuie să difere cu exact 86400000 ms (1 zi)
- Pentru `4h`: 14400000 ms (4 ore)
- Dacă apare gap → log warning + abort (nu continuăm cu date incomplete)

**Validare 2 — No anomalies:**
- Price change bar-to-bar: `abs(close[t] - close[t-1]) / close[t-1]` > 0.5 (50%) → flag warning
- Volume = 0 pe un bar → flag warning
- High < Low pe un bar → abort (date corupte)
- High < max(Open, Close) sau Low > min(Open, Close) → abort

**Validare 3 — Completitudine:**
- Daily: așteptăm 365 × 5.5 ≈ 2008 bars între 2019-06-01 și 2024-12-31. Tolerăm ±2 bars.
- 4h: așteptăm 6 × 2008 ≈ 12048 bars. Tolerăm ±10 bars.

### 5.6 Caching strategy

- Înainte de fetch: check SQLite pentru date existente
- Dacă (symbol, timeframe) deja conține datele complete pentru range-ul cerut → skip fetch
- Dacă date parțiale → fetch doar gap-ul
- După fetch reușit + validare: insert/replace în SQLite + export CSV backup

### 5.7 CSV backup format

```
timestamp,open,high,low,close,volume
2019-06-01T00:00:00Z,8568.34,8674.56,8521.78,8625.43,12345.67
...
```

Timestamps in ISO 8601 UTC, comma-separated, header pe primul row.

---

## 6. SQLite Cache Schema

Single table:

```sql
CREATE TABLE IF NOT EXISTS ohlcv (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp INTEGER NOT NULL,  -- milliseconds since epoch, UTC
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    PRIMARY KEY (symbol, timeframe, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_lookup
    ON ohlcv (symbol, timeframe, timestamp);
```

Insert strategy: `INSERT OR REPLACE` (idempotent re-runs).

No retention policy — păstrăm toate datele. Dimensiune estimată: ~12k bars × 50 bytes ≈ 600KB. Negligible.

---

## 7. Execution Model & Anti-Look-Ahead

### 7.1 Reguli universale

**R1 — Indicator computation:**
Indicatorii pentru bar `t` sunt calculați folosind DOAR date `[0..t]`. Pentru "exclude current bar" (cum e Volume SMA20 sau Donchian channel), folosim `[0..t-1]`.

**R2 — Signal timing:**
Semnalele se evaluează la **close of bar t**. Execuția se face la **open of bar t+1**.

- Pentru strategii cu **timeframe daily** (B, C, D): semnal pe close daily bar t → execuție pe open daily bar t+1
- Pentru **Strategia A** (mixed): semnal pe close 4h bar t (cu regime check pe ultimul bar daily închis) → execuție pe open 4h bar t+1

**R3 — Stop loss triggering:**
Stop losses sunt active intra-bar. Pe orice bar t+1 după entry:
- Long stop hit dacă `Low[t+1] <= stop_price`
- Short stop hit dacă `High[t+1] >= stop_price`
- Fill price:
  - Long: `min(Open[t+1], stop_price)` apoi aplicat slippage downward
  - Short: `max(Open[t+1], stop_price)` apoi aplicat slippage upward

Asta acoperă cazul gap-urilor (open dincolo de stop → fill la open, nu la stop).

**R4 — Signal-based exits (close-based rules):**
Reguli de tip "Close < EMA50 → SELL" se evaluează pe **close of bar t**, fill la **open of bar t+1** cu slippage.

### 7.2 Multi-timeframe alignment (Strategia A)

Strategia A folosește Daily pentru regime și 4h pentru entry. Sincronizare:

```
La close-ul fiecărui 4h bar t:
    1. Identifică ultimul daily bar complet închis (call it D_last)
       - 4h bar 00:00 UTC: D_last = ziua de ieri
       - 4h bar 04:00-20:00 UTC: D_last = ziua curentă (already closed at 00:00 today)
       - Wait — Daily bars close at 24:00 UTC. So at 4h bar 04:00 UTC, last closed daily is yesterday.
       - At 4h bar at exactly 00:00 UTC, last closed daily is yesterday.
    2. Evaluează regime (Layer 1 + Layer 2) folosind D_last
    3. Evaluează entry condition (Layer 3) folosind 4h bar t
    4. Dacă toate condițiile match → entry la 4h bar t+1 open
```

**Convenție strictă:** Daily bar al zilei N se consideră închis la 00:00 UTC al zilei N+1 (echivalent cu close-ul 23:59:59 al zilei N). 4h bar al orei H se închide la H+4:00 UTC.

La 4h bar = 00:00 UTC al zilei N: D_last = ziua N-1 (just closed).
La 4h bar = 04:00 UTC al zilei N: D_last = ziua N-1 (today's daily not closed yet).
La 4h bar = 04:00 UTC al zilei N+1: D_last = ziua N (now closed).

În cod, asta înseamnă:
```python
last_complete_daily_idx = daily_df.index.searchsorted(four_h_bar_close_time, side='right') - 1
# Adjust by one if 4h bar closes exactly at daily close time
if daily_df.index[last_complete_daily_idx] == four_h_bar_close_time:
    pass  # this daily bar just closed, use it
elif daily_df.index[last_complete_daily_idx] > four_h_bar_close_time:
    last_complete_daily_idx -= 1
```

### 7.3 Operations sequence per bar (engine loop)

For each bar t in chronological order:

```
1. Mark equity to current bar close (or open for execution bars)
2. Apply funding rate if futures and 00:00 UTC and position open (see §10)
3. Check liquidation if futures 2x (see §9)
4. Check intra-bar stops (R3)
5. If stop triggered → execute exit at fill price, update equity
6. Compute indicators for bar t (using only [0..t] data)
7. Evaluate strategy signals on bar t close
8. If signal generated → enqueue execution for bar t+1 open
9. End of bar
At start of bar t+1:
10. Execute enqueued orders at Open[t+1] (with slippage)
```

---

## 8. Cost Model

### 8.1 Fees

| Type | Rate | Applied |
|------|------|---------|
| Spot | 0.001 (0.1%) | Per side (entry AND exit) |
| Futures | 0.0005 (0.05%) | Per side (entry AND exit) |

Formula:
```python
fee_cost = abs(notional_value) * fee_rate
```

Fee se scade din cash imediat la execuție.

### 8.2 Slippage

```python
SLIPPAGE = 0.0005  # 0.05%
```

Aplicare:
- **Long entry (buy):** execute la `open * (1 + SLIPPAGE)`
- **Long exit (sell):** execute la `open * (1 - SLIPPAGE)`
- **Short entry (sell short):** execute la `open * (1 - SLIPPAGE)`
- **Short exit (cover):** execute la `open * (1 + SLIPPAGE)`

Pentru stop fills:
- Long stop sell: execute la `min(open, stop_price) * (1 - SLIPPAGE)`
- Short stop cover: execute la `max(open, stop_price) * (1 + SLIPPAGE)`

Slippage NU se scade separat din cash — e încorporat în price-ul de execuție.

### 8.3 Aplicare per trade

Total cost per trade roundtrip:
- Spot 1x: 0.1% (entry) + 0.05% (slippage entry) + 0.1% (exit) + 0.05% (slippage exit) = **0.30%**
- Futures 1x/2x: 0.05% + 0.05% + 0.05% + 0.05% = **0.20%** (+ funding)

---

## 9. Liquidation Model (Futures 2x)

Aplicat **doar** pe variantele `futures_2x`. Pe `futures_1x` și `spot_1x`, liquidare nu se modelează (margin 100%/100%).

### 9.1 Liquidation price calculation

Pentru long la entry price `P` cu leverage 2x:
```
margin_used_fraction = 1 / 2 = 0.5
maintenance_margin = 0.005

liquidation_price_long = P * (1 - (margin_used_fraction - maintenance_margin))
                      = P * (1 - 0.495)
                      = P * 0.505
```

Pentru short:
```
liquidation_price_short = P * (1 + (margin_used_fraction - maintenance_margin))
                       = P * 1.495
```

### 9.2 Liquidation triggering

La fiecare bar nou (start of bar t+1):
- Long position: if `Open[t+1] <= liquidation_price_long` → liquidate
- Long position: if `Low[t+1] <= liquidation_price_long` AND `Open[t+1] > liquidation_price_long` → liquidate at `liquidation_price_long`
- Short position: similar mirror

Liquidation fill price:
- Long liquidation: `min(Open[t+1], liquidation_price_long) * (1 - LIQUIDATION_PENALTY)`
- Short liquidation: `max(Open[t+1], liquidation_price_short) * (1 + LIQUIDATION_PENALTY)`

`LIQUIDATION_PENALTY = 0.01` (extra 1% adverse).

### 9.3 Post-liquidation

- Equity goes to whatever's left (can be near 0 but not below 0 in this model)
- Continue backtest with remaining equity
- Log liquidation event with timestamp + entry/exit prices

---

## 10. Funding Rate Model (Futures)

### 10.1 Application

Doar pe variante futures. Spot nu are funding.

```python
FUTURES_FUNDING_DAILY = 0.0001  # 0.01% per day
```

### 10.2 Timing

- Funding aplicat **la fiecare 00:00 UTC**, pe baza notional-ului poziției deschise la acel moment.
- Dacă nu există poziție deschisă la 00:00 UTC → no funding charge.
- Dacă poziția e deschisă/închisă în aceeași zi UTC fără să "supraviețuiască" peste 00:00 UTC → no funding.

### 10.3 Direction

- **Long position:** plătește funding → `cash -= notional * funding_rate`
- **Short position:** primește funding → `cash += notional * funding_rate`

Implementare în engine:
```python
def apply_funding(self, bar):
    if not self.is_futures:
        return
    if bar.timestamp_utc.hour == 0 and bar.timestamp_utc.minute == 0:
        if self.position is not None:
            notional = abs(self.position.units * bar.open)
            funding = notional * FUTURES_FUNDING_DAILY
            if self.position.side == "long":
                self.cash -= funding
            else:  # short
                self.cash += funding
```

**Notă:** pentru daily timeframe, fiecare bar începe la 00:00 UTC, deci funding se aplică o dată per bar. Pentru 4h timeframe, doar bar-ul 00:00 UTC declanșează funding.

---

## 11. Position Sizing

### 11.1 ATR-based sizing (A, B, C)

```python
def compute_position_size_atr(equity, entry_price, atr_value, mode):
    """
    Returns: (units, notional_value)
    """
    stop_distance_pct = (2 * atr_value) / entry_price
    risk_amount = RISK_PER_TRADE * equity  # 1% equity
    
    # Raw position size from risk
    target_notional = risk_amount / stop_distance_pct
    
    # Apply cap
    max_notional = MAX_NOTIONAL_FRACTION[mode] * equity
    notional = min(target_notional, max_notional)
    
    units = notional / entry_price
    return units, notional
```

`MAX_NOTIONAL_FRACTION`:
- `spot_1x`: 0.50
- `futures_1x`: 0.50
- `futures_2x`: 1.00

### 11.2 Vol-targeted sizing (D)

```python
def compute_position_size_voltarget(equity, daily_returns_history, mode):
    """
    Returns: (units, notional_value) or (0, 0) if skip
    
    daily_returns_history: pd.Series of last 30 days log returns
    """
    if len(daily_returns_history) < VOL_LOOKBACK_DAYS:
        return 0, 0  # not enough history, skip
    
    sigma_daily = daily_returns_history.std(ddof=1)  # sample stdev
    sigma_annual = sigma_daily * (ANNUALIZATION_DAYS ** 0.5)
    
    if sigma_annual <= 0:
        return 0, 0  # degenerate case
    
    target_fraction = VOL_TARGET_ANNUAL / sigma_annual
    
    if target_fraction < VOL_MIN_NOTIONAL_FRACTION:
        return 0, 0  # vol too high, skip
    
    max_fraction = VOL_MAX_NOTIONAL_FRACTION[mode]
    fraction = min(target_fraction, max_fraction)
    
    notional = fraction * equity
    # units computed at entry execution (next bar open price)
    return None, notional  # units finalized at execution
```

(Units sunt calculate la execuție, când avem entry price-ul real.)

### 11.3 Loss reduction (Strategy A only)

După 3 losses consecutive: position size halved pentru următoarele 5 trades.
- "Loss" = trade roundtrip cu PnL < 0
- Contor "3 consecutive losses" se resetează la primul win
- Contor "5 trades" decrementează pentru fiecare trade nou (win sau loss)
- Reset complet ambele contoare după "5 trades" expirate

Implementare:
```python
class ConsecutiveLossTracker:
    def __init__(self):
        self.consecutive_losses = 0
        self.reduced_size_trades_remaining = 0
    
    def on_trade_close(self, pnl):
        if self.reduced_size_trades_remaining > 0:
            self.reduced_size_trades_remaining -= 1
        
        if pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= 3 and self.reduced_size_trades_remaining == 0:
                self.reduced_size_trades_remaining = 5
                self.consecutive_losses = 0  # reset counter
        else:
            self.consecutive_losses = 0
    
    @property
    def size_multiplier(self):
        return 0.5 if self.reduced_size_trades_remaining > 0 else 1.0
```

---

## 12. Equity Tracking

### 12.1 Model

Tracking în două variabile:
- `cash`: USD balance (poate include profit realizat + costuri scoase)
- `position`: dacă există, conține (side, units, entry_price, mode)

**Spot model:**
- Entry long: `cash -= units * entry_exec_price + fee`
- Hold: `equity = cash + units * current_price`
- Exit: `cash += units * exit_exec_price - fee`

**Futures model:**
- Entry: `cash -= notional * fee` (no cash deducted for position itself — uses margin, modeled implicitly)
- Hold: `equity = cash + (current_price - entry_price) * units * side_sign`
  - `side_sign = +1` for long, `-1` for short
- Funding (00:00 UTC): `cash -= notional * funding_rate * side_sign`
- Exit: `cash += (exit_price - entry_price) * units * side_sign - notional * fee`

### 12.2 Mark-to-market frequency

- La fiecare bar close: recompute equity using close price
- La fiecare bar open (înainte de execuții): recompute equity using open price (for circuit breaker checks pe Strategia A)
- La trigger de stop/liquidation: recompute la fill price

### 12.3 Daily equity series

Output: pd.Series indexat pe daily close timestamps, valori = equity la close of each daily bar.

Pentru strategii cu execuții 4h (A): equity-ul pe daily bar = ultima valoare la 4h bar close de la 20:00 UTC.

---

## 13. Strategy A — Complex Multi-Layer

### 13.1 Overview

```
Timeframes:  Daily (regime) + 4h (entry/management)
Indicators:  EMA50_d, EMA200_d, EMA21_4h, ADX14_d, RSI14_4h, ATR14_4h, SMA20(Vol)_4h
Direction:   Long-only on spot. Long+Short on futures.
Max position: One at a time.
```

### 13.2 Indicators — exact computation

Pe daily DataFrame:
- `EMA50_d` = `ta.ema(close, length=50)`
- `EMA200_d` = `ta.ema(close, length=200)`
- `ADX14_d` = `ta.adx(high, low, close, length=14)['ADX_14']`

Pe 4h DataFrame:
- `EMA21_4h` = `ta.ema(close, length=21)`
- `RSI14_4h` = `ta.rsi(close, length=14)`
- `ATR14_4h` = `ta.atr(high, low, close, length=14)`
- `SMA20_Vol_4h` = `ta.sma(volume, length=20).shift(1)`  ← shift(1) excludes current bar

### 13.3 Entry rules — LONG (all modes)

La close-ul fiecărui 4h bar `t` (în zona de backtest, după warmup):

```
Let D_last = last completed daily bar at time t

LAYER 1 (regime active):
    ADX14_d[D_last] >= 25

LAYER 2 (direction bias LONG):
    EMA50_d[D_last] > EMA200_d[D_last]  AND
    Close_d[D_last] > EMA50_d[D_last]

LAYER 3 (entry signal LONG):
    Low_4h[t] <= EMA21_4h[t] <= High_4h[t]      # pullback (bar crosses EMA21)
    AND 40 <= RSI14_4h[t] <= 55
    AND Close_4h[t] > Open_4h[t]                # bullish close
    AND Volume_4h[t] > SMA20_Vol_4h[t]          # SMA20 excluding current bar

LAYER 6 (circuit breaker check — see §13.7):
    Not in any "bot off" state

If ALL conditions:
    → BUY at Open_4h[t+1] (with slippage)
    → Size per §13.4
```

### 13.4 Position Sizing (LONG/SHORT)

```python
atr_at_entry = ATR14_4h[t]                   # ATR at signal bar
entry_price = Open_4h[t+1] * (1 + SLIPPAGE)  # actual exec price
stop_distance_pct = (2 * atr_at_entry) / entry_price
risk_amount = RISK_PER_TRADE * equity_now

target_notional = risk_amount / stop_distance_pct
max_notional = MAX_NOTIONAL_FRACTION[mode] * equity_now
notional = min(target_notional, max_notional)

# Apply consecutive-loss reducer
notional *= consecutive_loss_tracker.size_multiplier

units = notional / entry_price
```

If `notional < 1.0` USD (effectively zero): skip trade.

### 13.5 Entry rules — SHORT (futures only)

Toate condițiile oglindă. La close-ul fiecărui 4h bar `t`:

```
LAYER 1: ADX14_d[D_last] >= 25
LAYER 2 SHORT bias:
    EMA50_d[D_last] < EMA200_d[D_last]  AND
    Close_d[D_last] < EMA50_d[D_last]
LAYER 3 SHORT entry:
    Low_4h[t] <= EMA21_4h[t] <= High_4h[t]      # same pullback definition
    AND 45 <= RSI14_4h[t] <= 60
    AND Close_4h[t] < Open_4h[t]                # bearish close
    AND Volume_4h[t] > SMA20_Vol_4h[t]
LAYER 6: not in bot-off state

If all → SHORT SELL at Open_4h[t+1] (slippage downward)
```

### 13.6 Trade Management

Stocate la entry: `entry_price`, `atr_at_entry`, `side`, `units`.

**Initial Stop Loss** (fixed at entry):
- Long: `stop_initial = entry_price - 2 * atr_at_entry`
- Short: `stop_initial = entry_price + 2 * atr_at_entry`

**State machine after entry:**

```
State: "INITIAL"
    stop = stop_initial
    
    At each 4h bar t after entry:
        # Check break-even trigger
        IF state == "INITIAL":
            For long: if High_4h[t] >= entry_price + 1.5 * atr_at_entry:
                stop = entry_price   # move to break-even
                state = "BREAKEVEN_TRAIL"
            For short: if Low_4h[t] <= entry_price - 1.5 * atr_at_entry:
                stop = entry_price
                state = "BREAKEVEN_TRAIL"
        
        # Update trailing stop in TRAIL state
        IF state == "BREAKEVEN_TRAIL":
            atr_now = ATR14_4h[t]
            For long:
                peak = max(peak_so_far, High_4h[t])
                new_trail = peak - 3 * atr_now
                stop = max(stop, new_trail)   # ratchet only up
            For short:
                trough = min(trough_so_far, Low_4h[t])
                new_trail = trough + 3 * atr_now
                stop = min(stop, new_trail)   # ratchet only down
```

**Note:** trailing uses `peak/trough = max/min of HIGH/LOW since entry` (not close). Decision per literature: most TF systems trail off bar extremes for stops. For B (different strategy) it's HIGHEST_CLOSE per its spec.

**Stop check intra-bar** (per §7.1 R3):
- Long stop hit if `Low_4h[t] <= stop`
- Short stop hit if `High_4h[t] >= stop`
- Exit at `min/max(Open_4h[t], stop)` with slippage (see §8.2)

**Hard exit — daily regime change:**

At close of each daily bar `D`:
- For long position: if `Close_d[D] < EMA50_d[D]` → flag exit
- For short position: if `Close_d[D] > EMA50_d[D]` → flag exit
- Execute exit at `Open_4h[next_4h_bar].with_slippage`
  - "Next 4h bar" = first 4h bar after daily close (typically the 00:00 UTC bar of next day, or in our convention since daily bars close at end of day = 00:00 UTC next day, this is exactly the same bar)

### 13.7 Circuit breakers

State variables maintained by engine:
- `daily_equity_open`: equity at 00:00 UTC of current day
- `weekly_equity_open`: equity at 00:00 UTC of last Monday
- `peak_equity`: all-time peak since backtest start
- `bot_off_until`: datetime, or None
- `bot_off_permanent`: bool, default False

At each 4h bar close (after equity update):
```
current_equity = equity_now

# 1. Permanent drawdown check
drawdown_pct = 1 - current_equity / peak_equity
if drawdown_pct > 0.08:
    bot_off_permanent = True
    if position is not None:
        FORCE CLOSE at Open_4h[next_bar] with slippage

# 2. Daily loss check
daily_pnl_pct = current_equity / daily_equity_open - 1
if daily_pnl_pct < -0.03:
    bot_off_until = next_00_00_UTC
    if position is not None:
        FORCE CLOSE at Open_4h[next_bar] with slippage

# 3. Weekly loss check
weekly_pnl_pct = current_equity / weekly_equity_open - 1
if weekly_pnl_pct < -0.05:
    bot_off_until = next_Monday_00_00_UTC
    if position is not None:
        FORCE CLOSE at Open_4h[next_bar] with slippage
```

**At Monday 00:00 UTC each week:** `weekly_equity_open = current_equity`

**At every 00:00 UTC daily:** `daily_equity_open = current_equity`, also reset weekly tracker on Mondays.

**At every bar close:** `peak_equity = max(peak_equity, current_equity)`

**Bot-off state means:**
- No new entries allowed
- Existing position closed forcibly at trigger (within same 4h bar window — exit at next 4h bar open)

**Bot-off check before each entry signal:**
```python
def can_trade(now):
    if bot_off_permanent:
        return False
    if bot_off_until is not None and now < bot_off_until:
        return False
    return True
```

### 13.8 Transition LONG ↔ SHORT (futures only)

Per user clarification: minim 1 daily bar fără poziție între LONG și SHORT (sau invers).

Implementare:
- After closing a LONG position, save `last_close_timestamp`
- New SHORT signal accepted only if `current_4h_bar.timestamp - last_close_timestamp >= 1 daily bar duration`
  - Operational definition: cel puțin 1 daily bar a închis complet după închiderea poziției anterioare
- Same constraint for SHORT → LONG

---

## 14. Strategy B — Simple Pure Trend

### 14.1 Overview

```
Timeframe:   Daily ONLY
Indicators:  EMA50_d, EMA200_d, ATR14_d, SMA20(Vol)_d
Direction:   Long-only (ALL modes: spot, futures 1x, futures 2x)
Max position: One at a time.
No circuit breakers. No short.
```

### 14.2 Indicators

- `EMA50_d` = `ta.ema(close, length=50)`
- `EMA200_d` = `ta.ema(close, length=200)`
- `ATR14_d` = `ta.atr(high, low, close, length=14)`
- `SMA20_Vol_d` = `ta.sma(volume, length=20).shift(1)` ← excludes current bar

### 14.3 Entry rules — LONG

At close of each daily bar `t`:

```
ENTRY LONG:
    EMA50_d[t] > EMA200_d[t]            # uptrend
    AND Close_d[t] > EMA50_d[t]         # price above EMA50
    AND Volume_d[t] > 1.2 * SMA20_Vol_d[t]  # volume confirmation

If all:
    → BUY at Open_d[t+1] (with slippage)
    → Size per §11.1
```

### 14.4 Position Sizing

Standard ATR-based (§11.1):
```python
atr_at_entry = ATR14_d[t]
entry_price = Open_d[t+1] * (1 + SLIPPAGE)
stop_distance_pct = (2 * atr_at_entry) / entry_price
target_notional = (0.01 * equity) / stop_distance_pct
max_notional = MAX_NOTIONAL_FRACTION[mode] * equity
notional = min(target_notional, max_notional)
units = notional / entry_price
```

### 14.5 Trade Management — Trailing Stop

Activated IMMEDIATELY after entry (per original spec).

Formula at each daily bar `t` after entry:
```python
# B uses HIGHEST_CLOSE since entry (not high)
highest_close_so_far = max(Close_d[entry_bar..t])

atr_current = ATR14_d[t]  # recalculated each bar
new_trail = highest_close_so_far - 2 * atr_current

# Ratchet up only
trailing_stop = max(trailing_stop, new_trail)
```

On entry day: `trailing_stop = entry_price - 2 * atr_at_entry` (initial value).

**Stop check intra-bar:**
- If `Low_d[t] <= trailing_stop` → flag exit
- Exit at `min(Open_d[t+1], trailing_stop) * (1 - SLIPPAGE)` (or trailing_stop intra-day if Low touched during bar)

**Wait — clarification on stop check timing for B:**

B's trailing uses HIGHEST_CLOSE, so it's updated at each daily close. But the stop trigger can be:
- Intra-bar (Low touches stop during bar) → exit during bar at stop_price
- Close-based (Close < stop at end of bar) → exit at next open

Per §7.1 R3 (universal), use intra-bar triggering with fill at `min(Open[t], stop_price)` after the fact. So:

At start of bar `t+1`:
- Check if `Low_d[t+1] <= trailing_stop` (where trailing_stop was last updated at close of bar `t`)
- If yes: fill at `min(Open_d[t+1], trailing_stop) * (1 - SLIPPAGE)`
- If no: at close of bar `t+1`, update trailing_stop (ratchet), continue

### 14.6 Exit rules

**Exit 1 — Trailing stop hit:** as above.

**Exit 2 — EMA50 break (close-based):**
At close of daily bar `t`:
```
If Close_d[t] < EMA50_d[t]:
    → SELL at Open_d[t+1] * (1 - SLIPPAGE)
```

**Priority:** if both conditions met on same bar (stop hit intra-bar + close < EMA50), stop hit takes precedence (it happens chronologically first within the bar).

### 14.7 Re-entry

After exit, all 3 entry conditions must reappear from scratch:
- `EMA50_d > EMA200_d`
- `Close_d > EMA50_d`
- `Volume_d > 1.2 * SMA20_Vol_d`

No cooldown period required. Same bar that triggers exit cannot trigger re-entry (we exit at next open; new entry signal can come at end of same next bar).

### 14.8 No circuit breakers

Strategy B has NO circuit breakers, NO drawdown halt, NO daily/weekly loss limits. Pure rules-based, infinite loop.

---

## 15. Strategy C — Hybrid

### 15.1 Overview

```
Timeframe:   Daily ONLY
Indicators:  EMA50_d, EMA200_d, ADX14_d, ATR14_d, SMA20(Vol)_d
Direction:   Long-only (ALL modes)
Max position: One at a time.
Circuit breakers: Simplified (drawdown halt + consecutive loss reducer).
```

### 15.2 Indicators

Same as B, plus:
- `ADX14_d` = `ta.adx(high, low, close, length=14)['ADX_14']`

### 15.3 Entry rules — LONG

At close of each daily bar `t`:

```
ENTRY LONG:
    EMA50_d[t] > EMA200_d[t]
    AND Close_d[t] > EMA50_d[t]
    AND ADX14_d[t] > 25                          # FROM Strategy A
    AND Volume_d[t] > 1.2 * SMA20_Vol_d[t]

Circuit breaker check (see §15.6):
    Not in bot-off-permanent state

If all:
    → BUY at Open_d[t+1] (with slippage)
    → Size per §11.1, with consecutive-loss reducer applied
```

### 15.4 Trade Management — Combination

**Initial SL** (fixed at entry):
- `stop_initial = entry_price - 2 * atr_at_entry`

**State machine:**

```
State: "INITIAL"
    stop = stop_initial
    
    At each daily bar t after entry:
        IF state == "INITIAL":
            # Check break-even trigger
            If High_d[t] >= entry_price + 1.5 * atr_at_entry:
                stop = entry_price       # break-even
                state = "TRAILING"
        
        IF state == "TRAILING":
            # Trailing stop activation
            atr_current = ATR14_d[t]
            peak = max(peak_so_far, High_d[t])
            new_trail = peak - 2 * atr_current   # C uses 2×ATR trail (B is also 2, A is 3)
            stop = max(stop, new_trail)
```

**Stop check intra-bar** (R3 universal).

### 15.5 Exit rules

**Exit 1 — Stop hit** (intra-bar, R3).

**Exit 2 — EMA50 break:** at close, `Close_d[t] < EMA50_d[t]` → exit at next open.

### 15.6 Circuit breakers — Simplified

State variables:
- `peak_equity`: all-time peak
- `bot_off_permanent`: bool
- Consecutive loss tracker (§11.3)

At each daily bar close:
```
current_equity = equity_now
peak_equity = max(peak_equity, current_equity)
drawdown_pct = 1 - current_equity / peak_equity

if drawdown_pct > 0.08:
    bot_off_permanent = True
    if position is not None:
        FORCE CLOSE at Open_d[next_bar] * (1 - SLIPPAGE)
```

Consecutive loss tracker (per §11.3) applies size multiplier on entries.

---

## 16. Strategy D-Primary — Vol-Targeted Donchian (55/20)

### 16.1 Overview

```
Timeframe:   Daily ONLY
Indicators:  Donchian channels (55/20), 30-day realized vol
Direction:   Long-only on spot. Long+Short on futures.
Max position: One at a time.
No additional stop loss (channel exit IS the stop).
No circuit breakers (vol-targeting handles risk adaptively).
```

### 16.2 Indicators — exact computation

Per daily DataFrame:
```python
df['donchian_high_55'] = df['high'].rolling(55).max().shift(1)  # exclude current bar
df['donchian_low_20']  = df['low'].rolling(20).min().shift(1)
df['donchian_low_55']  = df['low'].rolling(55).min().shift(1)   # for short entry
df['donchian_high_20'] = df['high'].rolling(20).max().shift(1)  # for short exit

df['log_return'] = (df['close'] / df['close'].shift(1)).apply(lambda x: 0 if x <= 0 else np.log(x))
df['sigma_30d'] = df['log_return'].rolling(30).std(ddof=1)  # sample stdev
df['sigma_annual'] = df['sigma_30d'] * (365 ** 0.5)
```

### 16.3 Entry rules — LONG (all modes)

At close of daily bar `t`:

```
ENTRY LONG:
    Close_d[t] > donchian_high_55[t]
    (close-ul rupe maximul ultimelor 55 zile, exclusiv bar curent)
    
    AND sigma_30d[t] is not NaN  (i.e., we have 30+ days of return history)
    
    AND target_fraction = VOL_TARGET_ANNUAL / sigma_annual[t]
    AND target_fraction >= VOL_MIN_NOTIONAL_FRACTION   (else skip — vol too high)

If conditions met:
    → BUY at Open_d[t+1] * (1 + SLIPPAGE)
    → Size per §11.2
```

### 16.4 Exit rules — LONG

At close of daily bar `t` (while long):
```
EXIT LONG:
    Close_d[t] < donchian_low_20[t]
    
If triggered:
    → SELL at Open_d[t+1] * (1 - SLIPPAGE)
```

**No additional stop loss.** Donchian exit IS the protection. (Theoretical worst case: gap-down through channel exit — modeled normally per R3.)

### 16.5 Entry rules — SHORT (futures only)

At close of daily bar `t`:
```
ENTRY SHORT:
    Close_d[t] < donchian_low_55[t]
    AND sigma not NaN
    AND target_fraction >= min

If conditions met:
    → SHORT SELL at Open_d[t+1] * (1 - SLIPPAGE)
```

### 16.6 Exit rules — SHORT

```
EXIT SHORT:
    Close_d[t] > donchian_high_20[t]

If triggered:
    → COVER at Open_d[t+1] * (1 + SLIPPAGE)
```

### 16.7 Transition LONG ↔ SHORT (futures)

Same rule as A: minim 1 daily bar fără poziție între ele.

In practice for daily strategy: exit bar `t` at open `t+1`, earliest possible new opposite entry is signal at close of `t+1` → exec at open `t+2`. That's 1 full bar gap, satisfies the rule. So this rule is auto-satisfied for daily strategies.

### 16.8 Position Sizing (vol-targeted)

Per §11.2. Repeat for clarity:

```python
sigma_annual = sigma_30d[t] * sqrt(365)
target_fraction = 0.30 / sigma_annual

if target_fraction < 0.05:
    skip trade  # vol too wild
    
fraction = min(target_fraction, MAX_FRACTION[mode])
# MAX_FRACTION: spot_1x=1.00, futures_1x=1.00, futures_2x=2.00

entry_price = Open_d[t+1] * (1 + SLIPPAGE)  # for long
notional = fraction * equity
units = notional / entry_price
```

### 16.9 No state machine

Strategy D doesn't have:
- Break-even logic
- Multiple stop stages
- ATR-based trailing
- Circuit breakers
- Consecutive loss tracker

Single state: "in position" or "not in position".

---

## 17. Strategy D-Alt — Robustness Variants

Strategy D-Alt = identical Strategy D-Primary rules, **doar periodele Donchian se schimbă**.

### 17.1 D-Alt-Short (20/10)

- Donchian entry: `donchian_high_20` (rolling 20-day max, shift 1)
- Donchian exit: `donchian_low_10` (rolling 10-day min, shift 1)
- For short: `donchian_low_20` entry, `donchian_high_10` exit

### 17.2 D-Alt-Medium (40/15)

- Donchian entry: `donchian_high_40`, exit `donchian_low_15`
- For short: `donchian_low_40` entry, `donchian_high_15` exit

### 17.3 Scope of testing

**Only Spot 1x Long-only** for both D-Alt variants. Purpose: parameter sensitivity check, not full evaluation.

**Important:** D-Primary 55/20 is "Strategy D" for verdict purposes. D-Alt variants are reported as robustness analysis only. NO retrospective "best of D" selection allowed.

---

## 18. Benchmark — Buy & Hold

### 18.1 Specification

```
Mode:        Spot 1x
Direction:   Long
Entry:       2020-01-01 at first available daily open
Exit:        2024-12-31 at last available daily close
Fees:        0.1% spot fee on entry (only 1 trade)
Slippage:    0.05% on entry only
No subsequent rebalancing, no DCA, just lump-sum buy and hold.
```

### 18.2 Calculation

```python
entry_price = Open_d[2020-01-01] * (1 + SLIPPAGE)
fee = INITIAL_CAPITAL * SPOT_FEE
cash_after_buy = (INITIAL_CAPITAL - fee)
units = cash_after_buy / entry_price

# Throughout backtest
equity[t] = units * Close_d[t]

# Final
exit_price = Close_d[2024-12-31] * (1 - SLIPPAGE)
final_equity = units * exit_price - units * exit_price * SPOT_FEE
```

Note: dacă vrei să fii ultra-conservativ, aplici fee + slippage și pe exit. Convenție pentru B&H: aplicăm pe ENTRY ONLY (buy and hold = nu vinzi niciodată). Final equity = `units * Close_d[2024-12-31]` fără fee de exit.

**Decizie finală pentru consistență:** apply fee + slippage pe entry only. Final equity = `units × Close_d[2024-12-31]` (no exit costs, since "hold forever" assumption).

---

## 19. Test Matrix (15 backtests)

| # | Strategy | Mode | Direction | Leverage | Purpose |
|---|----------|------|-----------|----------|---------|
| 1 | A Complex | Spot 1x | Long-only | 1x | Main eval |
| 2 | A Complex | Futures 1x | Long+Short | 1x | Main eval |
| 3 | A Complex | Futures 2x | Long+Short | 2x | Main eval |
| 4 | B Simple | Spot 1x | Long-only | 1x | Main eval |
| 5 | B Simple | Futures 1x | Long-only | 1x | Main eval |
| 6 | B Simple | Futures 2x | Long-only | 2x | Main eval |
| 7 | C Hybrid | Spot 1x | Long-only | 1x | Main eval |
| 8 | C Hybrid | Futures 1x | Long-only | 1x | Main eval |
| 9 | C Hybrid | Futures 2x | Long-only | 2x | Main eval |
| 10 | D-Primary (55/20) | Spot 1x | Long-only | 1x | Main eval |
| 11 | D-Primary (55/20) | Futures 1x | Long+Short | 1x | Main eval |
| 12 | D-Primary (55/20) | Futures 2x | Long+Short | 2x | Main eval |
| 13 | D-Alt-Short (20/10) | Spot 1x | Long-only | 1x | Robustness |
| 14 | D-Alt-Medium (40/15) | Spot 1x | Long-only | 1x | Robustness |
| 15 | Buy & Hold | Spot 1x | Long-only | 1x | Benchmark |

**Total: 15 backtests.**

### 19.1 Sub-period reporting

For backtests #1-12 and #15 (everything except robustness alts), report metrics for THREE periods:
- **Full:** 2020-01-01 → 2024-12-31
- **In-sample:** 2020-01-01 → 2023-12-31
- **Out-of-sample:** 2024-01-01 → 2024-12-31

For backtests #13-14 (D-Alt robustness): full period only.

---

## 20. Metrics & Definitions

Toate metricile calculate pe equity curve daily (close-to-close).

### 20.1 Returns

**Total Return %**
```
total_return = (final_equity / initial_equity) - 1
```

**CAGR %** (Compound Annual Growth Rate)
```
years = (end_date - start_date).days / 365.25
cagr = (final_equity / initial_equity) ** (1 / years) - 1
```

**Annual Returns** — listed per calendar year (2020, 2021, ..., 2024).
```python
annual_return[Y] = equity_at_year_end[Y] / equity_at_year_start[Y] - 1
```

### 20.2 Risk

**Max Drawdown %**
```python
running_peak = equity.expanding().max()
drawdown = (equity - running_peak) / running_peak
max_dd = drawdown.min()  # most negative value, e.g., -0.30 = -30%
```

**Avg Drawdown %** — average of drawdown values during drawdown periods (excluding "no drawdown" bars).

**Drawdown Duration (days)** — for max drawdown, the number of days from peak to recovery (or end of backtest if no recovery).

### 20.3 Risk-adjusted

**Sharpe Ratio** (annualized)
```python
daily_returns = equity.pct_change().dropna()
mean_daily = daily_returns.mean() - (RISK_FREE_RATE / 365)
std_daily = daily_returns.std(ddof=1)
sharpe = (mean_daily / std_daily) * sqrt(365)
```

**Sortino Ratio** (annualized, MAR=0)
```python
downside_returns = daily_returns[daily_returns < 0]
downside_std = downside_returns.std(ddof=1)
mean_daily = daily_returns.mean() - 0  # MAR = 0
sortino = (mean_daily / downside_std) * sqrt(365)
```

**Calmar Ratio**
```python
calmar = cagr / abs(max_dd)
```

### 20.4 Trade stats

**Total Trades** — number of closed roundtrips.

**Win Rate %**
```python
win_rate = wins / total_trades
```

Where `wins` = trades with PnL > 0 (after costs).

**Avg Win %** / **Avg Loss %** — mean PnL of winning / losing trades, as percentage of equity at entry.

**Profit Factor**
```python
profit_factor = sum_of_wins_usd / abs(sum_of_losses_usd)
```

**Best Trade %** / **Worst Trade %** — single best/worst PnL as % of equity at entry.

**Avg Hold Duration (days)** — mean number of days between entry and exit.

### 20.5 Exposure

**Time in Market %**
```python
in_market_bars = sum(1 for bar if position is not None at bar close)
time_in_market = in_market_bars / total_bars
```

**Avg Position Size %** — mean of `notional / equity_at_entry` across all trades.

### 20.6 Costs

**Total Fees Paid (USD)** — cumulative fee_cost across all trades.

**Total Slippage (USD)** — cumulative slippage cost (computed as `notional × SLIPPAGE` per execution).

**Total Funding (USD)** — for futures only. Net funding paid (positive = paid, negative = received).

**Net vs Gross Return** — return without costs vs with costs. Computed as:
```
gross_return = sum of trade PnLs without fees/slippage/funding
net_return = actual realized return
```

### 20.7 Metrics for Buy & Hold

Same metrics applied. "Total Trades" = 1. "Time in Market %" = 100%. No drawdown breaches, no stops.

---

## 21. In-Sample / Out-of-Sample Protocol

### 21.1 Split

- **In-sample (IS):** 2020-01-01 → 2023-12-31 (4 ani, 4 × 365 = 1461 days)
- **Out-of-sample (OOS):** 2024-01-01 → 2024-12-31 (1 an, 366 days)

### 21.2 Rules

1. **No parameter tuning.** Toți parametrii sunt fixați a priori în SPEC (asta). Nu se ajustează NIMIC pe baza rezultatelor IS sau OOS.

2. **OOS evaluat o singură dată.** Generăm raportul final, vedem rezultatele. Nu re-rulăm, nu ajustăm, nu "fixăm" nimic post-OOS.

3. **Verdictul final se bazează pe OOS.** Decizia "strategia X merită live" depinde de performanța OOS, nu IS.

4. **Robustness gap raportat:**
```
robustness_gap = (CAGR_in_sample - CAGR_out_of_sample) / CAGR_in_sample
```
Gap mare (>50%) sugerează overfitting implicit (strategia "merge" pe perioada de design dar nu pe nouă).

### 21.3 Engine implementation

Engine-ul rulează un singur backtest pe perioada completă (2020-2024). Metricile sunt calculate apoi pe slice-uri:
```python
results = run_backtest(start=2020-01-01, end=2024-12-31)

equity_full = results.equity_curve
equity_is = equity_full[:'2023-12-31']
equity_oos = equity_full['2024-01-01':]

trades_full = results.trades
trades_is = [t for t in trades_full if t.exit_date <= 2023-12-31]
trades_oos = [t for t in trades_full if t.entry_date >= 2024-01-01]
# Trades spanning the boundary: classify by exit_date

metrics_full = compute_metrics(equity_full, trades_full)
metrics_is = compute_metrics(equity_is, trades_is)
metrics_oos = compute_metrics(equity_oos, trades_oos)
```

**Trades that span IS-OOS boundary:** classified by exit_date. Entry was IS, exit was OOS → counted in OOS trade stats. Equity curve handled continuously (no reset at boundary).

---

## 22. Output Deliverables

### 22.1 `reports/master.csv`

One row per backtest. Columns:

```
strategy, mode, direction, leverage, period,
total_return_pct, cagr_pct, max_dd_pct, avg_dd_pct, dd_duration_days,
sharpe, sortino, calmar,
total_trades, win_rate_pct, avg_win_pct, avg_loss_pct, profit_factor,
best_trade_pct, worst_trade_pct, avg_hold_days,
time_in_market_pct, avg_position_size_pct,
total_fees_usd, total_slippage_usd, total_funding_usd, net_vs_gross_pct,
annual_2020_pct, annual_2021_pct, annual_2022_pct, annual_2023_pct, annual_2024_pct
```

`period` ∈ {`full`, `in_sample`, `out_of_sample`}.

For each "main eval" backtest: 3 rows (full, IS, OOS). 12 backtests × 3 + 2 robustness × 1 + 1 B&H × 3 = 41 rows total.

### 22.2 `reports/equity_curves.png`

Single matplotlib chart, log-scale Y axis. All 13 main backtests + B&H overlaid (D-Alt excluded from overlay to avoid clutter). Legend with strategy + mode. Vertical line at IS-OOS boundary (2024-01-01).

Image: 1600×1000 px, embedded base64 in master HTML report too.

### 22.3 Per-backtest HTML reports

For each of the 15 backtests: `reports/{strategy}_{mode}.html`

Generated from `analysis/templates/report.html.j2`. Contains:

1. **Header:** strategy name, mode, direction, leverage, period covered, generation timestamp
2. **Summary table:** all 25+ metrics (full, IS, OOS columns)
3. **Equity curve (PNG embedded):** log-scale, x-axis = date, y-axis = equity USD
4. **Drawdown curve (PNG embedded):** linear scale, percentage
5. **Annual returns heatmap (PNG embedded):** rows = years, cols = months, cell color = return%
6. **Distribution of returns histogram (PNG embedded):** daily returns histogram with mean/stdev annotated
7. **Trade list (HTML table):** entry_date, entry_price, exit_date, exit_price, side, units, notional, pnl_usd, pnl_pct, hold_days, exit_reason
8. **Trade list — winners and losers separately (sortable, top 10 each)**

### 22.4 `reports/summary.html`

Master report aggregating all backtests:

1. Test matrix table with key metrics (CAGR, Sharpe, MaxDD, OOS CAGR)
2. Equity curves overlay chart
3. IS vs OOS comparison chart (CAGR delta per strategy)
4. D-Primary vs D-Alt robustness comparison
5. Final verdict section (per §26)

### 22.5 Console output

During run, log to stdout:
- Data load progress (bars fetched, validated)
- Each backtest start/end with summary metrics
- Final master CSV path

Log file `reports/run.log` with full DEBUG-level output for troubleshooting.

---

## 23. Edge Cases & Conventions

### 23.1 Position concurrency

**STRICT one-at-a-time** per strategy. If a position is open and a new entry signal fires:
- For LONG→LONG same direction: ignore signal (already in position)
- For LONG→SHORT or SHORT→LONG opposite direction (futures only): ignore signal unless transition rule allows (min 1 daily bar gap after previous close)

### 23.2 Cash insufficient

If computed position size (with all fees factored) would result in `cash < 0` post-execution:
- **Skip trade entirely.** Don't reduce size, don't partial fill.
- Log warning.

For spot, this means: `(units * entry_price * (1 + slippage) + units * entry_price * fee) > cash` → skip.
For futures, margin used is much smaller, so this rarely triggers.

### 23.3 Equity ≤ 0

If equity drops to ≤ 0 at any point:
- Strategy halted permanently for that backtest run.
- Equity stays at 0 (no negative).
- Subsequent bars: equity remains 0, no trades, no further activity.

### 23.4 Open position at backtest end

At 2024-12-31 (last available bar), if a position is still open:
- **Force-close at Close_d[2024-12-31]** with slippage and fee.
- Counted as a trade roundtrip in stats.
- `exit_reason = "EOB"` (end of backtest).

### 23.5 NaN handling in indicators

During warmup (before indicators are fully computed):
- All entries skipped (signals require non-NaN indicators)
- Indicator NaN check is implicit via signal evaluation

For Strategy D specifically: skip until `sigma_30d` is non-NaN (≥30 days of returns history).

### 23.6 Duplicate signals on same bar

If both entry and exit signals trigger on same bar (rare, e.g., D entry signal AND immediate exit signal both met):
- **Exit takes precedence.** Don't enter a position that would immediately exit.
- Net effect: no trade.

### 23.7 Time zone

All timestamps stored and processed in **UTC**. Display in UTC throughout reports.

### 23.8 USDT vs USD

We treat USDT = USD 1:1. No depeg modeling. Mention assumption in README.

### 23.9 Determinism

Backtest must produce identical results on every run with the same code and data. No random sampling, no time-dependent operations. Set explicit pandas options if needed.

### 23.10 Floating point precision

Use `float64` throughout. Don't round intermediate calculations. Only round at display time in reports (e.g., 2 decimals for percentages).

### 23.11 Trade ID

Each trade gets a unique sequential ID starting at 1 per backtest. Used for cross-referencing in logs and reports.

### 23.12 Engine ticker iteration

For Strategy A (mixed timeframes), engine iterates over 4h bars. At each 4h bar:
- Look up most recent completed daily bar for regime check
- Apply funding at 00:00 UTC bars only
- Update daily/weekly trackers at appropriate boundaries

For all other strategies (daily only), engine iterates over daily bars.

### 23.13 Bar timestamp convention

Bar timestamp = bar OPEN time (UTC). E.g., daily bar for 2020-01-01 has timestamp `2020-01-01 00:00:00 UTC`. Bar closes at the timestamp of the NEXT bar (2020-01-02 00:00:00 UTC for daily).

For 4h bars: timestamps at 00, 04, 08, 12, 16, 20 UTC.

---

## 24. Validation & Testing

> **STATUS: DEFERRED for v1.** Această secțiune rămâne documentată ca referință pentru o versiune ulterioară. Implementarea efectivă a testelor unitare e amânată per decizia din 2026-05-11. **Singura validare activă pentru v1** este §24.2 (Integration validation — sanity checks manuale) și un end-to-end run reușit din `main.py`.

### 24.1 Unit tests (pytest) — DEFERRED

**`test_data_loader.py`:**
- Fetch single day returns 1 bar (daily) or 6 bars (4h)
- Validate detects gaps, anomalies, missing data
- SQLite insert/read roundtrip preserves values

**`test_indicators.py`:**
- EMA, ATR, RSI, ADX, SMA match known reference values (compute by hand on small sample)
- Donchian channels: 5-bar sample, verify high/low rolling max/min with shift(1)
- Vol estimation: 30 returns of known sigma → expect sigma × sqrt(365) annual

**`test_position.py`:**
- Long position PnL = `(exit - entry) * units` (spot)
- Long position PnL futures = same, with margin not deducted from cash
- Short position PnL = `(entry - exit) * units` (futures only)

**`test_costs.py`:**
- Spot fee deducted on entry and exit
- Slippage applied directionally (entry worse, exit worse)
- Funding charged at 00:00 UTC only when position open
- Liquidation triggered correctly on gap

**`test_metrics.py`:**
- Buy and hold $100 → $200 over 2 years = CAGR 41.42%
- Constant 1%/day returns × 252 days → Sharpe ≈ infinite (no downside)
- All-loss curve → Sortino = 0 / undefined handled

**`test_strategy_X.py`** (X = A, B, C, D):
- Construct synthetic OHLCV that triggers exact entry condition → verify entry detected at correct bar
- Construct sequence that triggers exit → verify exit at correct bar
- Position sizing matches expected for known equity/ATR

**`test_engine.py`:**
- Simple buy-and-hold strategy on flat data → returns 0
- Buy-and-hold on linear ramp → returns expected
- Strategy that always loses → eventually halts (Strategy A drawdown breaker, equity ≤ 0)

### 24.2 Integration validation

**Sanity check 1 — Buy & Hold replication:**
Run Buy & Hold backtest. Total return should equal (Close_d[2024-12-31] / Open_d[2020-01-01]) * (1 - SPOT_FEE - SLIPPAGE) - 1. Tolerance: 0.001% (floating point).

**Sanity check 2 — Manual trade replay:**
For Strategy B, pick a specific entry signal date by hand from data (find a day where EMA50 just crossed EMA200 with volume confirmation). Run backtest. Verify the engine entered on that date.

**Sanity check 3 — Indicator continuity:**
EMA200 at 2020-01-01 should equal `ta.ema(close[2019-06-01:2020-01-01], length=200).iloc[-1]`. Run separately and compare.

**Sanity check 4 — Slippage symmetric:**
Run identical Strategy B with SLIPPAGE=0. Compare to SLIPPAGE=0.05%. Difference per trade should equal `2 * SLIPPAGE * avg_notional`.

### 24.3 Test coverage target

- Indicators: 100%
- Position lifecycle: 100%
- Cost model: 100%
- Strategy signal logic: 80%+ (cover main entry/exit paths, common edge cases)
- Engine loop: 70%+ (test orchestration, leave edge cases to integration)

---

## 25. Implementation Phases

### Phase 1 — Foundation (Day 1)
- [ ] Project structure created
- [ ] `config.py` with all parameters
- [ ] `requirements.txt`
- [ ] `.gitignore`
- [ ] `README.md` skeleton (basic instructions)
- [ ] `data_loader.py` — ccxt fetch + SQLite cache + validation
- [ ] **Acceptance:** `python data_loader.py` downloads full dataset, validates, caches

### Phase 2 — Indicators & Position primitives (Day 2)
- [ ] Helper module for indicators (wrapper around pandas-ta)
- [ ] `backtest/position.py` — Position dataclass + lifecycle methods
- [ ] `backtest/costs.py` — fee, slippage, funding, liquidation logic
- [ ] **Acceptance:** manual verification on synthetic data — apply fee + slippage on known trade, verify cash flow matches expected formula

### Phase 3 — Engine core (Day 3)
- [ ] `backtest/engine.py` — main backtest loop, supports daily and 4h iteration
- [ ] `strategies/base.py` — abstract base class for strategies
- [ ] `benchmarks/buy_and_hold.py` — implementation
- [ ] Engine integration with B&H
- [ ] **Acceptance:** Buy & Hold backtest matches expected return within 0.001%

### Phase 4 — Strategies (Days 4-5)
- [ ] `strategies/strategy_b.py` (simplest, do first as sanity check)
- [ ] `strategies/strategy_c.py`
- [ ] `strategies/strategy_d.py` (with parametric Donchian periods for D-Primary and D-Alt)
- [ ] `strategies/strategy_a.py` (most complex, do last)
- [ ] **Acceptance:** each strategy runs end-to-end without errors on full data, generates trade list non-trivial

### Phase 5 — Runner & Metrics (Day 6)
- [ ] `backtest/metrics.py` — all metrics from §20
- [ ] `backtest/runner.py` — runs all 15 backtests, generates results dict
- [ ] In-sample / out-of-sample splitting
- [ ] **Acceptance:** running `python main.py` produces JSON output for all 15 backtests

### Phase 6 — Reporting (Day 7)
- [ ] `analysis/compare.py` — master CSV generation
- [ ] `analysis/templates/report.html.j2` — Jinja2 template
- [ ] `analysis/reports.py` — HTML generation with matplotlib charts
- [ ] Master summary HTML
- [ ] Equity curves overlay
- [ ] **Acceptance:** `reports/` directory contains master.csv + 15 HTML reports + overlay PNG + summary.html

### Phase 7 — Polish (Day 8)
- [ ] README.md complete with run instructions
- [ ] Logging configuration
- [ ] Final integration test: run from scratch (delete cache), verify reproducibility
- [ ] Code review pass: remove dead code, comments, formatting
- [ ] **Acceptance:** clean run from `git clone` → `pip install -r requirements.txt` → `python main.py` → reports generated

**Total estimated: ~6-7 days of work** (tests deferred, see §24 — adăugarea ulterioară a testelor ~1.5-2 zile suplimentare).

---

## 26. Final Verdict Reporting

### 26.1 Verdict format

În `reports/summary.html`, secțiune "Final Verdict":

```
1. RANKING out-of-sample (sorted by Sharpe descending):
   Top 5 strategies + B&H benchmark.

2. ROBUSTNESS check:
   - For top 3 strategies: IS vs OOS CAGR delta
   - If delta > 50% → flag potential overfitting
   - D-Primary vs D-Alt comparison (does period choice matter?)

3. BEAT B&H check:
   - Which strategies beat B&H on OOS CAGR?
   - Which on Sharpe?
   - Which on Calmar (risk-adjusted)?

4. KEY OBSERVATIONS:
   - Did futures + leverage add value over spot?
   - Did adding shorts (A, D futures) help or hurt?
   - Did circuit breakers (A only) help or hurt?
   - Did vol-targeting (D) beat ATR-based sizing (A/B/C)?

5. RECOMMENDATION:
   - Single strategy recommended for live implementation, with justification
   - Caveats and risks
   - Suggested monitoring metrics for live deployment
```

### 26.2 Decision criteria for recommendation

Strategy recommended for live = single strategy that:
1. Has positive OOS CAGR (else: just hodl)
2. Has OOS Sharpe ≥ 1.0 (risk-adjusted reasonable)
3. Has OOS Max DD < 30% (survivable)
4. Has IS-OOS CAGR robustness gap < 50% (not obviously overfit)
5. Beat at least 2 of: B&H CAGR, B&H Sharpe, B&H Calmar (on OOS)

If multiple strategies pass: pick highest OOS Sharpe.
If none pass: recommend NOT to deploy — verdict is "stay with B&H or don't trade".

### 26.3 What we explicitly DO NOT recommend

- Multi-strategy portfolio (out of scope for this exercise)
- Live deployment of D-Alt variants (these are robustness tests, not candidates)
- Any strategy with OOS CAGR < B&H CAGR (better to just buy and hold)

---

## APPENDIX A — Glossary

- **OHLCV:** Open, High, Low, Close, Volume — standard bar data
- **EMA:** Exponential Moving Average
- **ATR:** Average True Range — measure of volatility
- **RSI:** Relative Strength Index — momentum oscillator
- **ADX:** Average Directional Index — trend strength
- **SMA:** Simple Moving Average
- **PnL:** Profit and Loss
- **MaxDD:** Maximum Drawdown
- **CAGR:** Compound Annual Growth Rate
- **IS/OOS:** In-Sample / Out-of-Sample
- **B&H:** Buy and Hold
- **DCA:** Dollar-Cost Averaging (NOT used as benchmark in this project)
- **TF:** Trend Following
- **Long+Short:** strategy can take both directions
- **Long-only:** strategy can only take long positions
- **Liquidation:** forced position close due to insufficient margin (futures only)
- **Funding rate:** periodic payment between longs and shorts in perpetual futures

---

## APPENDIX B — Reference parameters summary table

| Parameter | Strategy A | Strategy B | Strategy C | Strategy D |
|-----------|------------|------------|------------|------------|
| Timeframe | Daily + 4h | Daily | Daily | Daily |
| EMA (long) | 200d | 200d | 200d | — |
| EMA (medium) | 50d | 50d | 50d | — |
| EMA (entry) | 21 (4h) | — | — | — |
| ADX threshold | 25 (daily) | — | 25 (daily) | — |
| RSI range LONG | 40-55 (4h) | — | — | — |
| RSI range SHORT | 45-60 (4h) | — | — | — |
| Volume mult | >SMA20 | >1.2×SMA20 | >1.2×SMA20 | — |
| Donchian entry | — | — | — | 55-day high |
| Donchian exit | — | — | — | 20-day low |
| Sizing method | ATR (1% risk) | ATR (1% risk) | ATR (1% risk) | Vol-target 30% |
| Initial SL | 2×ATR (4h) | 2×ATR (daily) | 2×ATR (daily) | None (channel) |
| Break-even | +1.5×ATR | None | +1.5×ATR | N/A |
| Trail multiplier | 3×ATR | 2×ATR | 2×ATR | N/A |
| Trail reference | High (4h) | Highest close | High (daily) | N/A |
| Hard exit | Close vs EMA50_d | Close < EMA50 | Close < EMA50 | N/A |
| Daily circuit breaker | -3% | None | None | None |
| Weekly circuit breaker | -5% | None | None | None |
| DD circuit breaker | -8% perm | None | -8% perm | None |
| Loss reducer | 3 losses → 50% × 5 | None | 3 losses → 50% × 5 | None |
| Direction (spot) | Long-only | Long-only | Long-only | Long-only |
| Direction (futures) | Long+Short | Long-only | Long-only | Long+Short |

---

## APPENDIX C — Files manifest (must be created)

Code files (estimated lines):
- `config.py` (~100)
- `data_loader.py` (~250)
- `backtest/engine.py` (~600)
- `backtest/position.py` (~150)
- `backtest/costs.py` (~150)
- `backtest/metrics.py` (~300)
- `backtest/runner.py` (~200)
- `strategies/base.py` (~80)
- `strategies/strategy_a.py` (~500)
- `strategies/strategy_b.py` (~150)
- `strategies/strategy_c.py` (~200)
- `strategies/strategy_d.py` (~250)
- `benchmarks/buy_and_hold.py` (~80)
- `analysis/compare.py` (~150)
- `analysis/reports.py` (~400)
- `analysis/templates/report.html.j2` (~250)
- `main.py` (~100)

Test files (**DEFERRED for v1** — estimate când vor fi adăugate):
- `tests/test_data_loader.py` (~150)
- `tests/test_indicators.py` (~200)
- `tests/test_position.py` (~150)
- `tests/test_costs.py` (~200)
- `tests/test_metrics.py` (~200)
- `tests/test_strategy_a.py` (~200)
- `tests/test_strategy_b.py` (~150)
- `tests/test_strategy_c.py` (~150)
- `tests/test_strategy_d.py` (~200)
- `tests/test_engine.py` (~300)

Documentation:
- `README.md` (~150)

**Total estimated v1: ~3400 lines de cod** (teste deferred = ~1900 linii suplimentare când vor fi adăugate, total ~5300).

---

**END OF SPEC**

This document is the contract for implementation. Any deviation requires explicit user approval and a versioned update to this file.
