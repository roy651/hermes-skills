#!/usr/bin/env python3
"""Phase 2-3: earnings surprise (SUE) and the drift that follows it.

SUE = (actual EPS - expected EPS) / sigma(that company's own past surprises). The expectation
is a SEASONAL RANDOM WALK WITH DRIFT — the same quarter a year ago, plus the average recent
year-on-year change. No analyst consensus, so nothing here costs money, and the literature
prefers this form precisely because it keeps small, thinly-covered names in the sample, which
is where the drift is strongest.

Two timing decisions that decide whether this is honest:

1. **The event is the FILING date, not the period end.** That is when the number became public.
2. **Returns are measured from the close ON the filing date.** Press releases usually precede
   the 10-Q by a few days, so the announcement pop is largely already in that close. This makes
   the measurement CONSERVATIVE — we are deliberately buying after the jump and asking whether
   the *drift* pays, which is the only version of this that is tradable.

Benchmark is the equal-weighted panel over the identical window, so "excess" means "better than
the average stock over the same days", consistent with every other study in this repo.

Usage:  sue.py [--min-history 8]
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CACHE = Path(__file__).parent / "cache"
HORIZONS = [5, 10, 21, 63]
MIN_HISTORY = 8          # prior surprises needed before sigma means anything


def compute_sue(events: pd.DataFrame, min_history: int = MIN_HISTORY) -> pd.DataFrame:
    """One SUE per event, using only information available at that event."""
    out = []
    for t, g in events.groupby("ticker", sort=False):
        g = g.sort_values("period_end").reset_index(drop=True)
        g["q"] = g.period_end.dt.quarter
        g["yr"] = g.period_end.dt.year
        lookup = {(r.yr, r.q): r.eps for r in g.itertuples()}
        g["eps_yoy"] = [lookup.get((r.yr - 1, r.q)) for r in g.itertuples()]
        g = g.dropna(subset=["eps_yoy"])
        if len(g) < min_history + 1:
            continue
        # Seasonal random walk WITH DRIFT: last year's quarter plus the average of the four most
        # recent year-on-year changes, all strictly lagged so nothing peeks at the current print.
        yoy_change = g.eps - g.eps_yoy
        drift = yoy_change.shift(1).rolling(4, min_periods=2).mean()
        g["expected"] = g.eps_yoy + drift.fillna(0.0)
        g["surprise"] = g.eps - g.expected
        # sigma from PRIOR surprises only
        g["sigma"] = g.surprise.shift(1).rolling(min_history, min_periods=min_history).std()
        g = g[g.sigma > 0]
        if g.empty:
            continue
        g["sue"] = g.surprise / g.sigma
        out.append(g[["ticker", "period_end", "filed", "eps", "expected", "surprise", "sue"]])
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def price_matrices() -> tuple[pd.DataFrame, dict[int, pd.Series]]:
    """Prices, plus a per-horizon benchmark that is APPLES-TO-APPLES with a single stock.

    The first version compounded the cross-sectional mean DAILY return into an index and
    benchmarked against that. It made every decile look negative — because a daily-rebalanced
    equal-weighted index mechanically beats the average individual stock (less volatility drag),
    so the comparison was rigged before any signal was measured.

    The correct benchmark for "I bought one stock and held it h days" is "the average of what
    every stock did over those same h days" — the cross-sectional mean of h-period BUY-AND-HOLD
    returns, not a rebalanced index.
    """
    panel = pickle.load(open(CACHE / "panel.pkl", "rb"))
    us = [t for t, d in panel.items()
          if d is not None and "." not in t and not t.startswith("^") and len(d) > 400]
    close = pd.DataFrame({t: panel[t]["close"] for t in us}).sort_index()
    bench = {h: (close.shift(-h) / close - 1).mean(axis=1) for h in HORIZONS}
    return close, bench


def attach_returns(sue: pd.DataFrame, close: pd.DataFrame, bench: dict) -> pd.DataFrame:
    idx = close.index
    rows = []
    bench_v = {h: bench[h].to_numpy() for h in HORIZONS}
    for t, g in sue.groupby("ticker", sort=False):
        if t not in close.columns:
            continue
        px = close[t].to_numpy()
        for r in g.itertuples():
            i = idx.searchsorted(r.filed, side="left")     # first session on/after the filing
            if i >= len(idx) or not np.isfinite(px[i]) or px[i] <= 0:
                continue
            rec = {"ticker": t, "filed": idx[i], "sue": r.sue, "eps": r.eps,
                   "surprise": r.surprise}
            ok = False
            for h in HORIZONS:
                j = i + h
                if j >= len(idx) or not np.isfinite(px[j]):
                    rec[f"x{h}"] = np.nan
                    continue
                b = bench_v[h][i]
                if not np.isfinite(b):
                    rec[f"x{h}"] = np.nan
                    continue
                rec[f"x{h}"] = (px[j] / px[i] - 1 - b) * 100
                ok = True
            if ok:
                rows.append(rec)
    return pd.DataFrame(rows)


def fm(g: pd.DataFrame, col: str) -> tuple[float, float]:
    q = g.groupby(g.filed.dt.to_period("M"))[col].mean().dropna()
    if len(q) < 12:
        return np.nan, np.nan
    return q.mean(), q.mean() / (q.std(ddof=1) / np.sqrt(len(q)))


def report(df: pd.DataFrame) -> None:
    df = df.dropna(subset=["sue"]).copy()
    df["decile"] = df.groupby(df.filed.dt.to_period("M"))["sue"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 10, labels=False, duplicates="drop") + 1)
    pd.set_option("display.width", 200)
    print(f"\n{len(df):,} events with SUE · {df.ticker.nunique()} tickers · "
          f"{df.filed.min().date()}..{df.filed.max().date()}")

    for h in HORIZONS:
        col = f"x{h}"
        sub = df.dropna(subset=[col])
        if sub.empty:
            continue
        rows = []
        for d, g in sub.groupby("decile"):
            mu, t = fm(g, col)
            rows.append({"decile": int(d), "n": len(g), "mean_sue": g.sue.mean(),
                         f"mean_x{h}": mu, "t": t, "med": g[col].median(),
                         "win%": (g[col] > 0).mean() * 100})
        tab = pd.DataFrame(rows)
        hi = sub[sub.decile == 10].groupby(sub.filed.dt.to_period("M"))[col].mean()
        lo = sub[sub.decile == 1].groupby(sub.filed.dt.to_period("M"))[col].mean()
        sp = (hi - lo).dropna()
        tstat = sp.mean() / (sp.std(ddof=1) / np.sqrt(len(sp))) if len(sp) > 12 else np.nan
        print(f"\n=== HORIZON {h} sessions ===")
        print(tab.to_string(index=False, float_format=lambda v: f"{v:8.2f}"))
        print(f"  TOP minus BOTTOM decile: {sp.mean():+.2f}%  t={tstat:+.2f}  "
              f"over {len(sp)} months  ·  positive in {100*(sp>0).mean():.0f}% of months")


def main():
    mh = int(sys.argv[sys.argv.index("--min-history") + 1]) if "--min-history" in sys.argv else MIN_HISTORY
    ev = pd.read_pickle(CACHE / "eps_events.pkl")
    print(f"[sue] {len(ev):,} raw events", file=sys.stderr)
    s = compute_sue(ev, mh)
    print(f"[sue] {len(s):,} events with a usable SUE", file=sys.stderr)
    close, bench = price_matrices()
    print(f"[sue] price matrix {close.shape}", file=sys.stderr)
    df = attach_returns(s, close, bench)
    df.to_pickle(CACHE / "sue_events.pkl")
    print(f"[sue] {len(df):,} events with forward returns", file=sys.stderr)
    report(df)


if __name__ == "__main__":
    main()
