#!/usr/bin/env python3
"""The gate between the lab and the line.

A detector may only reach production by passing every check here. The criteria are written
down rather than held in someone's head, because the 2026-08-07 session produced five
"significant" findings and three of them were data artifacts. The judgement that caught them
was luck plus suspicion; this file is the attempt to make it mechanical.

    python research/promote.py            # score everything, write promoted.json
    python research/promote.py --explain  # also show why each rejection failed

Output `cache/promoted.json` is what `scripts/scan.py` reads. Nothing else is tradeable.
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import detectors as D

CACHE = Path(__file__).parent / "cache"
HOLDOUT_FROM = pd.Timestamp("2025-01-01")

# --- the bar -----------------------------------------------------------------------
MIN_N = 250              # below this, small-sample noise dominates
MIN_T = 2.0              # Fama-MacBeth |t| in at least one period
MIN_YEARS_POSITIVE = 4   # of 6
# Sign must agree between the working sample and the untouched holdout.


def fama_macbeth(sub: pd.DataFrame, col: str = "x6") -> tuple[float, float]:
    per_date = sub.groupby("date")[col].mean().dropna()
    if len(per_date) < 8:
        return float("nan"), float("nan")
    m = per_date.mean()
    se = per_date.std(ddof=1) / np.sqrt(len(per_date))
    return m, (m / se if se > 0 else float("nan"))


# --- the lint that would have caught the 2026-08-07 bug ----------------------------

# A close outside its own bar is impossible, so any occurrence is bad data. But isolated bad
# prints are normal on foreign exchanges, while the 2026-08 bug corrupted 68-86% of a series.
# These thresholds separate "a few bad ticks" from "the scale is wrong".
BROKEN_TICKER_PCT = 0.05    # above this, that ticker's series is unusable — exclude it
SYSTEMIC_TICKER_PCT = 0.10  # if this share of ALL tickers is affected, the code is wrong


def check_panel_integrity(panel: dict) -> tuple[list[str], list[str], bool]:
    """Returns (broken_tickers, notes, systemic).

    The August bug was a close taken from Yahoo's dividend-adjusted series while high/low
    stayed raw, so on a dividend payer the close sat below the bar's own low — 86% of PG's
    bars. Every detector comparing close to high or low silently became a dividend-yield sort.
    Nothing downstream can detect this; only the data can, so it is checked here and first.

    A handful of impossible bars is exchange noise: report, exclude the ticker, continue.
    A broad pattern means the adjustment code is wrong again: stop everything."""
    broken, noisy, checked = [], 0, 0
    for ticker, df in panel.items():
        if df is None or len(df) < 50:
            continue
        checked += 1
        bad = ((df["close"] < df["low"] - 1e-6) | (df["close"] > df["high"] + 1e-6)).mean()
        if bad > BROKEN_TICKER_PCT:
            broken.append(f"{ticker}: {bad*100:.1f}% of bars impossible")
        elif bad > 0:
            noisy += 1

    if checked == 0:
        return [], ["no tickers had enough history to check"], True

    affected = (len(broken) + noisy) / checked
    systemic = affected > SYSTEMIC_TICKER_PCT
    notes = [f"{checked} tickers checked · {len(broken)} broken · {noisy} with isolated bad "
             f"bars · {affected*100:.1f}% affected overall"]
    return broken, notes, systemic


def evaluate(p: pd.DataFrame, name: str) -> dict | None:
    fired = p[p[name]]
    if len(fired) < 50:
        return None
    ins, out = fired[fired.date < HOLDOUT_FROM], fired[fired.date >= HOLDOUT_FROM]
    m_all, t_all = fama_macbeth(fired)
    m_in, t_in = fama_macbeth(ins)
    m_out, t_out = fama_macbeth(out)
    yearly = fired.groupby("year")["x6"].mean()

    # Regime split decides WHEN a promoted detector is allowed to fire, not whether.
    on = fired[fired.risk_on] if "risk_on" in fired else fired.iloc[:0]
    off = fired[~fired.risk_on] if "risk_on" in fired else fired.iloc[:0]
    m_on = fama_macbeth(on)[0] if len(on) >= 100 else float("nan")
    m_off = fama_macbeth(off)[0] if len(off) >= 100 else float("nan")

    # "Useful" means positive for an attack detector and negative for a defence one, so the
    # regime label has to be sign-aware or it reads exactly backwards.
    group = D.ALL[name][0] if name in D.ALL else "WYCKOFF"
    sign = -1 if group == "DEFENSE" else 1
    good_on, good_off = sign * m_on, sign * m_off

    if np.isnan(m_on) or np.isnan(m_off):
        regime = "unknown"
    elif good_on > 0 and good_off > 0:
        regime = "both"
    elif good_on > 0:
        regime = "risk_on_only"
    elif good_off > 0:
        regime = "risk_off_only"
    else:
        regime = "neither"

    return {
        "detector": name, "group": group,
        "n": int(len(fired)), "fire_pct": round(len(fired) / len(p) * 100, 2),
        "x6": round(m_all, 3), "t6": round(t_all, 2),
        "in_sample": round(m_in, 3), "holdout": round(m_out, 3), "t_holdout": round(t_out, 2),
        "years_positive": int((yearly > 0).sum()), "years": int(len(yearly)),
        "regime": regime,
        "x6_risk_on": None if np.isnan(m_on) else round(m_on, 3),
        "x6_risk_off": None if np.isnan(m_off) else round(m_off, 3),
    }


def verdict(r: dict) -> tuple[bool, list[str]]:
    """Pass/fail with the specific reasons, so a rejection is auditable.

    DEFENSE detectors are scored with the sign flipped: their job is to predict weakness, so
    a strongly NEGATIVE excess return is a pass. Judging them on the attack criteria would
    reject exactly the ones that work."""
    fails = []
    want_negative = r["group"] == "DEFENSE"
    sign = -1 if want_negative else 1
    direction = "negative" if want_negative else "positive"

    ins, out = sign * r["in_sample"], sign * r["holdout"]
    years_good = (r["years"] - r["years_positive"]) if want_negative else r["years_positive"]

    if r["n"] < MIN_N:
        fails.append(f"n={r['n']} < {MIN_N}")
    if not (ins > 0 and out > 0):
        fails.append(f"not {direction} in both periods "
                     f"(in {r['in_sample']}, out {r['holdout']})")
    if not (abs(r["t6"]) >= MIN_T or abs(r["t_holdout"]) >= MIN_T):
        fails.append(f"|t| < {MIN_T} in both periods (t6 {r['t6']}, t_out {r['t_holdout']})")
    if years_good < MIN_YEARS_POSITIVE:
        fails.append(f"{direction} in only {years_good}/{r['years']} years")
    # An attack detector must work in at least one regime; a defence detector that only
    # fires usefully in one regime is still useful, so the regime is recorded not gated.
    if not want_negative and r["regime"] in ("neither", "unknown"):
        fails.append(f"regime={r['regime']}")
    return (not fails), fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--explain", action="store_true", help="show why each detector failed")
    args = ap.parse_args()

    panel = pickle.load(open(CACHE / "panel.pkl", "rb"))
    broken, notes, systemic = check_panel_integrity(panel)
    for n in notes:
        print(f"[gate] {n}", file=sys.stderr)
    if systemic:
        print("\nPANEL INTEGRITY FAILED — refusing to promote anything.", file=sys.stderr)
        for line in broken[:12]:
            print(f"  {line}", file=sys.stderr)
        print("\nA broad pattern of impossible bars means the price adjustment is wrong "
              "again (the 2026-08 bug class). Fix it before scoring.", file=sys.stderr)
        raise SystemExit(1)
    if broken:
        print(f"[gate] excluding {len(broken)} broken series: "
              f"{', '.join(b.split(':')[0] for b in broken)}", file=sys.stderr)

    p = pd.read_pickle(CACHE / "observations.pkl")
    excluded = {b.split(":")[0] for b in broken}
    if excluded:
        p = p[~p.ticker.isin(excluded)]
    if "risk_on" not in p.columns:
        spy = panel["SPY"]["close"]
        risk_on = spy > spy.rolling(200).mean()
        p["risk_on"] = p["date"].map(
            lambda d: bool(risk_on.loc[risk_on.index[risk_on.index <= d][-1]])
            if len(risk_on.index[risk_on.index <= d]) else True)

    candidates = [c for c in p.columns if c in D.ALL or c.startswith("wyk_")]
    rows = [r for c in candidates if (r := evaluate(p, c))]

    promoted, rejected = [], []
    for r in rows:
        ok, fails = verdict(r)
        (promoted if ok else rejected).append({**r, "fails": fails})

    promoted.sort(key=lambda r: -r["holdout"])
    pd.set_option("display.width", 200)

    print("\n" + "=" * 104)
    print(f"PROMOTED — {len(promoted)} of {len(rows)} detectors cleared the gate")
    print("=" * 104)
    if promoted:
        print(pd.DataFrame(promoted)[["detector", "group", "n", "fire_pct", "x6", "t6",
                                      "in_sample", "holdout", "t_holdout",
                                      "years_positive", "regime"]].to_string(index=False))
    else:
        print("(none)")

    if args.explain:
        print("\n" + "=" * 104)
        print("REJECTED")
        print("=" * 104)
        for r in sorted(rejected, key=lambda r: -r["x6"]):
            print(f"{r['detector']:<26} x6={r['x6']:>7} t={r['t6']:>6}  →  {'; '.join(r['fails'])}")

    CACHE.mkdir(exist_ok=True)
    (CACHE / "promoted.json").write_text(json.dumps(
        {"promoted": promoted, "rejected": rejected,
         "criteria": {"min_n": MIN_N, "min_abs_t": MIN_T,
                      "min_years_positive": MIN_YEARS_POSITIVE,
                      "holdout_from": str(HOLDOUT_FROM.date())}}, indent=2))
    print(f"\nwrote {CACHE / 'promoted.json'}", file=sys.stderr)


if __name__ == "__main__":
    main()
