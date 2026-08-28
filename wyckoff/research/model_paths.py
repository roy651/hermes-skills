#!/usr/bin/env python3
"""Fit the multi-scale path features. Protocol fixed BEFORE the first run.

PRE-REGISTERED — changing any of this after seeing results invalidates the test:

  features   the 29 rank-standardised (_z) path features, nothing else
  target     BINARY, sign of forward 6-month return relative to sector on the same date.
             Binary because means misled us repeatedly here; a sign is robust to the tails
             that made ROE and gross profitability look real when they were not.
  model      HistGradientBoostingClassifier, max_depth 3, 200 iters, min_leaf 50 — the same
             conservative settings used elsewhere. No tuning; tuning on this data IS the leak.
  validation expanding walk-forward by date, >= 24 training dates, EMBARGO of 6 monthly dates
             because a 6-month forward return overlaps the next 5 observations
  metric     Fama-MacBeth t on the per-date rank correlation between prediction and outcome
  control    permutation of outcomes WITHIN date (the parametric t over-counts: rows inside a
             date are correlated, which is what fooled us at 40 batches in the blind test)
  bar        |t| >= 2 AND outside the 95% permutation band. Both, not either.

Usage:  model_paths.py [--per-sector]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import HistGradientBoostingClassifier

CACHE = Path(__file__).parent / "cache"
MIN_TRAIN_DATES = 24
EMBARGO = 6
SEED = 0


def walk_forward(p: pd.DataFrame, feats: list[str]) -> pd.DataFrame:
    dates = sorted(p.date.unique())
    out = []
    for k in range(MIN_TRAIN_DATES, len(dates)):
        test_d = dates[k]
        train_d = dates[:max(0, k - EMBARGO)]
        if len(train_d) < MIN_TRAIN_DATES:
            continue
        tr, te = p[p.date.isin(train_d)], p[p.date == test_d]
        if len(tr) < 800 or len(te) < 20:
            continue
        m = HistGradientBoostingClassifier(max_depth=3, max_iter=200, learning_rate=0.05,
                                           min_samples_leaf=50, l2_regularization=1.0,
                                           random_state=SEED)
        m.fit(tr[feats].to_numpy(), tr.up.to_numpy())
        o = te[["ticker", "date", "y", "up"]].copy()
        o["pred"] = m.predict_proba(te[feats].to_numpy())[:, 1]
        out.append(o)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def evaluate(res: pd.DataFrame, label: str) -> None:
    if res.empty or res.date.nunique() < 12:
        print(f"{label:<26} too few dates"); return
    per = res.groupby("date").apply(
        lambda g: stats.spearmanr(g.pred, g.y)[0] if len(g) > 5 else np.nan,
        include_groups=False).dropna()
    t = per.mean() / (per.std(ddof=1) / np.sqrt(len(per)))

    rng = np.random.default_rng(3)
    null = []
    for _ in range(1000):
        s = res.copy()
        s["y"] = s.groupby("date")["y"].transform(lambda x: rng.permutation(x.values))
        q = s.groupby("date").apply(
            lambda g: stats.spearmanr(g.pred, g.y)[0] if len(g) > 5 else np.nan,
            include_groups=False).dropna()
        null.append(q.mean())
    lo, hi = np.percentile(null, [2.5, 97.5])
    inside = lo <= per.mean() <= hi

    top = res[res.groupby("date")["pred"].transform(lambda s: s >= s.quantile(0.8))]
    bot = res[res.groupby("date")["pred"].transform(lambda s: s <= s.quantile(0.2))]
    sp = (top.groupby("date").y.mean() - bot.groupby("date").y.mean()).dropna()
    spm = (top.groupby("date").y.median() - bot.groupby("date").y.median()).dropna()

    print(f"{label:<26} n={len(res):<6} dates={len(per):<4} rank-corr={per.mean():+.4f} "
          f"t={t:+.2f}  null±{hi:.4f} [{'INSIDE' if inside else 'OUTSIDE'}]  "
          f"q5-q1 mean {sp.mean():+.2f}pp med {spm.mean():+.2f}pp")


def main():
    p = pd.read_pickle(CACHE / "path_features.pkl")
    feats = sorted(c for c in p.columns if c.endswith("_z"))
    p = p.dropna(subset=feats + ["y"]).copy()
    p["up"] = (p.y > 0).astype(int)
    print(f"[model] {len(p):,} rows · {p.ticker.nunique()} tickers · {len(feats)} features · "
          f"{p.date.nunique()} dates · base rate {p.up.mean()*100:.1f}%\n", file=sys.stderr)

    evaluate(walk_forward(p, feats), "POOLED (3 sectors)")
    if "--per-sector" in sys.argv:
        for s, g in p.groupby("sector"):
            if g.date.nunique() < MIN_TRAIN_DATES + EMBARGO + 12:
                continue
            evaluate(walk_forward(g, feats), s[:24])


if __name__ == "__main__":
    main()
