#!/usr/bin/env python3
"""Single-ticker deep-dive DATA for the conversational agent (and humans).

Prints the deterministic exit-engine breakdown for one ticker — the 0-9 deterioration score with every
criterion, the trailing-stop math, the scale-out ladder decision and *why*, the Wyckoff structure, plus
real catalysts (earnings + recent headlines). It does NOT call an LLM and does NOT post to Telegram:
the conversational agent reads this output and reasons over it using README.md + DESIGN.md (the
analytical lens). This is the data feed behind "why is X a trim?" / "should I add Y?" follow-ups.

Usage:  explain.py <TICKER>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")

import yaml
import data as market_data
import holdings as portfolio
import risk
import deterioration as det
import ladder
import events
import finnhub
from prescreener import _get_spy_context

_BIG_PV = 1e12   # single-ticker view: keep the portfolio-level concentration cap from binding here


def _catalysts(ticker: str) -> dict:
    out = {"earnings_soon": False, "headlines": []}
    try:
        out["earnings_soon"] = ticker in finnhub.earnings_within({ticker}, days=14)
    except Exception:
        pass
    try:
        out["headlines"] = [n["headline"] for n in finnhub.company_news(ticker, days=21, limit=6)]
    except Exception:
        pass
    return out


def explain(ticker: str) -> None:
    ticker = ticker.upper()
    cfg = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())
    lookback = cfg.get("llm", {}).get("lookback_days", 120)

    td = market_data.fetch_ohlcv(ticker, days=lookback)
    df = td.df
    price = float(df["close"].iloc[-1])
    held = portfolio.load()
    is_held = ticker in held
    try:
        mkt = _get_spy_context()
    except Exception:
        mkt = None
    if mkt is not None:
        try:
            _spy = market_data.fetch_ohlcv("SPY", days=lookback).df["close"]
            mkt["spy_window_return"] = float(_spy.iloc[-1] / _spy.iloc[0] - 1)
        except Exception:
            pass

    sym = {"USD": "$", "ILS": "₪"}.get(td.currency, td.currency + " ")
    head = f"{ticker} ({td.name})" if td.name != ticker else ticker
    out = [f"=== {head} — {'HELD' if is_held else 'not held'} ==="]

    loss_pct = None
    if is_held:
        h = held[ticker]
        cost_local = h["avg_cost"] / 100 if td.currency == "ILS" else h["avg_cost"]
        loss_pct = (price / cost_local - 1) if cost_local else None
        pnl = loss_pct * 100 if loss_pct is not None else 0.0
        out.append(f"{h['qty']} sh @ {sym}{cost_local:.2f} · now {sym}{price:.2f} ({'+' if pnl >= 0 else ''}{pnl:.1f}%)  [{td.currency}]")
    else:
        out.append(f"{sym}{price:.2f}  [{td.currency}]")

    ds = det.deterioration_score(df, mkt, loss_pct=loss_pct)
    out.append(f"\nEXIT SCORE: {ds['score']}/9")
    for k, v in ds["criteria"].items():
        out.append(f"  {'✓' if v else '·'} {k}: {v}")
    out.append(f"  [flags] established_markdown={ds['established_markdown']} · "
               f"has_structural={ds['has_structural']} · thin_volume={ds['thin_volume']}")

    if is_held:
        state = risk.load_state()                       # not saved -> read-only
        rk = risk.assess(ticker, df, held[ticker]["qty"], state=state)
        out.append(f"\nTRAILING STOP: {sym}{rk['stop']} ({rk['stop_type']}) · {rk['distance_pct']}% below · stop_hit={rk['stop_hit']}")
        out.append(f"  ATR(14) {rk['atr']} · highest-high(since first-seen) {rk['highest_high']} · "
                   f"baseline {rk['baseline_qty']} sh · max_stage {rk['max_stage']}")
        evs = events.detect_events(df)
        baseline = rk["baseline_qty"] or held[ticker]["qty"]
        ratio = held[ticker]["qty"] / baseline if baseline else 1.0
        executed_stage = 2 if ratio <= 0.625 else 1 if ratio <= 0.875 else 0
        rec = ladder.recommend(
            qty=held[ticker]["qty"], price=price, portfolio_value=_BIG_PV,
            is_core=(ticker == "DGRO"), det_score=ds["score"], stop_hit=rk["stop_hit"],
            max_stage=executed_stage, baseline_qty=baseline,
            has_entry_event=events.has_entry_event(evs), has_structural=ds["has_structural"],
            established_markdown=ds["established_markdown"],
        )
        action = rec["action"]
        if action.startswith("ADD"):
            out.append("\nLADDER: ADD candidate · stage 0 — clean (0/9) with a fresh entry setup, below target")
            out.append("  NOTE: the add SIZE depends on portfolio value (the 20% cap) — see the full report for the share target (this single-ticker view can't size it).")
        else:
            out.append(f"\nLADDER: {action}  (Δ {rec['delta_qty']:+g} sh) · stage {rec['stage']}")
            out.append(f"  reason: {rec['reason']}")
            out.append("  NOTE: the 20% concentration cap is portfolio-level (needs total value) — not applied in this single-ticker view; see the full report.")
    else:
        out.append("\n(not held — no stop/ladder; structural read only)")

    cat = _catalysts(ticker)
    out.append(f"\nCATALYSTS:  earnings≤14d: {'YES' if cat['earnings_soon'] else 'no'}")
    for hl in cat["headlines"]:
        out.append(f"   - {hl}")

    rng = ds["events"].get("range")
    out.append("\nWYCKOFF STRUCTURE:")
    out.append(f"  range: {rng if rng else 'none detected'}")
    for kk in ("upthrust", "sow", "lpsy", "support_break"):
        if ds["events"].get(kk):
            out.append(f"  {kk}: {ds['events'][kk]}")

    print("\n".join(out))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: explain.py <TICKER>")
        sys.exit(1)
    explain(sys.argv[1])
