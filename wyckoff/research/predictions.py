#!/usr/bin/env python3
"""Score every US name on a weekly grid, so the backtest can rebalance at any cadence.

`metalabel.py` predicts only on the 113 monthly observation dates, which is right for
measuring a signal but cannot answer "what if we rebalanced weekly?". This builds the same
walk-forward predictions on a denser grid: every Friday plus every month-end. Weekly,
bi-weekly, monthly and bi-monthly cadences are then all subsets of one artifact.

The honesty constraint that matters: a grid date d is scored by the model trained only on
observation dates whose 6-month forward windows had already closed before d. That is the same
embargo `metalabel.py` applies, expressed so it does not depend on the rebalance cadence —
otherwise a weekly backtest would quietly train on its own future.

Usage:  predictions.py [--features base+liq]
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import detectors as D
import features_extra as FX
import metalabel as ML

CACHE = Path(__file__).parent / "cache"
MOM_THRESHOLD = 0.30       # the validated primary
MIN_PRICE = 5.0
MIN_BARS = 260


def grid_dates(spine: pd.DatetimeIndex, start="2016-09-01") -> list[pd.Timestamp]:
    """Fridays (or the last trading day of each week) plus month-ends."""
    s = pd.Series(spine, index=spine)
    weekly = s.groupby([spine.isocalendar().year, spine.isocalendar().week]).max()
    monthly = s.groupby([spine.year, spine.month]).max()
    both = pd.DatetimeIndex(sorted(set(weekly) | set(monthly)))
    return [d for d in both if d >= pd.Timestamp(start)]


def build_grid(feature_set=ML.DEFAULT_FEATURES) -> pd.DataFrame:
    panel = pickle.load(open(CACHE / "panel.pkl", "rb"))
    mkt = FX.market_context(panel)
    sec_map = FX.sectors()
    sec_codes = {s: i for i, s in enumerate(sorted(set(sec_map.values())))}

    us = [t for t, df in panel.items()
          if df is not None and len(df) > 600 and "." not in t and not t.startswith("^")]
    spine = max((panel[t].index for t in us), key=len)
    dates = grid_dates(spine)
    print(f"[grid] {len(us)} US tickers · {len(dates)} grid dates "
          f"{dates[0].date()}..{dates[-1].date()}", file=sys.stderr)

    want = [c for c in ML.FEATURE_SETS[feature_set] if c not in ML.MKT + ML.XS]
    rows = []
    for n, t in enumerate(us):
        df = panel[t]
        f = D.compute_features(df)
        x = FX.per_ticker(df, mkt["spy_ret"])
        idx = f.index
        arrs = {c: f[c].to_numpy() for c in want if c in f.columns}
        arrs.update({c: x[c].to_numpy() for c in want if c in x.columns})
        close, mom = f["close"].to_numpy(), f["mom_12_1"].to_numpy()
        code = sec_codes.get(sec_map.get(t, ""), -1)

        for d in dates:
            i = idx.searchsorted(d, side="right") - 1
            if i < MIN_BARS or i >= len(idx):
                continue
            # The primary gate, applied here so the grid holds only tradable candidates.
            if not (np.isfinite(mom[i]) and mom[i] > MOM_THRESHOLD and close[i] >= MIN_PRICE):
                continue
            rec = {"date": d, "ticker": t, "close": close[i], "sector_code": code,
                   "bar": idx[i]}
            for c, a in arrs.items():
                rec[c] = a[i]
            rows.append(rec)
        if n % 300 == 0:
            print(f"[grid] {n}/{len(us)}  rows={len(rows):,}", file=sys.stderr)

    g = pd.DataFrame(rows)
    for c in ML.MKT:
        g[c] = g.date.map(mkt[c].reindex(g.date.unique()).ffill())
    g["xs_dispersion"] = g.groupby("date")["ret63"].transform("std")
    g["xs_breadth"] = g.groupby("date")["ret63"].transform(lambda s: (s > 0).mean())
    g["xs_mom_rank"] = g.groupby("date")["mom_12_1"].rank(pct=True)
    return g


def score(g: pd.DataFrame, train: pd.DataFrame, feature_set=ML.DEFAULT_FEATURES) -> pd.DataFrame:
    """Attach a meta-probability to every grid row, under a cadence-free embargo."""
    feats = [c for c in ML.FEATURE_SETS[feature_set] if c in train.columns and c in g.columns]
    cat = [feats.index("sector_code")] if "sector_code" in feats else []
    obs_dates = sorted(train.date.unique())

    g = g.sort_values("date").copy()
    g["p"] = np.nan
    model_cache: dict[int, list] = {}

    for d, chunk in g.groupby("date", sort=True):
        # Models may only see observations whose forward window closed before d.
        k = int(np.searchsorted(obs_dates, d, side="left")) - ML.EMBARGO_DATES
        if k < ML.MIN_TRAIN_DATES:
            continue
        if k not in model_cache:
            tr = train[train.date.isin(obs_dates[:k])]
            fitted = []
            for s in ML.SEEDS:
                m = HistGradientBoostingClassifier(
                    max_depth=3, max_iter=200, learning_rate=0.05,
                    min_samples_leaf=50, l2_regularization=1.0, random_state=s,
                    categorical_features=cat or None)
                m.fit(tr[feats].to_numpy(), tr.y.to_numpy())
                fitted.append(m)
            model_cache[k] = fitted
            model_cache = {kk: v for kk, v in model_cache.items() if kk >= k - 1}  # keep it small
        pr = np.mean([m.predict_proba(chunk[feats].to_numpy())[:, 1]
                      for m in model_cache[k]], axis=0)
        g.loc[chunk.index, "p"] = pr

    g = g.dropna(subset=["p"])
    g["rank_p"] = g.groupby("date")["p"].rank(ascending=False, method="first")
    g["rank_mom"] = g.groupby("date")["mom_12_1"].rank(ascending=False, method="first")
    return g


def main():
    fs = ML.DEFAULT_FEATURES
    if "--features" in sys.argv:
        fs = sys.argv[sys.argv.index("--features") + 1]
    train = ML.build()
    print(f"[grid] training matrix {len(train):,} rows / {train.date.nunique()} dates",
          file=sys.stderr)
    g = build_grid(fs)
    print(f"[grid] raw grid {len(g):,} rows", file=sys.stderr)
    g = score(g, train, fs)
    g.to_pickle(CACHE / "pred_grid.pkl")
    print(f"[grid] wrote pred_grid.pkl — {len(g):,} scored rows over "
          f"{g.date.nunique()} dates", file=sys.stderr)
    print(g.groupby(g.date.dt.year).size().to_string(), file=sys.stderr)


if __name__ == "__main__":
    main()
