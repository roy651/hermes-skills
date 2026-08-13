#!/usr/bin/env python3
"""Validate the exit engine — the half of the system that has never been measured.

`risk -> deterioration -> ladder` issues real trim and exit instructions, and none of it
has evidence behind it. Three studies, no LLM:

  1. DETERIORATION SCORE — does a high 0-9 score actually predict weakness? Cross-sectional,
     same method as the entry work with the sign flipped. A working score shows a monotonic
     gradient: 7/9 names should underperform 2/9 names.

  2. TRAILING STOP — does it help or hurt? This one cannot be answered with endpoint returns
     because a stop is PATH-dependent, so each position is simulated day by day twice: held
     outright, and held with the engine's actual stop rule. The honest question is whether it
     truncates more loss than gain.

  3. CONTRADICTION AUDIT — deterioration and events read the same bars and disagreed twice in
     one digest (TEVA, DD). Measure how often, and who is right when they do.

Usage:  python research/exits.py [--sample N]
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import deterioration as det
import events as wyk

CACHE = Path(__file__).parent / "cache"
H6 = 126                     # ~6 months of sessions
LOOKBACK = 120               # bars handed to the detectors, matching production
ATR_N, CHAND_MULT, STRUCT_LB, STRUCT_BUF = 14, 3.0, 20, 0.25


def atr_series(high, low, close, n=ATR_N):
    prev = np.concatenate([[np.nan], close[:-1]])
    tr = np.nanmax(np.vstack([high - low, np.abs(high - prev), np.abs(low - prev)]), axis=0)
    out = pd.Series(tr).rolling(n).mean().to_numpy()
    return out


def simulate_stop(high, low, close, atr, t, horizon=H6):
    """Walk the engine's real stop rule forward from entry at bar t.

    stop = max(chandelier, structure), i.e. whichever is TIGHTER:
      chandelier = highest_high_since_entry - 3 * ATR
      structure  = min(low of the prior <=20 OWNED sessions, excluding today) - 0.25 * ATR
    Exit on the first CLOSE below the stop — the digest treats close-through as the hard exit.

    Returns (exit_return, bars_held, stopped_out).
    """
    entry = close[t]
    end = min(t + horizon, len(close) - 1)
    highest = high[t]
    owned_min = np.inf
    rolling20 = pd.Series(low).rolling(STRUCT_LB).min().to_numpy()

    for i in range(t + 1, end + 1):
        highest = max(highest, high[i])
        owned_min = min(owned_min, low[i - 1])
        a = atr[i]
        if not np.isfinite(a):
            continue
        chand = highest - CHAND_MULT * a
        # prior owned sessions only; once 20 owned bars exist the plain rolling min is correct
        base = rolling20[i - 1] if (i - 1 - t) >= STRUCT_LB else owned_min
        struct = base - STRUCT_BUF * a if np.isfinite(base) else -np.inf
        stop = max(chand, struct)
        if close[i] < stop:
            return close[i] / entry - 1, i - t, True
    return close[end] / entry - 1, end - t, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0, help="limit tickers (0 = all)")
    args = ap.parse_args()

    panel = pickle.load(open(CACHE / "panel.pkl", "rb"))
    obs = pd.read_pickle(CACHE / "observations.pkl")
    dates = sorted(obs["date"].unique())

    spy = panel["SPY"]["close"]
    spy_idx = spy.index

    def bench_at(d, offset_bars):
        """SPY close `offset_bars` sessions after observation date d."""
        j = spy_idx.searchsorted(d, side="right") - 1 + offset_bars
        return float(spy.iloc[j]) if 0 <= j < len(spy) else None

    tickers = [t for t, df in panel.items()
               if df is not None and len(df) > 600 and not t.startswith("^")]
    if args.sample:
        tickers = tickers[:args.sample]
    print(f"[exits] {len(tickers)} tickers x {len(dates)} dates", file=sys.stderr)

    rows = []
    for n, t in enumerate(tickers):
        df = panel[t]
        idx = df.index
        high, low, close = (df[c].to_numpy(dtype=float) for c in ("high", "low", "close"))
        atr = atr_series(high, low, close)

        for d in dates:
            i = idx.searchsorted(d, side="right") - 1
            if i < 260 or i + H6 >= len(close) or not np.isfinite(close[i]) or close[i] <= 0:
                continue
            window = df.iloc[i - LOOKBACK + 1:i + 1]
            try:
                ds = det.deterioration_score(window)
                ev = wyk.detect_events(window)
            except Exception:
                continue

            hold_ret = close[min(i + H6, len(close) - 1)] / close[i] - 1
            stop_ret, bars, stopped = simulate_stop(high, low, close, atr, i)

            # Being stopped does not put you in cash — you redeploy. Crediting the remaining
            # horizon at the benchmark is the honest comparison; without it the stop is
            # penalised for every day it is correctly out of a falling name.
            redeploy = 0.0
            if stopped and bars < H6:
                b0, b1 = bench_at(d, bars), bench_at(d, H6)
                if b0 and b1:
                    redeploy = b1 / b0 - 1
            stop_rd = (1 + stop_ret) * (1 + redeploy) - 1

            rows.append({
                "ticker": t, "date": d,
                "score": int(ds["score"]),
                "has_structural": bool(ds["has_structural"]),
                "established_markdown": bool(ds["established_markdown"]),
                "wyk_entry": bool(wyk.has_entry_event(ev)),
                "wyk_sos": bool(ev.get("sos")), "wyk_spring": bool(ev.get("spring")),
                "hold_r": hold_ret * 100,
                "stop_r": stop_ret * 100,
                "stop_rd": stop_rd * 100,      # stop + redeploy into the benchmark
                "stopped": stopped, "bars_held": bars,
            })
        if n % 250 == 0:
            print(f"[exits] {n}/{len(tickers)}  rows={len(rows):,}", file=sys.stderr)

    r = pd.DataFrame(rows)
    # Peer-relative, same as the entry work: zero = no better than a coin toss among peers.
    r["region"] = r.ticker.map(lambda x: "US" if "." not in x else x.rsplit(".", 1)[-1])
    peer = r.groupby(["region", "date"])["hold_r"].transform("mean")
    r["hold_x"] = r["hold_r"] - peer
    r.to_pickle(CACHE / "exit_study.pkl")
    print(f"[exits] {len(r):,} observations\n", file=sys.stderr)

    pd.set_option("display.width", 200)
    f = lambda v: f"{v:8.2f}"

    # ---- 1. does the score predict weakness? -------------------------------------
    print("=" * 94)
    print("STUDY 1 — DETERIORATION SCORE vs FORWARD 6m EXCESS RETURN (peer-relative, %)")
    print("a working score should decline monotonically as score rises")
    print("=" * 94)
    g = r.groupby("score").agg(n=("hold_x", "size"), mean_x=("hold_x", "mean"),
                               med_x=("hold_x", "median"),
                               win=("hold_x", lambda s: (s > 0).mean() * 100))
    print(g[g.n >= 100].to_string(float_format=f))

    # ---- 2. stop vs hold ---------------------------------------------------------
    print("\n" + "=" * 94)
    print("STUDY 2 — TRAILING STOP vs HOLD (same positions, raw 6m return %)")
    print("=" * 94)
    out = []
    for label, sub in [("ALL", r), ("score 0-2", r[r.score <= 2]),
                       ("score 3-4", r[r.score.between(3, 4)]),
                       ("score 5+", r[r.score >= 5])]:
        if len(sub) < 200:
            continue
        out.append({
            "bucket": label, "n": len(sub),
            "hold_mean": sub.hold_r.mean(), "stop_mean": sub.stop_r.mean(),
            "stop+redeploy": sub.stop_rd.mean(),
            "delta_rd": sub.stop_rd.mean() - sub.hold_r.mean(),
            "hold_med": sub.hold_r.median(), "stop_med": sub.stop_r.median(),
            "hold_p5": sub.hold_r.quantile(.05), "stop_p5": sub.stop_r.quantile(.05),
            "hold_p95": sub.hold_r.quantile(.95), "stop_p95": sub.stop_r.quantile(.95),
            "stopped_%": sub.stopped.mean() * 100,
        })
    print(pd.DataFrame(out).to_string(index=False, float_format=f))

    # ---- 3. contradiction audit --------------------------------------------------
    print("\n" + "=" * 94)
    print("STUDY 3 — WHEN deterioration AND events DISAGREE, WHO IS RIGHT?")
    print("=" * 94)
    bear = r.score >= 5
    bull = r.wyk_entry
    cases = [("agree: calm (score<5, no entry event)", ~bear & ~bull),
             ("BULL only (entry event, score<5)", ~bear & bull),
             ("BEAR only (score>=5, no entry event)", bear & ~bull),
             ("CONTRADICT (score>=5 AND entry event)", bear & bull)]
    out2 = []
    for label, m in cases:
        sub = r[m]
        if len(sub) < 100:
            continue
        out2.append({"case": label, "n": len(sub), "share_%": len(sub) / len(r) * 100,
                     "mean_x": sub.hold_x.mean(), "med_x": sub.hold_x.median(),
                     "win_%": (sub.hold_x > 0).mean() * 100})
    print(pd.DataFrame(out2).to_string(index=False, float_format=f))


if __name__ == "__main__":
    main()
