# BTC Trading Bot — Backtest Framework

Backtest comparativ pentru 4 strategii de trading BTC pe perioada 2020-2024.

**Status:** v1 implementat. Vezi `SPEC.md` pentru specificația completă.

---

## Quick Start

```bash
# 1. Install dependencies (Python 3.11+ required, testat pe 3.12)
pip install -r requirements.txt

# 2. Download OHLCV data from Binance (~30 seconds, only first time)
python data_loader.py

# 3. Run full backtest matrix (15 backtests, ~30 seconds total)
python main.py

# 4. View results
# Deschide reports/summary.html în browser pentru overview + verdict
# Reports per-backtest: reports/{strategy}_{mode}.html
# Master CSV: reports/master.csv
```

---

## Ce face frameworkul

Rulează **15 backtests** pentru 4 strategii pe BTC/USDT (2020-2024):

| # | Strategie | Variante | Total |
|---|-----------|----------|-------|
| A | Complex Multi-Layer (Daily+4h, 6 layere, circuit breakers) | spot/futures1x/futures2x | 3 |
| B | Simple Pure Trend (Daily, EMA50/200 + volume) | spot/futures1x/futures2x | 3 |
| C | Hybrid (B + ADX filter + DD halt) | spot/futures1x/futures2x | 3 |
| D | Vol-Targeted Donchian Breakout (55/20) | spot/futures1x/futures2x | 3 |
| D-Alt | Robustness (20/10 + 40/15) | spot_1x only | 2 |
| BnH | Buy & Hold benchmark | spot_1x | 1 |
| **Total** | | | **15** |

Capital inițial: **$10,000**. Costuri reale (fees, slippage, funding, liquidation pentru futures 2x).

In-sample: 2020-2023 | Out-of-sample: 2024 (single evaluation, no re-tuning).

---

## Project Structure

```
m-trade/
├── config.py                  # Parametri globali
├── data_loader.py             # ccxt fetch + SQLite cache + CSV backup
├── main.py                    # Entry point
├── strategies/
│   ├── base.py                # BaseStrategy ABC
│   ├── strategy_a.py          # Complex Multi-Layer
│   ├── strategy_b.py          # Simple Pure Trend
│   ├── strategy_c.py          # Hybrid
│   └── strategy_d.py          # Vol-Targeted Donchian (parametric)
├── benchmarks/
│   └── buy_and_hold.py        # B&H benchmark
├── backtest/
│   ├── engine.py              # Custom backtest loop (NU backtesting.py)
│   ├── position.py            # Position, Order, Trade types
│   ├── costs.py               # Fees, slippage, funding, liquidation
│   ├── indicators.py          # EMA, SMA, ATR, RSI, ADX, Donchian (inline)
│   ├── metrics.py             # CAGR, Sharpe, Sortino, Calmar, drawdown, etc.
│   └── runner.py              # Orchestrare 15 backtests
├── analysis/
│   ├── compare.py             # master.csv + equity curves PNG
│   ├── reports.py             # HTML reports + verdict generation
│   └── templates/
│       └── report.html.j2     # Jinja2 template per-backtest
├── data/                      # Generated (gitignored)
│   ├── cache.sqlite           # OHLCV cache
│   └── BTCUSDT_*.csv          # Backup CSVs
├── reports/                   # Generated (gitignored)
│   ├── master.csv             # Toate metricile, 41 rows
│   ├── equity_curves.png      # Overlay comparativ
│   ├── summary.html           # Master summary + verdict
│   └── {strategy}_{mode}.html # Per-backtest detail
├── SPEC.md                    # Specificație completă (contract)
├── plan.md                    # Document original de design
└── requirements.txt
```

---

## Documentație

- **`SPEC.md`** — Specificația completă pe 26 secțiuni. Citire obligatorie înainte de modificări la strategii sau engine.
- **`plan.md`** — Documentul original de design (per-strategy reguli, motivație).

---

## Observații importante

- **Tests deferred** pentru v1 (vezi SPEC §24). Validare actuală = end-to-end run reușit.
- **Custom engine** (NU `backtesting.py`) — necesar pentru mixed timeframes (Strategia A), funding/liquidation realiste, vol-targeting în D. Vezi SPEC §2 pentru justificare.
- **Indicatori inline** (NU `pandas-ta`) — pandas-ta 0.3.x nu mai e pe PyPI și 0.4.x are conflict numpy. Toți 6 indicatori (EMA, SMA, ATR, RSI, ADX, Donchian) implementați cu pandas pur în `backtest/indicators.py`. Vezi SPEC changelog v1.2.

---

## Commands

```bash
# Re-fetch data (forțează re-download)
python -c "from data_loader import load_or_fetch; import config; load_or_fetch(config.SYMBOL, '1d', config.DATA_START, config.DATA_END, force_refresh=True)"

# Doar un singur backtest (debugging)
python -c "
import config
from data_loader import load_or_fetch
from backtest.engine import run_backtest
from strategies.strategy_d import StrategyD
daily = load_or_fetch(config.SYMBOL, '1d', config.DATA_START, config.DATA_END)
four_h = load_or_fetch(config.SYMBOL, '4h', config.DATA_START, config.DATA_END)
strat = StrategyD('spot_1x', n_entry=55, n_exit=20)
res = run_backtest(strat, daily, four_h, 'spot_1x', config.BACKTEST_START, config.BACKTEST_END)
print(f'Final: \${res.final_equity:.2f}, Trades: {len(res.trades)}')
"
```

---

## Limitări cunoscute

1. **Single asset** (BTC/USDT). Niciun multi-asset support.
2. **No optimization** by design (parametri fixați a priori în SPEC pentru anti-overfitting).
3. **5 ani de date** ≈ 1 ciclu market — power statistic limitat pentru extrapolare 10 ani. Vezi discuția în chat pentru context.
4. **End-of-2024**: bars 4h pe 2024-12-31 04:00-20:00 UTC nu sunt în cache (data fetch s-a oprit la 00:00 UTC). Impact: minim. Vezi SPEC v1.2 changelog.

---

## License

Personal project. No license.
