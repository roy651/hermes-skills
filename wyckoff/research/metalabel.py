#!/usr/bin/env python3
"""Meta-labelling: given that momentum fired, will THIS one work?

The primary signal is the rule we already validated (`mom_12_1 > 30%`) — no ML involved.
The secondary model sees only the observations where the primary fired and answers one binary
question: did it produce a positive peer-relative excess return?

Why this shape rather than a from-scratch return model: the effective sample is ~61
observation dates, not 31k rows, because returns are cross-sectionally correlated within a
date. A binary target on a filtered subset is a far easier learning problem than predicting
returns, and it yields a probability that can size a position.

Features are deliberately CONTEXT ("is this a good moment for momentum?") rather than stock
quality ("is this a good stock?") — the primary already encodes the latter.

Validation is expanding-window walk-forward with an embargo of at least the forward horizon,
because a 6-month forward return overlaps the next 5 observation dates. Without the embargo
the model trains on the outcome it is being asked to predict.
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import detectors as D

CACHE = Path(__file__).parent / "cache"
EMBARGO_DATES = 6          # 6 monthly observations ~ the 126-day forward window
MIN_TRAIN_DATES = 18

FEATURES = ["dd", "atr_pct", "rsi14", "vol_ratio", "bb_width", "close_pos",
            "ret21", "ret63", "ret126", "mom_12_1", "ma200_slope", "ma50_slope",
            "dist_days", "obv", "rng"]


def build() -> pd.DataFrame:
    panel = pickle.load(open(CACHE / "panel.pkl", "rb"))
    obs = pd.read_pickle(CACHE / "observations.pkl")
    obs = obs[obs.mom_12_1_strong]                       # primary fired
    print(f"[meta] primary fired on {len(obs):,} observations", file=sys.stderr)

    rows = []
    for t, grp in obs.groupby("ticker", sort=False):
        df = panel.get(t)
        if df is None or len(df) < 400:
            continue
        f = D.compute_features(df)
        idx = f.index
        arrs = {c: f[c].to_numpy() for c in FEATURES if c in f.columns}
        for r in grp.itertuples():
            i = idx.searchsorted(r.date, side="right") - 1
            if i < 260:
                continue
            rec = {"ticker": t, "date": r.date, "x6": r.x6, "region": r.region}
            for c, a in arrs.items():
                rec[c] = a[i]
            rows.append(rec)
    p = pd.DataFrame(rows).dropna(subset=["x6"])

    # Market-wide context: how dispersed and how strong is the cross-section that day?
    p["xs_dispersion"] = p.groupby("date")["ret63"].transform("std")
    p["xs_breadth"] = p.groupby("date")["ret63"].transform(lambda s: (s > 0).mean())
    p["y"] = (p.x6 > 0).astype(int)
    return p


def walk_forward(p: pd.DataFrame) -> pd.DataFrame:
    feats = [c for c in FEATURES if c in p.columns] + ["xs_dispersion", "xs_breadth"]
    dates = sorted(p.date.unique())
    out = []
    for k in range(MIN_TRAIN_DATES, len(dates)):
        test_d = dates[k]
        train_d = dates[:max(0, k - EMBARGO_DATES)]      # embargo: drop the overlapping window
        if len(train_d) < MIN_TRAIN_DATES:
            continue
        tr = p[p.date.isin(train_d)]
        te = p[p.date == test_d]
        if len(tr) < 500 or len(te) < 20:
            continue
        m = HistGradientBoostingClassifier(
            max_depth=3, max_iter=200, learning_rate=0.05,
            min_samples_leaf=50, l2_regularization=1.0, random_state=0)
        m.fit(tr[feats].to_numpy(), tr.y.to_numpy())
        pr = m.predict_proba(te[feats].to_numpy())[:, 1]
        o = te[["ticker", "date", "x6", "y"]].copy()
        o["p"] = pr
        out.append(o)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def main():
    p = build()
    print(f"[meta] feature matrix: {len(p):,} rows x {len(FEATURES)+2} features "
          f"across {p.date.nunique()} dates", file=sys.stderr)
    res = walk_forward(p)
    if res.empty:
        print("no out-of-sample predictions produced", file=sys.stderr)
        return
    res.to_pickle(CACHE / "metalabel_oos.pkl")

    def fm(s):
        q = s.groupby("date")["x6"].mean().dropna()
        if len(q) < 8:
            return np.nan, np.nan
        mu = q.mean(); se = q.std(ddof=1) / np.sqrt(len(q))
        return mu, mu / se

    # Rank within each date so the filter is a same-day decision, not a global threshold.
    res["pct"] = res.groupby("date")["p"].rank(pct=True)
    print(f"\nout-of-sample: {len(res):,} predictions over {res.date.nunique()} dates")
    print(f"base rate (primary alone won): {res.y.mean()*100:.1f}%\n")

    rows = []
    for lab, sub in [("ALL primary signals (baseline)", res),
                     ("meta top 50%", res[res.pct > 0.50]),
                     ("meta top 30%", res[res.pct > 0.70]),
                     ("meta top 10%", res[res.pct > 0.90]),
                     ("meta bottom 50%", res[res.pct <= 0.50]),
                     ("meta bottom 10%", res[res.pct <= 0.10])]:
        if len(sub) < 100:
            continue
        mu, t = fm(sub)
        rows.append({"cohort": lab, "n": len(sub), "mean_x6": mu, "t": t,
                     "med_x6": sub.x6.median(), "win%": (sub.x6 > 0).mean() * 100})
    pd.set_option("display.width", 190)
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda v: f"{v:8.2f}"))


if __name__ == "__main__":
    main()
