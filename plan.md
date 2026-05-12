Excelent — acum am tot ce-mi trebuie. Răspunsurile lui sunt **foarte bune** și consistente cu principiile de simplitate. Iată planul complet pentru Claude Code.

---

# 📋 PLAN COMPLET DE IMPLEMENTARE — BTC Trading Bot cu Backtesting Comparativ

## 🎯 Obiectiv

Construire framework de backtesting pentru **3 strategii** de trading BTC, evaluate pe **5 ani de date** (2020-2025), pe **spot și futures**, cu **$10,000 capital inițial**. Scopul: determinarea empirică a strategiei optime pentru implementare live.

---

## 📦 Stack Tehnic

```
Limbaj:         Python 3.11+
Date:           ccxt (Binance OHLCV daily + 4h)
Indicatori:     pandas-ta sau ta-lib
Backtesting:    backtesting.py (Pythonic, ușor de extins)
Analiză:        pandas, numpy
Vizualizare:    matplotlib, plotly
Output:         CSV + HTML report cu grafice
Storage:        SQLite (cache date OHLCV)
```

**Recomandare:** folosește `backtesting.py` în loc de `vectorbt` pentru claritatea codului. Pentru această fază exploratorie, debug-ul ușor e mai valoros decât viteza.

---

## 🗂️ Structura proiectului

```
btc_backtest/
├── data/
│   ├── btc_daily_2020_2025.csv
│   ├── btc_4h_2020_2025.csv
│   └── cache.sqlite
├── strategies/
│   ├── __init__.py
│   ├── strategy_a_complex.py      # Strategia mea inițială (6 layers)
│   ├── strategy_b_simple.py       # Strategia celuilalt agent
│   └── strategy_c_hybrid.py       # Strategia finală hibridă
├── backtest/
│   ├── engine.py                  # Wrapper peste backtesting.py
│   ├── metrics.py                 # Sharpe, Sortino, Calmar, etc.
│   └── runner.py                  # Rulează toate strategiile
├── analysis/
│   ├── compare.py                 # Comparație side-by-side
│   └── reports.py                 # Generare HTML report
├── data_loader.py                 # Descărcare + cache OHLCV
├── config.py                      # Toți parametrii într-un singur loc
├── main.py                        # Entry point
├── requirements.txt
└── README.md
```

---

## ⚙️ Configurație generală (config.py)

```python
# Perioada
START_DATE = "2020-01-01"
END_DATE = "2025-12-31"

# Capital
INITIAL_CAPITAL = 10_000  # USD

# Asset
SYMBOL = "BTC/USDT"
EXCHANGE = "binance"

# Costuri reale
SPOT_FEE = 0.001          # 0.1% Binance spot (taker)
FUTURES_FEE = 0.0005      # 0.05% Binance futures (taker)
SLIPPAGE = 0.0005         # 0.05% slippage realist pe BTC liquid

# Funding rate futures (medie istorică)
FUTURES_FUNDING_DAILY = 0.0001  # ~0.01%/zi (positive funding average)

# Moduri de testare
MODES = ["spot", "futures_1x", "futures_2x"]
```

---

## 📐 STRATEGIA A — "Complex Multi-Layer" (versiunea mea inițială)

### Mecanism

```
TIMEFRAMES: Daily (regim) + 4h (entry)
INDICATORI: EMA50, EMA200, EMA21, ADX(14), RSI(14), ATR(14), Volume
DIRECTION: Long + Short
```

### Reguli

**LAYER 1 — Regim (Daily):**
- ADX(14) ≥ 25 → activă
- ADX(14) < 25 → inactivă

**LAYER 2 — Direcție (Daily):**
- EMA50 > EMA200 AND Price > EMA50 → bias LONG
- EMA50 < EMA200 AND Price < EMA50 → bias SHORT
- Otherwise → no bias

**LAYER 3 — Entry (4h):**
- LONG: pullback la EMA21, RSI între 40-55, candela 4h bullish close, volume > SMA20(volume)
- SHORT: pullback la EMA21, RSI între 45-60, candela 4h bearish close, volume > SMA20(volume)

