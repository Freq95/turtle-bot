"""
HTML report generation (per-backtest + master summary). See SPEC.md §22.3, §22.4, §26.
"""

from __future__ import annotations

import base64
import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

import config
from backtest.runner import BacktestRecord

log = logging.getLogger(__name__)


# ============================================================
# Chart helpers (matplotlib → base64 PNG)
# ============================================================

def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def _equity_chart(equity: pd.Series, title: str) -> str:
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(equity.index, equity.values, color="#1f77b4", linewidth=1.5)
    ax.set_yscale("log")
    ax.set_xlabel("Date")
    ax.set_ylabel("Equity (USD, log)")
    ax.set_title(title)
    ax.grid(True, which="both", alpha=0.3)
    # IS / OOS line
    boundary = pd.Timestamp(config.OUT_OF_SAMPLE_START, tz="UTC")
    ax.axvline(boundary, color="gray", linestyle="--", alpha=0.5)
    fig.tight_layout()
    return _fig_to_b64(fig)


def _drawdown_chart(equity: pd.Series, title: str) -> str:
    peak = equity.cummax()
    dd = (equity - peak) / peak * 100.0
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.fill_between(dd.index, dd.values, 0, color="#c44", alpha=0.5)
    ax.set_xlabel("Date")
    ax.set_ylabel("Drawdown (%)")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return _fig_to_b64(fig)


