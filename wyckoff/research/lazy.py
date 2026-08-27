#!/usr/bin/env python3
"""Lazy Prices and Loughran-McDonald: does the LANGUAGE of a filing predict returns?

Two ideas, one dataset, both deterministic — which is why they come before any LLM reading of
filings: nothing here can be contaminated by knowing the outcome.

  Lazy Prices (Cohen, Malloy & Nguyen, JF 2020). Firms mostly copy last year's filing forward.
  When they DON'T — when the language changes materially — it tends to precede underperformance.
  Measured as cosine similarity between a filing's term vector and the SAME QUARTER's filing a
  year earlier, so seasonal boilerplate differences do not masquerade as change.

  Loughran-McDonald. Finance-specific sentiment. Both the LEVEL (how negative is this filing?)
  and the CHANGE versus a year ago, since a shift in tone may carry more than its level.

The comparison must be same-quarter year-on-year: a Q3 10-Q against a Q2 10-Q would differ for
calendar reasons alone and the measure would read that as news.

Usage:  lazy.py
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CACHE = Path(__file__).parent / "cache"
HORIZONS = [21, 63, 126]
LM_TAGS = ["negative", "positive", "uncertainty", "litigious", "constraining"]


def cosine(a: dict, b: dict) -> float:
    keys = set(a) | set(b)
    va = np.array([a.get(k, 0) for k in keys], dtype=float)
    vb = np.array([b.get(k, 0) for k in keys], dtype=float)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    return float(va @ vb / (na * nb)) if na and nb else np.nan


def build() -> pd.DataFrame:
    vecs = pickle.load(open(CACHE / "filing_vectors.pkl", "rb"))
    panel = pickle.load(open(CACHE / "panel.pkl", "rb"))
    us = [t for t in vecs if t in panel and panel[t] is not None and len(panel[t]) > 600]
    close = pd.DataFrame({t: panel[t]["close"] for t in us}).sort_index()
    bench = {h: (close.shift(-h) / close - 1).mean(axis=1) for h in HORIZONS}
    idx = close.index

    rows = []
    for t in us:
        recs = sorted(vecs[t], key=lambda r: r["filed"])
        for r in recs:
            r["_d"] = pd.Timestamp(r["filed"])
            r["_q"] = r["_d"].quarter
        px = close[t].to_numpy()
        for i, r in enumerate(recs):
            # same quarter, roughly one year earlier, same form type
            prior = [p for p in recs[:i]
                     if p["form"] == r["form"] and p["_q"] == r["_q"]
                     and 300 <= (r["_d"] - p["_d"]).days <= 430]
            if not prior:
                continue
            p = prior[-1]
            sim = cosine(r["terms"], p["terms"])
            if not np.isfinite(sim):
                continue
            j = idx.searchsorted(r["_d"], side="left")
            if j >= len(idx) or not np.isfinite(px[j]) or px[j] <= 0:
                continue
            rec = {"ticker": t, "filed": idx[j], "sim": sim,
                   "len_chg": r["n_words"] / p["n_words"] - 1 if p["n_words"] else np.nan}
            for tag in LM_TAGS:
                k = f"lm_{tag}"
                if k in r and k in p:
                    rec[k] = r[k]
                    rec[f"d_{tag}"] = r[k] - p[k]
            keep = False
            for h in HORIZONS:
                jj, b = j + h, bench[h].to_numpy()[j]
                if jj < len(idx) and np.isfinite(px[jj]) and np.isfinite(b):
                    rec[f"x{h}"] = (px[jj] / px[j] - 1 - b) * 100
                    keep = True
                else:
                    rec[f"x{h}"] = np.nan
            if keep:
                rows.append(rec)
    df = pd.DataFrame(rows)
    df.to_pickle(CACHE / "lazy_events.pkl")
    print(f"[lazy] {len(df):,} filing pairs · {df.ticker.nunique()} tickers · "
          f"{df.filed.min().date()}..{df.filed.max().date()}", file=sys.stderr)
    print(f"[lazy] similarity: median {df.sim.median():.3f}  "
          f"p10 {df.sim.quantile(.1):.3f}  p90 {df.sim.quantile(.9):.3f}", file=sys.stderr)
    return df


SIGNALS = ["sim", "len_chg"] + [f"lm_{t}" for t in LM_TAGS] + [f"d_{t}" for t in LM_TAGS]


def report(df: pd.DataFrame) -> None:
    pd.set_option("display.width", 200)
    for h in HORIZONS:
        col = f"x{h}"
        sub = df.dropna(subset=[col]).copy()
        sub["q"] = sub.filed.dt.to_period("Q")
        out = []
        for sig in SIGNALS:
            if sig not in sub.columns:
                continue
            s = sub.dropna(subset=[sig]).copy()
            if len(s) < 1500:
                continue
            # Terciles, not deciles: 263 companies is ~125 filings a quarter, so deciles would
            # be a dozen names each and the extremes would be noise.
            s["g"] = s.groupby("q")[sig].transform(
                lambda x: pd.qcut(x.rank(method="first"), 3, labels=False, duplicates="drop"))
            hi = s[s.g == 2].groupby("q")[col].mean()
            lo = s[s.g == 0].groupby("q")[col].mean()
            sp = (hi - lo).dropna()
            him = s[s.g == 2].groupby("q")[col].median()
            lom = s[s.g == 0].groupby("q")[col].median()
            spm = (him - lom).dropna()
            if len(sp) < 12:
                continue
            t = sp.mean() / (sp.std(ddof=1) / np.sqrt(len(sp)))
            tm = spm.mean() / (spm.std(ddof=1) / np.sqrt(len(spm)))
            out.append({"signal": sig, "n": len(s), "mean_sp%": sp.mean(), "t_mean": t,
                        "med_sp%": spm.mean(), "t_med": tm,
                        "agree": "yes" if np.sign(sp.mean()) == np.sign(spm.mean()) else "NO",
                        "qtrs": len(sp)})
        r = pd.DataFrame(out).sort_values("t_med", key=abs, ascending=False)
        print(f"\n=== HORIZON {h} — top minus bottom TERCILE, quarterly Fama-MacBeth ===")
        print(r.to_string(index=False, float_format=lambda v: f"{v:8.2f}"))


if __name__ == "__main__":
    f = CACHE / "lazy_events.pkl"
    df = pd.read_pickle(f) if ("--reuse" in sys.argv and f.exists()) else build()
    report(df)