**LAYER 4 — Position Sizing:**
- Risk per trade: 1% capital
- Stop distance: 2 × ATR
- Position = (1% × equity) / (stop_distance%)
- Cap max: 50% capital, leverage max 2x

**LAYER 5 — Trade Management:**
- SL inițial: 2 × ATR
- Break-even la +1.5 × ATR profit
- Trailing stop: 3 × ATR de la peak (după activare)
- Hard exit: close daily peste/sub EMA50 împotriva poziției

**LAYER 6 — Circuit Breakers:**
- Daily loss > 3% → bot off 24h
- Weekly loss > 5% → bot off până luni
- Drawdown > 8% → bot off complet, flag pentru analiză
- 3 losses consecutive → reduce size 50% pentru 5 trades

---

## 📐 STRATEGIA B — "Simple Pure Trend" (celălalt agent)

### Mecanism

```
TIMEFRAME: Daily ONLY
INDICATORI: EMA50, EMA200, ATR(14), Volume
DIRECTION: Long ONLY (spot: long sau cash)
```

### Reguli (exact cum a specificat el)

**ENTRY LONG:**
```
TOATE condițiile simultan:
  1. EMA50 > EMA200
  2. Close > EMA50
  3. Volume_today > 1.2 × SMA20(Volume)

→ BUY la deschiderea barului următor
```

**INITIAL STOP:**
```
Stop = Entry - 2 × ATR(14)
```

**TRAILING STOP:**
```
Activare: IMEDIAT după entry (nu așteaptă profit)
Formula: Trailing_Stop = Highest_Close_Since_Entry - 2 × ATR(14)
Stop nu coboară niciodată (ratchet only up)
```

**EXIT:**
```
Oricare:
  - Close < EMA50  → SELL
  - Trailing stop hit → SELL automat
```

**POSITION SIZING:**
```
Risk per trade: 1% equity
Position_Size = (1% × Equity) / Stop_Distance%
Cap max: 50% din capital într-un trade
```

**RE-ENTRY:**
```
Toate cele 3 condiții de entry trebuie să REAPARĂ:
  - EMA50 > EMA200
  - Close > EMA50
  - Volume > 1.2 × SMA20
```

**NICIUN circuit breaker, NICIUN short.**

---

## 📐 STRATEGIA C — "Hybrid Final" (recomandarea consensuală)

### Mecanism

```
TIMEFRAME: Daily ONLY
INDICATORI: EMA50, EMA200, ADX(14), ATR(14), Volume
DIRECTION: Long ONLY (spot/futures long-only fază 1)
```

### Reguli (cel mai bun din ambele)

**ENTRY LONG (din strategia B + ADX filter din A):**
```
TOATE condițiile:
  1. EMA50 > EMA200
  2. Close > EMA50
  3. ADX(14) > 25                           ← din strategia A
  4. Volume_today > 1.2 × SMA20(Volume)

→ BUY la deschiderea barului următor
```

**INITIAL STOP (din B):**
```
Stop = Entry - 2 × ATR(14)
```

**TRADE MANAGEMENT (combinație):**
```
- SL inițial: 2 × ATR
- Break-even: SL → entry când profit ≥ 1.5 × ATR    ← din A
- Trailing stop: Peak - 2 × ATR, activat după break-even   ← hibrid
```

**EXIT:**
```
Oricare:
  - Close < EMA50  → SELL
  - Trailing/SL hit → SELL
```

**POSITION SIZING (identic cu B):**
```
Risk: 1% per trade
Cap max: 50% capital
```

**CIRCUIT BREAKERS (din A, simplificate):**
```
- Drawdown total > 8% → bot oprit, flag review
- 3 losses consecutive → size redus 50% pentru 5 trades
```

---

## 🧪 Framework de Backtesting

### Cerințe critice pentru backtest valid

