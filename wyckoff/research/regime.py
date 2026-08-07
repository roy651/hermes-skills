#!/usr/bin/env python3
"""Which detectors survive a regime change?

Everything measured so far is conditioned on one market. The sample does contain a genuine
bear market — 2022 — so the regime question can be asked without new data: classify each
observation date by whether SPY sat above or below its own 200-day average, then score
every detector separately in each state.

A detector that only works risk-on is not wrong; it needs a switch. A detector that works
in both is the rarer and more valuable thing.
"""
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import detectors as D

# Cache lives beside this file and is gitignored — it holds ~1GB of prices.
CACHE = Path(__file__).parent / "cache"
LAB = CACHE


def fm(sub, col="x6"):
    per = sub.groupby("date")[col].mean().dropna()
    if len(per) < 5:
        return np.nan, np.nan
    m = per.mean()
    se = per.std(ddof=1) / np.sqrt(len(per))
    return m, (m / se if se > 0 else np.nan)


def main():
    p = pd.read_pickle(LAB / "observations.pkl")
    panel = pickle.load(open(LAB / "panel.pkl", "rb"))

    spy = panel["SPY"]["close"]
    ma200 = spy.rolling(200).mean()
    risk_on = (spy > ma200)

    def state(d):
        prior = risk_on.index[risk_on.index <= d]
        return bool(risk_on.loc[prior[-1]]) if len(prior) else True

    p["risk_on"] = p["date"].map(state)
    counts = p.groupby("risk_on").agg(obs=("x6", "size"), dates=("date", "nunique"))
    print("REGIME SPLIT (SPY vs its own 200-day average)")
    print(counts.to_string())
    print()

    names = [c for c in p.columns
             if c in D.ALL or c.startswith("wyk_")]

    rows = []
    for nm in names:
        on = p[p[nm] & p.risk_on]
        off = p[p[nm] & ~p.risk_on]
        if len(on) < 100 or len(off) < 100:
            continue
        m_on, t_on = fm(on)
        m_off, t_off = fm(off)
        rows.append({"detector": nm, "n_on": len(on), "x6_riskON": m_on, "t_on": t_on,
                     "n_off": len(off), "x6_riskOFF": m_off, "t_off": t_off,
                     "swing": m_on - m_off,
                     "both": "YES" if (m_on > 0 and m_off > 0) else
                             ("flips" if (m_on > 0) != (m_off > 0) else "no")})

    res = pd.DataFrame(rows).sort_values("x6_riskOFF", ascending=False)
    pd.set_option("display.width", 200)
    fmt = lambda v: f"{v:8.2f}"

    print("=" * 116)
    print("DETECTOR PERFORMANCE BY REGIME — mean 6m excess vs peers (%)")
    print("=" * 116)
    print(res.to_string(index=False, float_format=fmt))

    print("\n\nWORKS IN BOTH REGIMES (the durable ones)")
    print("=" * 116)
    print(res[res.both == "YES"].sort_values("x6_riskOFF", ascending=False)
          .to_string(index=False, float_format=fmt))

    print("\n\nREGIME-DEPENDENT — needs a switch")
    print("=" * 116)
    print(res[res.both == "flips"].sort_values("swing", ascending=False)
          .to_string(index=False, float_format=fmt))

    res.to_csv(LAB / "regime_scores.csv", index=False)


if __name__ == "__main__":
    main()
