#!/usr/bin/env python3
"""Test detector COMBINATIONS — selectively, with a stated reason for each family.

Judgment on when a combination is worth testing at all:

  WORTH IT
  * Filter x Trigger — one detector establishes context (is this a healthy uptrend?), the
    other times the entry (is now a good moment?). They answer different questions, so the
    evidence compounds instead of repeating.
  * Independent information sources — price structure x volume x volatility. Conditionally
    independent readings of the same hypothesis.
  * Attack AND NOT Defense — the strongest effects in this study are the bearish ones, so
    using them as a veto has a high prior.

  NOT WORTH IT
  * Two detectors measuring the same thing (golden_cross x above_rising_200,
    donchian_20 x donchian_55). Redundant: shrinks n, adds no information.
  * Anything that drops n below ~150 — no power, and small samples produce the biggest
    and most seductive fake numbers.
  * Exhaustive pairwise search. 33 detectors = 528 pairs; at p<0.05 that manufactures ~26
    false positives with no economic reasoning behind any of them.

A combination is only interesting if it beats BOTH parents. That is reported explicitly.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Cache lives beside this file and is gitignored — it holds ~1GB of prices.
CACHE = Path(__file__).parent / "cache"
LAB = CACHE
HOLDOUT_FROM = pd.Timestamp("2025-01-01")

TRIGGERS = ["three_day_pullback", "pullback_ema20", "rsi2_oversold_uptrend", "nr7",
            "accumulation_day", "donchian_20", "inside_bar_uptrend", "vcp",
            "wyk_markup_pullback", "wyk_sos", "wyk_lps", "pocket_pivot",
            "volume_dryup_near_high", "obv_new_high"]
FILTERS = ["mom_12_1_strong", "minervini_template", "above_rising_200"]
VETOES = ["breakdown_50day_low", "below_falling_200", "distribution_cluster"]


def fm(sub, col="x6"):
    per_date = sub.groupby("date")[col].mean().dropna()
    if len(per_date) < 8:
        return np.nan, np.nan
    m = per_date.mean()
    se = per_date.std(ddof=1) / np.sqrt(len(per_date))
    return m, (m / se if se > 0 else np.nan)


def stat(p, mask, label, parents):
    sub = p[mask]
    if len(sub) < 150:
        return None
    m, t = fm(sub)
    ins, out = sub[sub.date < HOLDOUT_FROM], sub[sub.date >= HOLDOUT_FROM]
    m_in, _ = fm(ins)
    m_out, t_out = fm(out)
    yearly = sub.groupby("year")["x6"].mean()
    best_parent = max(parents, key=lambda x: x if not np.isnan(x) else -99)
    return {"combo": label, "n": len(sub), "fire%": len(sub) / len(p) * 100,
            "x6": m, "t6": t, "in": m_in, "out": m_out, "t_out": t_out,
            "win6": (sub.x6 > 0).mean() * 100,
            "best_parent": best_parent, "lift": m - best_parent,
            "yrs+": f"{int((yearly>0).sum())}/{len(yearly)}"}


def main():
    p = pd.read_pickle(LAB / "observations.pkl")
    solo = {}
    for c in set(TRIGGERS + FILTERS + VETOES):
        if c in p.columns and p[c].sum() >= 150:
            solo[c] = fm(p[p[c]])[0]

    pd.set_option("display.width", 230)
    fmt = lambda v: f"{v:7.2f}"
    tests = 0
    out_rows = {}

    # --- Family A: filter x trigger -------------------------------------------------
    rows = []
    for flt in FILTERS:
        if flt not in p.columns:
            continue
        for trg in TRIGGERS:
            if trg not in p.columns or trg == flt:
                continue
            r = stat(p, p[flt] & p[trg], f"{flt} + {trg}",
                     [solo.get(flt, np.nan), solo.get(trg, np.nan)])
            if r:
                rows.append(r)
                tests += 1
    out_rows["A. FILTER x TRIGGER — context plus timing"] = rows

    # --- Family B: attack AND NOT defense -------------------------------------------
    rows = []
    for atk in TRIGGERS + FILTERS:
        if atk not in p.columns:
            continue
        for veto in VETOES:
            if veto not in p.columns:
                continue
            r = stat(p, p[atk] & ~p[veto], f"{atk} + NOT {veto}",
                     [solo.get(atk, np.nan)])
            if r:
                rows.append(r)
                tests += 1
    out_rows["B. ATTACK with DEFENSE VETO — same signal, disqualifying condition removed"] = rows

    # --- Family C: volume confirmation of price structure ---------------------------
    rows = []
    for price_sig in ["donchian_20", "new_52w_high", "three_day_pullback",
                      "pullback_ema20", "nr7", "wyk_markup_pullback", "vcp"]:
        for vol_sig in ["accumulation_day", "obv_new_high", "volume_dryup_near_high"]:
            if price_sig not in p.columns or vol_sig not in p.columns:
                continue
            r = stat(p, p[price_sig] & p[vol_sig], f"{price_sig} + {vol_sig}",
                     [solo.get(price_sig, np.nan), solo.get(vol_sig, np.nan)])
            if r:
                rows.append(r)
                tests += 1
    out_rows["C. PRICE x VOLUME — independent confirmation"] = rows

    # --- Family D: sharpening the exit signal ---------------------------------------
    rows = []
    for a in VETOES:
        for b in VETOES:
            if a >= b or a not in p.columns or b not in p.columns:
                continue
            r = stat(p, p[a] & p[b], f"{a} + {b}", [solo.get(a, np.nan), solo.get(b, np.nan)])
            if r:
                rows.append(r)
                tests += 1
    for a in VETOES:
        r = stat(p, p[a] & ~p["mom_12_1_strong"], f"{a} + NOT mom_12_1_strong",
                 [solo.get(a, np.nan)])
        if r:
            rows.append(r)
            tests += 1
    out_rows["D. DEFENSE STACKING — want MORE negative than either parent"] = rows

    for title, rows in out_rows.items():
        if not rows:
            continue
        df = pd.DataFrame(rows)
        ascending = title.startswith("D")
        df = df.sort_values("x6", ascending=ascending).head(14)
        print("\n" + "=" * 140)
        print(title)
        print("=" * 140)
        print(df[["combo", "n", "fire%", "x6", "t6", "in", "out", "t_out",
                  "win6", "best_parent", "lift", "yrs+"]]
              .to_string(index=False, float_format=fmt))

    print("\n\n" + "=" * 140)
    print(f"COMBINATIONS THAT BEAT BOTH PARENTS, survive the holdout, and are consistent")
    print("=" * 140)
    allr = pd.DataFrame([r for rows in out_rows.values() for r in rows])
    good = allr[(allr.lift > 0.5) & (allr["in"] > 0) & (allr["out"] > 0)
                & (allr.n >= 250)].sort_values("out", ascending=False)
    print(good[["combo", "n", "fire%", "x6", "t6", "in", "out", "t_out", "lift", "yrs+"]]
          .to_string(index=False, float_format=fmt))
    print(f"\n{tests} combinations tested. {len(good)} survived all four filters.")
    print("At p<0.05 pure chance would yield roughly "
          f"{tests*0.05:.0f} spurious 'significant' results — treat the holdout column, "
          "not t6, as the evidence.")
    allr.to_csv(LAB / "combination_scores.csv", index=False)


if __name__ == "__main__":
    main()
