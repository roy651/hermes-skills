#!/usr/bin/env python3
"""Is the feature->outcome relationship regime-dependent, and is that ACTIONABLE?

Track 2 found the model significantly inverted (pooled t=-2.21). The hypothesis is that the
relationship is not absent but non-stationary — it changes sign. That is only useful if the sign
is knowable in advance, so this tests three things in increasing order of difficulty:

  1. PERSISTENCE — does the per-date out-of-sample rank correlation come in RUNS, or is it white
     noise? If white noise, there is no regime to exploit and the idea dies here for free.
  2. PREDICTABILITY — do contemporaneously observable variables (market drawdown, volatility,
     breadth, cross-sectional dispersion) explain the sign? Detecting a flip after the fact is
     worthless; only a same-time observable is tradable.
  3. CONDITIONING — if 1 and 2 hold, does adding regime features to the model repair it?

Usage:  regime.py
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import HistGradientBoostingClassifier

sys.path.insert(0, str(Path(__file__).parent))
import features_extra as FX

CACHE = Path(__file__).parent / "cache"
MIN_TRAIN, EMBARGO = 24, 6


def oos_by_date(p: pd.DataFrame, feats: list[str]) -> pd.Series:
    dates = sorted(p.date.unique())
    rows = {}
    for k in range(MIN_TRAIN, len(dates)):
        d = dates[k]
        tr_d = dates[:max(0, k - EMBARGO)]
        if len(tr_d) < MIN_TRAIN:
            continue
        tr, te = p[p.date.isin(tr_d)], p[p.date == d]
        if len(tr) < 800 or len(te) < 20:
            continue
        m = HistGradientBoostingClassifier(max_depth=3, max_iter=200, learning_rate=0.05,
                                           min_samples_leaf=50, l2_regularization=1.0,
                                           random_state=0).fit(tr[feats], tr.up)
        pr = m.predict_proba(te[feats])[:, 1]
        rows[d] = stats.spearmanr(pr, te.y)[0]
    return pd.Series(rows).dropna().sort_index()


def main():
    p = pd.read_pickle(CACHE / "path_features.pkl")
    feats = sorted(c for c in p.columns if c.endswith("_z"))
    p = p.dropna(subset=feats + ["y"]).copy()
    p["up"] = (p.y > 0).astype(int)
    s = oos_by_date(p, feats)
    s.to_pickle(CACHE / "regime_series.pkl")

    print(f"\n=== 1. PERSISTENCE — {len(s)} out-of-sample dates ===")
    print(f"  mean rank-corr {s.mean():+.4f}   sd {s.std():.4f}   "
          f"positive on {100*(s>0).mean():.0f}% of dates")
    for lag in (1, 2, 3):
        a = s.autocorr(lag)
        print(f"  autocorrelation lag {lag}: {a:+.3f}")
    # Runs test: white noise would flip sign about half the time between adjacent dates.
    sign = np.sign(s.values)
    flips = (sign[1:] != sign[:-1]).mean()
    n = len(sign)
    se = np.sqrt(0.25 / (n - 1))
    z = (flips - 0.5) / se
    print(f"  sign flips between adjacent dates: {flips*100:.0f}%  (white noise = 50%, z={z:+.2f})")
    print(f"  longest run of one sign: {max(len(list(g)) for _, g in __import__('itertools').groupby(sign))}")

    print(f"\n=== 2. PREDICTABILITY — does an observable explain the sign? ===")
    panel = pickle.load(open(CACHE / "panel.pkl", "rb"))
    mkt = FX.market_context(panel)
    disp = p.groupby("date").y.std().rename("xs_dispersion")
    X = mkt.reindex(s.index).ffill()[["spy_dd", "spy_vol20", "spy_vol60",
                                      "spy_above_200", "spy_ret63"]]
    X = X.join(disp)
    ok = X.notna().all(axis=1)
    X, y = X[ok], s[ok]
    print(f"  {len(y)} dates with complete regime data")
    for c in X.columns:
        r, pv = stats.spearmanr(X[c], y)
        flag = "  <-- explains sign" if pv < 0.05 else ""
        print(f"    {c:<16} corr with rank-corr {r:+.3f}  p={pv:.3f}{flag}")
    # Does last month's OOS correlation predict this month's? The only free lunch, if it exists.
    lag1 = y.shift(1).dropna()
    r, pv = stats.spearmanr(lag1, y.loc[lag1.index])
    print(f"    {'PRIOR-MONTH corr':<16} corr with rank-corr {r:+.3f}  p={pv:.3f}"
          f"{'  <-- persistence is tradable' if pv < 0.05 else '  <-- no free lunch'}")


if __name__ == "__main__":
    main()
