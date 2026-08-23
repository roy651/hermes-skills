#!/usr/bin/env python3
"""Evaluate the detector bank on a global panel.

Two deliberate improvements on yesterday's method:

1. CROSS-SECTIONAL BENCHMARK. Each observation is measured against the equal-weighted mean
   forward return of every other stock in the same region on the same date. Yesterday I
   benchmarked to IWM, which made every bucket negative simply because the median stock
   lags a cap-weighted index — an artifact that obscured the real differences. Against a
   cross-sectional mean, zero means "no better than a coin toss among peers", which is the
   number we actually care about.

2. HOLDOUT. 2021-2024 is the working sample; 2025-2026 is held back. With ~33 detectors
   under test, some will look good by chance; surviving an untouched period is the check.

Output: one row per (ticker, date) with every detector's flag and forward excess returns.
"""
import pickle
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import detectors as D

# Cache lives beside this file and is gitignored — it holds ~1GB of prices.
CACHE = Path(__file__).parent / "cache"
LAB = CACHE
H3, H6 = 63, 126
# Widened from 2021 to the full panel depth: ~119 monthly dates instead of 61. The 2021+
# subset of this file reproduces the earlier study exactly (peer means are within-date),
# so the two windows stay comparable from one artifact.
START, END = "2016-09-01", "2026-02-01"


def region_of(ticker: str) -> str:
    if "." not in ticker:
        return "US"
    return ticker.rsplit(".", 1)[-1]


def main():
    panel = pickle.load(open(LAB / "panel.pkl", "rb"))
    tickers = [t for t, df in panel.items()
               if df is not None and len(df) > 600 and not t.startswith("^")]
    print(f"[harness] {len(tickers)} tickers with sufficient history", file=sys.stderr)

    # Common date spine from the most complete US series.
    spine = max((panel[t].index for t in tickers if region_of(t) == "US"), key=len)
    month_ends = pd.Series(spine).groupby([spine.year, spine.month]).max().values
    dates = [d for d in pd.to_datetime(month_ends)
             if pd.Timestamp(START) <= d <= pd.Timestamp(END)]
    print(f"[harness] {len(dates)} observation dates {dates[0].date()}..{dates[-1].date()}",
          file=sys.stderr)

    names = list(D.ALL)
    rows = []
    for n, t in enumerate(tickers):
        df = panel[t]
        try:
            feat_df = D.compute_features(df)
        except Exception as e:
            print(f"[harness] {t} features failed: {str(e)[:60]}", file=sys.stderr)
            continue
        # Detectors index numpy arrays directly — pandas element access is ~50x slower
        # and this loop runs several million times.
        f = SimpleNamespace(**{c: feat_df[c].to_numpy(dtype="float64")
                               for c in feat_df.columns})
        idx = feat_df.index
        close = f.close
        dd_arr = f.dd

        for d in dates:
            # last bar on or before the observation date
            i = idx.searchsorted(d, side="right") - 1
            if i < 260 or i + H6 >= len(idx):
                continue
            p0 = close[i]
            if not np.isfinite(p0) or p0 <= 0:
                continue
            rec = {"ticker": t, "region": region_of(t), "date": d, "dd": dd_arr[i],
                   "r3": close[i + H3] / p0 - 1,
                   "r6": close[i + H6] / p0 - 1}
            for nm in names:
                try:
                    rec[nm] = bool(D.ALL[nm][1](f, i))
                except Exception:
                    rec[nm] = False
            rows.append(rec)
        if n % 250 == 0:
            print(f"[harness] {n}/{len(tickers)}  rows={len(rows):,}", file=sys.stderr)

    p = pd.DataFrame(rows)
    print(f"[harness] raw observations: {len(p):,}", file=sys.stderr)

    # Cross-sectional benchmark: equal-weighted peer mean, same region and date.
    for horizon in ("r3", "r6"):
        peer = p.groupby(["region", "date"])[horizon].transform("mean")
        p[f"x{horizon[1:]}"] = (p[horizon] - peer) * 100
    p["r3"] *= 100
    p["r6"] *= 100
    p["dd"] *= 100
    p["year"] = p["date"].dt.year

    p.to_pickle(LAB / "observations.pkl")
    print(f"[harness] wrote {LAB/'observations.pkl'}  ({len(p):,} rows)", file=sys.stderr)
    print(p.groupby("region").size().sort_values(ascending=False).to_string(), file=sys.stderr)


if __name__ == "__main__":
    main()