def _monthly_heatmap(equity: pd.Series) -> Optional[str]:
    """Heatmap of monthly returns: years × months."""
    if len(equity) < 30:
        return None
    daily = equity.resample("1D").last().dropna()
    monthly = daily.resample("ME").last()
    monthly_ret = monthly.pct_change().dropna() * 100.0
    if len(monthly_ret) == 0:
        return None
    pivot = monthly_ret.to_frame("ret")
    pivot["year"] = pivot.index.year
    pivot["month"] = pivot.index.month
    matrix = pivot.pivot_table(index="year", columns="month", values="ret", aggfunc="first")
    months_ordered = list(range(1, 13))
    matrix = matrix.reindex(columns=months_ordered)

    fig, ax = plt.subplots(figsize=(10, 4))
    data = matrix.to_numpy(dtype=float)
    vmax = max(abs(np.nanmin(data)), abs(np.nanmax(data)), 1.0)
    im = ax.imshow(data, cmap="RdYlGn", aspect="auto", vmin=-vmax, vmax=vmax)
    ax.set_xticks(range(12))
    ax.set_xticklabels(["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
    ax.set_yticks(range(len(matrix.index)))
    ax.set_yticklabels(matrix.index)
    # Cell labels
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            v = data[i, j]
            if not np.isnan(v):
                ax.text(j, i, f"{v:+.1f}", ha="center", va="center",
                        fontsize=8, color="black")
    ax.set_title("Monthly Returns (%)")
    fig.colorbar(im, ax=ax, label="Return %")
    fig.tight_layout()
    return _fig_to_b64(fig)


def _histogram_chart(equity: pd.Series) -> Optional[str]:
    if len(equity) < 30:
        return None
    daily = equity.resample("1D").last().dropna()
    returns = daily.pct_change().dropna() * 100.0
    if len(returns) < 10:
        return None
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.hist(returns.values, bins=50, color="#1f77b4", alpha=0.7, edgecolor="black")
    ax.axvline(returns.mean(), color="red", linestyle="--",
               label=f"Mean: {returns.mean():.2f}%")
    ax.axvline(0, color="gray", linestyle="-", alpha=0.5)
    ax.set_xlabel("Daily Return (%)")
    ax.set_ylabel("Count")
    ax.set_title(f"Daily Returns Distribution (σ={returns.std():.2f}%)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return _fig_to_b64(fig)


# ============================================================
# Per-record report rendering
# ============================================================

METRIC_ROWS = [
    ("period_initial_usd", "Initial Equity (USD)", lambda v: f"${v:,.2f}" if isinstance(v, (int, float)) else str(v)),
    ("period_final_usd",   "Final Equity (USD)",   lambda v: f"${v:,.2f}" if isinstance(v, (int, float)) else str(v)),
    ("total_return_pct",   "Total Return %",       lambda v: f"{v:+.2f}%" if isinstance(v, (int, float)) else str(v)),
    ("cagr_pct",           "CAGR %",               lambda v: f"{v:+.2f}%" if isinstance(v, (int, float)) else str(v)),
    ("max_dd_pct",         "Max Drawdown %",       lambda v: f"{v:.2f}%" if isinstance(v, (int, float)) else str(v)),
    ("avg_dd_pct",         "Avg Drawdown %",       lambda v: f"{v:.2f}%" if isinstance(v, (int, float)) else str(v)),
    ("dd_duration_days",   "Max DD Duration (days)", lambda v: f"{v}" if isinstance(v, (int, float)) else str(v)),
    ("sharpe",             "Sharpe Ratio",         lambda v: f"{v:.2f}" if isinstance(v, (int, float)) else str(v)),
    ("sortino",            "Sortino Ratio",        lambda v: f"{v:.2f}" if isinstance(v, (int, float)) else str(v)),
    ("calmar",             "Calmar Ratio",         lambda v: f"{v:.2f}" if isinstance(v, (int, float)) else str(v)),
    ("total_trades",       "Total Trades",         lambda v: f"{v}"),
    ("win_rate_pct",       "Win Rate %",           lambda v: f"{v:.2f}%" if isinstance(v, (int, float)) else str(v)),
    ("avg_win_pct",        "Avg Win %",            lambda v: f"{v:+.2f}%" if isinstance(v, (int, float)) else str(v)),
    ("avg_loss_pct",       "Avg Loss %",           lambda v: f"{v:+.2f}%" if isinstance(v, (int, float)) else str(v)),
    ("profit_factor",      "Profit Factor",        lambda v: f"{v:.2f}" if isinstance(v, (int, float)) else str(v)),
    ("best_trade_pct",     "Best Trade %",         lambda v: f"{v:+.2f}%" if isinstance(v, (int, float)) else str(v)),
    ("worst_trade_pct",    "Worst Trade %",        lambda v: f"{v:+.2f}%" if isinstance(v, (int, float)) else str(v)),
    ("avg_hold_days",      "Avg Hold (days)",      lambda v: f"{v:.1f}" if isinstance(v, (int, float)) else str(v)),
    ("time_in_market_pct", "Time in Market %",     lambda v: f"{v:.2f}%" if isinstance(v, (int, float)) else str(v)),
    ("avg_position_size_pct", "Avg Position Size %", lambda v: f"{v:.2f}%" if isinstance(v, (int, float)) else str(v)),
    ("total_fees_usd",     "Total Fees (USD)",     lambda v: f"${v:,.2f}" if isinstance(v, (int, float)) else str(v)),
    ("total_slippage_usd", "Total Slippage (USD)", lambda v: f"${v:,.2f}" if isinstance(v, (int, float)) else str(v)),
    ("total_funding_usd",  "Total Funding (USD)",  lambda v: f"${v:,.2f}" if isinstance(v, (int, float)) else str(v)),
    ("net_vs_gross_pct",   "Net vs Gross %",       lambda v: f"{v:+.2f}%" if isinstance(v, (int, float)) else str(v)),
    ("annual_2020_pct",    "2020 Return %",        lambda v: f"{v:+.2f}%" if isinstance(v, (int, float)) else str(v)),
    ("annual_2021_pct",    "2021 Return %",        lambda v: f"{v:+.2f}%" if isinstance(v, (int, float)) else str(v)),
    ("annual_2022_pct",    "2022 Return %",        lambda v: f"{v:+.2f}%" if isinstance(v, (int, float)) else str(v)),
    ("annual_2023_pct",    "2023 Return %",        lambda v: f"{v:+.2f}%" if isinstance(v, (int, float)) else str(v)),
    ("annual_2024_pct",    "2024 Return %",        lambda v: f"{v:+.2f}%" if isinstance(v, (int, float)) else str(v)),
]


def _get_template_env() -> Environment:
    tpl_dir = Path(__file__).parent / "templates"
    return Environment(
        loader=FileSystemLoader(str(tpl_dir)),
        autoescape=select_autoescape(["html"]),
    )


def render_report(rec: BacktestRecord) -> str:
    env = _get_template_env()
    template = env.get_template("report.html.j2")
    title = f"{rec.label} — Equity Curve"
    chart_equity = _equity_chart(rec.result.equity_curve, title)
    chart_drawdown = _drawdown_chart(rec.result.equity_curve, "Drawdown")
    chart_monthly = _monthly_heatmap(rec.result.equity_curve)
    chart_histogram = _histogram_chart(rec.result.equity_curve)
    return template.render(
        rec=rec,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        chart_equity=chart_equity,
        chart_drawdown=chart_drawdown,
        chart_monthly=chart_monthly,
        chart_histogram=chart_histogram,
        metric_rows=METRIC_ROWS,
    )


def generate_all_html_reports(records: list[BacktestRecord], out_dir: str) -> None:
    for rec in records:
        html = render_report(rec)
        path = Path(out_dir) / f"{rec.label}.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        log.info("Wrote %s", path)


# ============================================================
# Master summary HTML
# ============================================================

def _build_verdict(records: list[BacktestRecord]) -> dict:
    """
    Per SPEC §26.2 decision criteria.
    Returns dict with ranking, beats_bnh list, recommendation.
    """
    bnh_oos = None
    for r in records:
        if r.strategy_key == "BnH" and r.metrics_oos:
            bnh_oos = r.metrics_oos
            break

    if bnh_oos is None:
        return {"recommendation": "No B&H benchmark available", "ranking": []}

    candidates = []
    for r in records:
        if r.purpose != "main":
            continue
        if r.metrics_oos is None:
            continue
        m = r.metrics_oos
        candidates.append({
            "rec": r,
            "cagr": m.get("cagr_pct", 0.0),
            "sharpe": m.get("sharpe", 0.0),
            "calmar": m.get("calmar", 0.0),
            "max_dd": m.get("max_dd_pct", 0.0),
            "is_cagr": r.metrics_is.get("cagr_pct", 0.0) if r.metrics_is else 0.0,
        })

    # Sort by OOS Sharpe
    candidates.sort(key=lambda x: x["sharpe"], reverse=True)

    # Evaluation
    bnh_cagr = bnh_oos.get("cagr_pct", 0.0)
    bnh_sharpe = bnh_oos.get("sharpe", 0.0)
    bnh_calmar = bnh_oos.get("calmar", 0.0)

    qualified = []
    for c in candidates:
        passes_cagr = c["cagr"] > 0
        passes_sharpe = c["sharpe"] >= 1.0
        passes_dd = abs(c["max_dd"]) < 30.0
        # Robustness gap
        if c["is_cagr"] != 0:
            gap = abs((c["is_cagr"] - c["cagr"]) / c["is_cagr"])
        else:
            gap = float("inf")
        passes_robust = gap < 0.5
        # Beat B&H
        beats = sum([
            c["cagr"] > bnh_cagr,
            c["sharpe"] > bnh_sharpe,
            c["calmar"] > bnh_calmar,
        ])
        passes_beats = beats >= 2

        c["passes_cagr"] = passes_cagr
        c["passes_sharpe"] = passes_sharpe
        c["passes_dd"] = passes_dd
        c["passes_robust"] = passes_robust
        c["passes_beats"] = passes_beats
        c["beats_count"] = beats
        c["robust_gap"] = gap

        if all([passes_cagr, passes_sharpe, passes_dd, passes_robust, passes_beats]):
            qualified.append(c)

    if qualified:
        recommendation = (f"<strong>{qualified[0]['rec'].label}</strong> recommended "
                          f"for live deployment (highest OOS Sharpe among qualified). "
                          f"Caveats apply — see SPEC §26.")
    else:
        recommendation = ("<strong>NO STRATEGY QUALIFIED.</strong> Recommendation: "
                          "stay with Buy & Hold or do not deploy. See SPEC §26.2 criteria.")

    return {
        "recommendation": recommendation,
        "ranking": candidates,
        "qualified": qualified,
        "bnh_metrics": bnh_oos,
    }


def generate_summary_html(records: list[BacktestRecord], out_dir: str) -> None:
    verdict = _build_verdict(records)

    # Build simple summary HTML
    rows = []
    for r in records:
        m_full = r.metrics_full
        m_oos = r.metrics_oos if r.metrics_oos else {}
        rows.append({
            "label": r.label,
            "strategy": r.strategy_key,
            "mode": r.mode,
            "direction": r.direction,
            "purpose": r.purpose,
            "cagr_full": m_full.get("cagr_pct", 0),
            "sharpe_full": m_full.get("sharpe", 0),
            "maxdd_full": m_full.get("max_dd_pct", 0),
            "trades": m_full.get("total_trades", 0),
            "cagr_oos": m_oos.get("cagr_pct", "-"),
            "sharpe_oos": m_oos.get("sharpe", "-"),
            "maxdd_oos": m_oos.get("max_dd_pct", "-"),
        })

    def fmt(v, suffix="%", dec=2):
        if isinstance(v, (int, float)):
            return f"{v:.{dec}f}{suffix}"
        return str(v)

    html_parts = ["""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>BTC Backtest — Master Summary</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       max-width: 1400px; margin: 0 auto; padding: 24px; background: #fafafa; }
h1, h2 { color: #222; }
table { border-collapse: collapse; width: 100%; background: white; margin: 16px 0; }
th, td { border: 1px solid #ddd; padding: 8px 12px; text-align: right; }
th { background: #f0f0f0; font-weight: 600; font-size: 13px; }
th:first-child, td:first-child { text-align: left; }
td.num { font-variant-numeric: tabular-nums; }
.positive { color: #2a8c4a; }
.negative { color: #c44; }
.verdict { background: #fff8dc; border: 2px solid #ffd700; padding: 16px;
           border-radius: 6px; margin: 24px 0; }
.pass { color: green; font-weight: bold; }
.fail { color: red; }
img { max-width: 100%; border: 1px solid #ddd; }
</style></head><body>"""]

    html_parts.append("<h1>BTC Backtest — Master Summary</h1>")
    html_parts.append(f"<p><em>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</em></p>")

    # Verdict
    html_parts.append("<div class='verdict'>")
    html_parts.append("<h2>Final Verdict</h2>")
    html_parts.append(f"<p>{verdict['recommendation']}</p>")
    html_parts.append("</div>")

    # Matrix table
    html_parts.append("<h2>Test Matrix Summary (Full Period)</h2>")
    html_parts.append("<table>")
    html_parts.append("<tr><th>Strategy</th><th>Mode</th><th>Direction</th><th>Purpose</th>"
                      "<th>CAGR%</th><th>Sharpe</th><th>MaxDD%</th><th>Trades</th>"
                      "<th>OOS CAGR%</th><th>OOS Sharpe</th><th>OOS MaxDD%</th>"
                      "<th>Report</th></tr>")
    for r in rows:
        cagr_class = "positive" if isinstance(r["cagr_full"], (int, float)) and r["cagr_full"] > 0 else "negative"
        oos_cagr_class = ""
        if isinstance(r["cagr_oos"], (int, float)):
            oos_cagr_class = "positive" if r["cagr_oos"] > 0 else "negative"
        html_parts.append(
            f"<tr><td>{r['strategy']}</td><td>{r['mode']}</td><td>{r['direction']}</td>"
            f"<td>{r['purpose']}</td>"
            f"<td class='num {cagr_class}'>{fmt(r['cagr_full'])}</td>"
            f"<td class='num'>{fmt(r['sharpe_full'], '', 2)}</td>"
            f"<td class='num negative'>{fmt(r['maxdd_full'])}</td>"
            f"<td class='num'>{r['trades']}</td>"
            f"<td class='num {oos_cagr_class}'>{fmt(r['cagr_oos'])}</td>"
            f"<td class='num'>{fmt(r['sharpe_oos'], '', 2)}</td>"
            f"<td class='num negative'>{fmt(r['maxdd_oos'])}</td>"
            f"<td><a href='{r['label']}.html'>open</a></td></tr>"
        )
    html_parts.append("</table>")

    # Equity overlay
    html_parts.append("<h2>Equity Curves Overlay</h2>")
    html_parts.append("<img src='equity_curves.png' alt='Equity curves overlay'>")

    # Ranking
    html_parts.append("<h2>OOS Ranking by Sharpe (Main Eval Only)</h2>")
    html_parts.append("<table>")
    html_parts.append("<tr><th>Rank</th><th>Strategy</th><th>OOS CAGR%</th><th>OOS Sharpe</th>"
                      "<th>OOS MaxDD%</th><th>IS-OOS Gap</th>"
                      "<th>CAGR>0</th><th>Sharpe≥1</th><th>DD&lt;30%</th>"
                      "<th>Robust&lt;50%</th><th>Beat B&H ≥2/3</th></tr>")
    for i, c in enumerate(verdict["ranking"], start=1):
        def chk(b):
            return "<span class='pass'>✓</span>" if b else "<span class='fail'>✗</span>"
        html_parts.append(
            f"<tr><td>{i}</td><td>{c['rec'].label}</td>"
            f"<td class='num'>{fmt(c['cagr'])}</td>"
            f"<td class='num'>{fmt(c['sharpe'], '', 2)}</td>"
            f"<td class='num negative'>{fmt(c['max_dd'])}</td>"
            f"<td class='num'>{fmt(c['robust_gap']*100 if c['robust_gap'] != float('inf') else 999)}</td>"
            f"<td>{chk(c['passes_cagr'])}</td>"
            f"<td>{chk(c['passes_sharpe'])}</td>"
            f"<td>{chk(c['passes_dd'])}</td>"
            f"<td>{chk(c['passes_robust'])}</td>"
            f"<td>{chk(c['passes_beats'])} ({c['beats_count']}/3)</td>"
            "</tr>"
        )
    html_parts.append("</table>")

    # B&H benchmark
    bnh = verdict.get("bnh_metrics", {})
    if bnh:
        html_parts.append("<h2>Buy & Hold Benchmark (OOS)</h2>")
        html_parts.append(
            f"<p>CAGR: {fmt(bnh.get('cagr_pct', 0))} | "
            f"Sharpe: {fmt(bnh.get('sharpe', 0), '', 2)} | "
            f"MaxDD: {fmt(bnh.get('max_dd_pct', 0))} | "
            f"Calmar: {fmt(bnh.get('calmar', 0), '', 2)}</p>"
        )

    html_parts.append("</body></html>")

    path = Path(out_dir) / "summary.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(html_parts))
    log.info("Wrote %s", path)
