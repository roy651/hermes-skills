#!/usr/bin/env python3
"""Multi-scale path features: the price history decomposed into non-overlapping eras.

The design point, stated so it is not forgotten: this buys REPRESENTATION, not information. A
252-day path is 252 numbers, and any window-return is a linear projection of them. What this
does is make multi-scale structure *findable* by a model that cannot construct
`ret(20) - ret(10)` for itself.

Two choices follow from that:

  * **Non-overlapping segments, not cumulative windows.** ret(20) and ret(21) share 95% of their
    data and correlate ~0.99; a dozen cumulative windows is a dozen copies of one number. Disjoint
    eras carry the same information with far less collinearity, and each answers a distinct
    question — what happened IN that era, rather than since it.
  * **Log spacing.** Return variance scales with sqrt(t), so equal spacing massively oversamples
    the recent end. The boundaries below roughly double each step.

The first derivative of a cumulative window IS the incremental-window return, so the segments
already are it. The SECOND derivative is genuinely additive — it says the trend is accelerating
or decaying, which no single window expresses — so it is built explicitly.

Usage:  paths.py [--sectors "Industrials,Utilities"] [--start 2016-09-01]
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import features_extra as FX

CACHE = Path(__file__).parent / "cache"
# (from, to) sessions back — disjoint, roughly doubling
SEGMENTS = [(1, 5), (6, 10), (11, 21), (22, 42), (43, 84), (85, 168), (169, 252)]
FWD = 126
MIN_BARS = 300


def path_features(close: np.ndarray, vol: np.ndarray, i: int) -> dict | None:
    """Segment return / volume / volatility at bar i, plus second derivatives."""
    if i < 252:
        return None
    f, rets = {}, []
    yr_vol = np.nanmean(vol[i - 252:i + 1])
    for a, b in SEGMENTS:
        p_end, p_start = close[i - a + 1], close[i - b]
        if not (np.isfinite(p_end) and np.isfinite(p_start) and p_start > 0):
            return None
        r = p_end / p_start - 1
        seg = close[i - b:i - a + 2]
        d = np.diff(seg) / seg[:-1]
        f[f"ret_{a}_{b}"] = r * 100
        f[f"vol_{a}_{b}"] = (np.nanstd(d) * np.sqrt(252) * 100) if len(d) > 2 else np.nan
        vseg = np.nanmean(vol[i - b:i - a + 2])
        f[f"dvol_{a}_{b}"] = (vseg / yr_vol) if yr_vol > 0 else np.nan
        rets.append(r)
    # Second derivative: change in the per-day pace between adjacent eras. A trend that is
    # strengthening reads differently from one of the same size that is fading.
    paces = [r / (b - a + 1) for r, (a, b) in zip(rets, SEGMENTS)]
    for k in range(len(paces) - 1):
        a1, b1 = SEGMENTS[k]
        f[f"accel_{a1}_{b1}"] = (paces[k] - paces[k + 1]) * 10000
    hi252 = np.nanmax(close[i - 251:i + 1])
    f["dd"] = (close[i] / hi252 - 1) * 100 if hi252 > 0 else np.nan
    f["mom_12_1"] = (close[i - 21] / close[i - 252] - 1) * 100 if close[i - 252] > 0 else np.nan
    return f


def build(sectors: list[str] | None, start: str) -> pd.DataFrame:
    panel = pickle.load(open(CACHE / "panel.pkl", "rb"))
    sec = FX.sectors()
    us = [t for t, d in panel.items()
          if d is not None and "." not in t and not t.startswith("^")
          and len(d) > MIN_BARS and t in sec and (not sectors or sec[t] in sectors)]
    print(f"[paths] {len(us)} tickers in {sectors or 'ALL sectors'}", file=sys.stderr)

    spine = max((panel[t].index for t in us), key=len)
    month_ends = pd.Series(spine, index=spine).groupby([spine.year, spine.month]).max()
    dates = [d for d in month_ends if d >= pd.Timestamp(start)]
    print(f"[paths] {len(dates)} monthly dates {dates[0].date()}..{dates[-1].date()}",
          file=sys.stderr)

    rows = []
    for n, t in enumerate(us):
        df = panel[t]
        idx = df.index
        close = df["close"].to_numpy(dtype=float)
        vol = df["volume"].to_numpy(dtype=float)
        for d in dates:
            i = idx.searchsorted(d, side="right") - 1
            if i < 252 or i + FWD >= len(close):
                continue
            f = path_features(close, vol, i)
            if not f:
                continue
            fwd = close[i + FWD] / close[i] - 1
            rows.append({"ticker": t, "date": d, "sector": sec[t], "fwd": fwd * 100, **f})
        if n % 100 == 0:
            print(f"[paths] {n}/{len(us)}  rows={len(rows):,}", file=sys.stderr)

    p = pd.DataFrame(rows)
    # Target: forward return relative to the SECTOR on the same date. Market and sector moves
    # are shared by construction, so what is left is company-specific.
    p["y"] = p.fwd - p.groupby(["date", "sector"]).fwd.transform("mean")

    # Cross-sectional standardisation within (date, sector) — the normalisation that actually
    # matters. It strips market-wide drift and makes every feature a relative statement, which
    # is what a cross-sectional target requires. Ranks, so outliers cannot dominate.
    feats = [c for c in p.columns if c not in ("ticker", "date", "sector", "fwd", "y")]
    for c in feats:
        p[c + "_z"] = p.groupby(["date", "sector"])[c].transform(
            lambda s: (s.rank(pct=True) - 0.5) * 2 if s.notna().sum() >= 8 else np.nan)
    p.to_pickle(CACHE / "path_features.pkl")
    print(f"[paths] {len(p):,} rows · {p.ticker.nunique()} tickers · {len(feats)} raw features "
          f"(+{len(feats)} standardised)", file=sys.stderr)
    print(f"[paths] target sd within sector: {p.y.std():.2f}", file=sys.stderr)
    return p


if __name__ == "__main__":
    a = lambda f, d: sys.argv[sys.argv.index(f) + 1] if f in sys.argv else d
    secs = a("--sectors", "Industrials,Consumer Discretionary,Utilities")
    build([s.strip() for s in secs.split(",")] if secs != "ALL" else None, a("--start", "2016-09-01"))