```python
1. WALK-FORWARD VALIDATION
   - NU rula optimizare pe toate datele
   - Train: 2020-2022, Test: 2023
   - Train: 2020-2023, Test: 2024
   - Train: 2020-2024, Test: 2025
   - Raportează DOAR rezultate out-of-sample

2. COSTURI REALISTE
   - Fees aplicate pe fiecare trade (entry + exit)
   - Slippage 0.05% pe execuție
   - Pentru futures: funding rate cumulat pe poziții overnight

3. NO LOOK-AHEAD BIAS
   - Indicatorii calculați doar din date PÂNĂ la momentul deciziei
   - Entry la OPEN-ul barului următor (nu close-ul barului semnal)

4. POSITION SIZING REAL
   - Tracking exact al equity
   - Recalcul size la fiecare trade nou
   - Respectă cap-urile (50% max, 2x leverage max)
```

### Variante de rulat (matrice completă)

Pentru fiecare strategie, rulează:

| # | Strategie | Mode | Direction | Leverage |
|---|-----------|------|-----------|----------|
| 1 | A Complex | Spot | Long-only | 1x |
| 2 | A Complex | Futures | Long+Short | 1x |
| 3 | A Complex | Futures | Long+Short | 2x |
| 4 | B Simple | Spot | Long-only | 1x |
| 5 | B Simple | Futures | Long-only | 1x |
| 6 | B Simple | Futures | Long-only | 2x |
| 7 | C Hybrid | Spot | Long-only | 1x |
| 8 | C Hybrid | Futures | Long-only | 1x |
| 9 | C Hybrid | Futures | Long-only | 2x |
| 10 | **Benchmark** | Buy & Hold | Long-only | 1x |

**Total: 10 backtest-uri.**

---

## 📊 Metrici de evaluat (pentru fiecare)

```python
METRICS = [
    # Returns
    "Total Return %",
    "CAGR %",
    "Annual Returns (per year)",
    
    # Risk
    "Max Drawdown %",
    "Avg Drawdown %",
    "Drawdown Duration (days)",
    
    # Risk-adjusted
    "Sharpe Ratio",
    "Sortino Ratio",
    "Calmar Ratio",
    
    # Trade stats
    "Total Trades",
    "Win Rate %",
    "Avg Win %",
    "Avg Loss %",
    "Profit Factor",
    "Best Trade %",
    "Worst Trade %",
    "Avg Hold Duration (days)",
    
    # Exposure
    "Time in Market %",
    "Avg Position Size %",
    
    # Costs
    "Total Fees Paid",
    "Total Slippage",
    "Net vs Gross Return",
]
```

---

## 📈 Output dorit

### 1. Tabel comparativ master

CSV cu toate cele 10 variante × toate metricile. Format wide pentru sortare ușoară.

### 2. Equity curves comparative

Un grafic suprapus cu toate cele 10 equity curves vs timp (2020-2025).

### 3. Per-strategy deep dive

Pentru fiecare strategie, raport HTML cu:
- Equity curve
- Drawdown curve
- Distribution of returns (histogram)
- Lista de trades cu entry/exit/PnL
- Stats per an (heatmap: rows = ani, cols = luni, valori = return %)

### 4. Robustness check

Pentru top 3 strategii, **parameter sensitivity analysis**:
- Variază EMA50 între 40-60 → cum se schimbă rezultatele?
- Variază ATR multiplier între 1.5-3.0 → impact?
- Dacă o variație mică **distruge complet rezultatele** → strategia e overfit

---

## 🚀 Faze de implementare pentru Claude Code

### Faza 1 — Data Pipeline (Day 1)
- [ ] Setup project structure
- [ ] `data_loader.py`: descarcă BTC OHLCV daily + 4h de la Binance via ccxt
- [ ] Cache în SQLite, evită re-descărcarea
- [ ] Validare date: no gaps, no anomalies
- [ ] Salvare CSV-uri de backup

