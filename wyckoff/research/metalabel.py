#!/usr/bin/env python3
"""Meta-labelling: given that momentum fired, will THIS one work?

The primary signal is the rule we already validated (`mom_12_1 > 30%`) — no ML involved.
The secondary model sees only the observations where the primary fired and answers one binary
question: did it produce a positive peer-relative excess return?

Why this shape rather than a from-scratch return model: the effective sample is ~100
observation dates, not 45k rows, because returns are cross-sectionally correlated within a
date. A binary target on a filtered subset is a far easier learning problem than predicting
returns, and it yields a probability that can size a position.

Features are deliberately CONTEXT ("is this a good moment for momentum?") rather than stock
quality ("is this a good stock?") — the primary already encodes the latter.

Validation is expanding-window walk-forward with an embargo of at least the forward horizon,
because a 6-month forward return overlaps the next 5 observation dates. Without the embargo
the model trains on the outcome it is being asked to predict.

Usage:  metalabel.py [--ablate]      # --ablate compares feature sets instead of cohorts
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

CACHE = Path(__file__).parent / "cache"
EMBARGO_DATES = 6          # 6 monthly observations ~ the 126-day forward window
MIN_TRAIN_DATES = 18
SEEDS = (0, 1, 2, 3, 4)    # averaged — a single fit reshuffles the order on identical data

BASE = ["dd", "atr_pct", "rsi14", "vol_ratio", "bb_width", "close_pos",
        "ret21", "ret63", "ret126", "mom_12_1", "ma200_slope", "ma50_slope",
        "dist_days", "obv", "rng"]
XS   = ["xs_dispersion", "xs_breadth", "xs_mom_rank"]
LIQ  = ["log_dollar_vol", "log_illiq", "beta", "idio_vol", "vol_trend"]
MKT  = ["spy_dd", "spy_vol20", "spy_vol60", "spy_above_200", "spy_ret63"]
SECT = ["sector_code"]

FEATURE_SETS = {
    "base": BASE + XS,
    "base+liq": BASE + XS + LIQ,
    "base+mkt": BASE + XS + MKT,
    "base+liq+mkt": BASE + XS + LIQ + MKT,
    "base+liq+mkt+sec": BASE + XS + LIQ + MKT + SECT,
}

# Chosen by ablation, not by taste. Liquidity lifted the top decile from +9.77 to +12.55
# (t 6.01 -> 7.02) — the one family the literature named a priori. Market context added
# nothing on top of the cross-sectional features, and SECTOR ACTIVELY HURT: an 11-level
# categorical on 30k rows lets the model memorise sector-era effects it cannot generalise.
DEFAULT_FEATURES = "base+liq"


def build() -> pd.DataFrame:
    panel = pickle.load(open(CACHE / "panel.pkl", "rb"))
    obs = pd.read_pickle(CACHE / "observations.pkl")
    obs = obs[obs.mom_12_1_strong]
    print(f"[meta] primary fired on {len(obs):,} observations", file=sys.stderr)

    mkt = FX.market_context(panel)
    sec_map = FX.sectors()
    sec_codes = {s: i for i, s in enumerate(sorted(set(sec_map.values())))}

    rows = []
    for t, grp in obs.groupby("ticker", sort=False):
        df = panel.get(t)
        if df is None or len(df) < 400:
            continue
        f = D.compute_features(df)
        x = FX.per_ticker(df, mkt["spy_ret"])
        idx = f.index
        arrs = {c: f[c].to_numpy() for c in BASE if c in f.columns}
        arrs.update({c: x[c].to_numpy() for c in LIQ})
        code = sec_codes.get(sec_map.get(t, ""), -1)

        for r in grp.itertuples():
            i = idx.searchsorted(r.date, side="right") - 1
            if i < 260:
                continue
            rec = {"ticker": t, "date": r.date, "x6": r.x6, "region": r.region,
                   "sector_code": code}
            for c, a in arrs.items():
                rec[c] = a[i]
            rows.append(rec)

    p = pd.DataFrame(rows).dropna(subset=["x6"])

    # Market context is one row per date and identical across tickers — map it on rather
    # than recompute it 45,000 times.
    mkt_on_dates = mkt[MKT].reindex(p.date.unique()).ffill()
    for c in MKT:
        p[c] = p.date.map(mkt_on_dates[c])

    # Cross-sectional context: how dispersed, how broad, and where this name sits in the
    # day's momentum ordering (a relative view the absolute mom_12_1 cannot give).
    p["xs_dispersion"] = p.groupby("date")["ret63"].transform("std")
    p["xs_breadth"] = p.groupby("date")["ret63"].transform(lambda s: (s > 0).mean())
    p["xs_mom_rank"] = p.groupby("date")["mom_12_1"].rank(pct=True)
    p["y"] = (p.x6 > 0).astype(int)
    return p


def walk_forward(p: pd.DataFrame, feats: list[str], seeds=SEEDS) -> pd.DataFrame:
    feats = [c for c in feats if c in p.columns]
    cat = [feats.index("sector_code")] if "sector_code" in feats else []
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
        Xtr, ytr, Xte = tr[feats].to_numpy(), tr.y.to_numpy(), te[feats].to_numpy()
        pr = np.zeros(len(te))
        for s in seeds:
            m = HistGradientBoostingClassifier(
                max_depth=3, max_iter=200, learning_rate=0.05,
                min_samples_leaf=50, l2_regularization=1.0, random_state=s,
                categorical_features=cat or None)
            m.fit(Xtr, ytr)
            pr += m.predict_proba(Xte)[:, 1]
        o = te[["ticker", "date", "x6", "y"]].copy()
        o["p"] = pr / len(seeds)
        out.append(o)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def fm(s: pd.DataFrame):
    q = s.groupby("date")["x6"].mean().dropna()
    if len(q) < 8:
        return np.nan, np.nan
    mu = q.mean()
    return mu, mu / (q.std(ddof=1) / np.sqrt(len(q)))


def cohorts(res: pd.DataFrame, label: str) -> pd.DataFrame:
    res = res.copy()
    res["pct"] = res.groupby("date")["p"].rank(pct=True)
    rows = []
    for lab, sub in [("all (baseline)", res), ("top 50%", res[res.pct > 0.50]),
                     ("top 30%", res[res.pct > 0.70]), ("top 10%", res[res.pct > 0.90]),
                     ("bottom 50%", res[res.pct <= 0.50])]:
        if len(sub) < 100:
            continue
        mu, t = fm(sub)
        rows.append({"features": label, "cohort": lab, "n": len(sub), "mean_x6": mu, "t": t,
                     "med": sub.x6.median(), "win%": (sub.x6 > 0).mean() * 100})
    return pd.DataFrame(rows)


def main():
    p = build()
    print(f"[meta] matrix: {len(p):,} rows across {p.date.nunique()} dates", file=sys.stderr)
    pd.set_option("display.width", 200)

    if "--ablate" in sys.argv:
        # Does each feature family earn its place? Judged on the top decile, which is the
        # cohort a 10-name portfolio actually draws from.
        allrows = []
        for label, feats in FEATURE_SETS.items():
            res = walk_forward(p, feats)
            if res.empty:
                continue
            c = cohorts(res, f"{label:16s}")
            allrows.append(c[c.cohort.isin(["all (baseline)", "top 30%", "top 10%"])])
            print(f"[ablate] {label} done", file=sys.stderr)
        print(pd.concat(allrows).to_string(index=False, float_format=lambda v: f"{v:8.2f}"))
        return

    res = walk_forward(p, FEATURE_SETS[DEFAULT_FEATURES])
    res.to_pickle(CACHE / "metalabel_oos.pkl")
    print(f"\nout-of-sample: {len(res):,} predictions over {res.date.nunique()} dates")
    print(f"base rate (primary alone won): {res.y.mean()*100:.1f}%\n")
    print(cohorts(res, "full").to_string(index=False, float_format=lambda v: f"{v:8.2f}"))


if __name__ == "__main__":
    main()