### Faza 2 — Backtest Engine (Day 2)
- [ ] Setup `backtesting.py` cu costuri reale (fees + slippage)
- [ ] `engine.py`: wrapper care suportă cele 10 moduri
- [ ] `metrics.py`: calcul metrici custom (Calmar, Sortino, etc.)
- [ ] Validare cu o strategie trivială (buy-and-hold) → trebuie să returneze ~exact BTC performance

### Faza 3 — Implementare Strategii (Day 3-4)
- [ ] `strategy_a_complex.py`: implementare completă cu 6 layere
- [ ] `strategy_b_simple.py`: implementare conform specificațiilor agentului
- [ ] `strategy_c_hybrid.py`: implementare hibridă
- [ ] Unit tests pentru fiecare regulă (verifică că semnalele apar corect)

### Faza 4 — Rulare Backtest-uri (Day 5)
- [ ] `runner.py`: rulează automat toate cele 10 variante
- [ ] Output CSV master + JSON detaliat per strategie
- [ ] Logging detaliat pentru fiecare trade

### Faza 5 — Analiză și Vizualizare (Day 6)
- [ ] `compare.py`: tabel comparativ master
- [ ] `reports.py`: HTML reports per strategie
- [ ] Equity curves overlay
- [ ] Robustness check pe top 3

### Faza 6 — Documentație (Day 7)
- [ ] README cu instrucțiuni de rulare
- [ ] Interpretare rezultate
- [ ] Recomandare finală bazată pe date

---

## ⚠️ Capcane critice de evitat (pentru Claude Code)

```
1. LOOK-AHEAD BIAS
   ❌ df['rsi'] = calculate_rsi(df) → split → backtest
   ✅ Calculează indicatori INCREMENTAL pe fiecare bar
   
2. SURVIVORSHIP BIAS
   N/A pentru BTC (există de tot timpul testat)
   
3. UNREALISTIC FILLS
   ❌ Entry la close-ul barului semnal
   ✅ Entry la open-ul barului următor
   
4. IGNORING COSTS
   ❌ Backtest fără fees/slippage
   ✅ Toate trade-urile au costuri reale aplicate
   
5. POSITION SIZING ERRORS
   ❌ Size fix indiferent de stop distance
   ✅ Size = risk / stop_distance, RECALCULAT la fiecare trade
   
6. OVERFITTING
   ❌ Optimizare parametri pe toate datele
   ✅ Walk-forward, raportezi DOAR out-of-sample
```

---

## 📝 Mesaj exact pentru Claude Code

Iată ce să-i dai (copy-paste):

---

**"Salut! Te rog implementează un framework de backtesting pentru 3 strategii de trading Bitcoin. Specificațiile complete sunt în documentul de mai jos. Te rog respectă cu strictețe:**

**1. Toate cele 3 strategii cu parametrii EXACȚI specificați (nu schimba nimic).**
**2. Costuri realiste (fees + slippage + funding pentru futures).**
**3. No look-ahead bias — entry mereu la deschiderea barului următor.**
**4. Walk-forward validation pentru top 3 strategii.**
**5. Output complet: CSV master + HTML reports + equity curves overlay.**

**[paste documentul de mai sus]**

**La sfârșit, dă-mi o recomandare bazată exclusiv pe date out-of-sample care strategie merită implementată live. Mulțumesc!"**

---

## ✅ Ce așteptăm să aflăm

După backtesting, vom ști cu certitudine:

1. **Care strategie are cel mai bun Sharpe** out-of-sample
2. **Care e cea mai robustă** la modificări de parametri
3. **Care a supraviețuit bear market-ului 2022** cel mai bine
4. **Dacă futures + leverage merită** complexitatea adăugată
5. **Dacă strategiile bat buy-and-hold** (dacă nu, atunci... mai bine HODL)

---

Vrei să modific ceva, sau e gata să-l trimiți lui Claude Code? Singura mea sugestie suplimentară: **rulează backtest-ul pe propriul calculator** (nu Claude Code direct) ca să poți itera rapid pe rezultate. Claude Code te ajută să **construiești framework-ul**, apoi tu îl execuți și analizezi.