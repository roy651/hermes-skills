#!/usr/bin/env python3
"""Score every detector, with honest statistics.

Three hazards this guards against:

* CROSS-SECTIONAL CORRELATION. On any given date, signalled stocks move together. Pooling
  every observation and running a naive t-test would treat 500 correlated names as 500
  independent facts. Instead: average within each date, then test the time series of those
  date-means (Fama-MacBeth). The t-stat below has ~60 degrees of freedom, not 100,000.
* MULTIPLE TESTING. ~33 detectors are under test; at p<0.05 roughly two will look good by
  luck. Sign consistency across years is the tiebreak, not the p-value.
* OVERFITTING. 2021-2024 is the working sample. 2025-2026 is held out and reported apart.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import detectors as D

# Cache lives beside this file and is gitignored — it holds ~1GB of prices.
CACHE = Path(__file__).parent / "cache"
LAB = CACHE
HOLDOUT_FROM = pd.Timestamp("2025-01-01")


def fama_macbeth(sub: pd.DataFrame, col: str) -> tuple[float, float, int]:
    """Mean excess return and t-stat computed across dates, not observations."""
    per_date = sub.groupby("date")[col].mean()
    per_date = per_date.dropna()
    if len(per_date) < 8:
        return float("nan"), float("nan"), len(per_date)
    m = per_date.mean()
    se = per_date.std(ddof=1) / np.sqrt(len(per_date))
    return m, (m / se if se > 0 else float("nan")), len(per_date)


def score(p: pd.DataFrame, name: str, group: str) -> dict:
    fired = p[p[name]]
    if len(fired) < 150:
        return None
    ins = fired[fired.date < HOLDOUT_FROM]
    out = fired[fired.date >= HOLDOUT_FROM]

    m6, t6, ndates = fama_macbeth(fired, "x6")
    m3, t3, _ = fama_macbeth(fired, "x3")
    m6_in, _, _ = fama_macbeth(ins, "x6")
    m6_out, t6_out, _ = fama_macbeth(out, "x6")

    yearly = fired.groupby("year")["x6"].mean()
    consistent = int((yearly > 0).sum()), int(len(yearly))

    return {
        "detector": name, "group": group,
        "n": len(fired), "fire%": len(fired) / len(p) * 100,
        "x3": m3, "t3": t3,
        "x6": m6, "t6": t6,
        "win6": (fired.x6 > 0).mean() * 100,
        "med_dd": fired.dd.median(),
        "in": m6_in, "out": m6_out, "t_out": t6_out,
        "yrs+": f"{consistent[0]}/{consistent[1]}",
    }


def main():
    p = pd.read_pickle(LAB / "observations.pkl")
    print(f"observations: {len(p):,}   dates: {p.date.nunique()}   "
          f"tickers: {p.ticker.nunique():,}\n", file=sys.stderr)

    rows = [r for name, (group, _fn) in D.ALL.items()
            if (r := score(p, name, group)) is not None]
    res = pd.DataFrame(rows)

    pd.set_option("display.width", 250)
    fmt = lambda v: f"{v:7.2f}"

    print("=" * 132)
    print("DETECTOR BANK — forward excess return vs same-region peer mean (%)")
    print("x6 = mean 6-month excess.  t6 = Fama-MacBeth t-stat across dates (|t|>2 ≈ p<0.05).")
    print("in = 2021-2024 sample.  out = 2025-2026 HOLDOUT.  yrs+ = years with positive mean.")
    print("=" * 132)
    for g in ["TREND", "PULLBACK", "SQUEEZE", "VOLUME", "REVERSION", "DEFENSE"]:
        sub = res[res.group == g].sort_values("x6", ascending=False)
        if sub.empty:
            continue
        print(f"\n--- {g} ---")
        print(sub[["detector", "n", "fire%", "x3", "t3", "x6", "t6", "win6",
                   "med_dd", "in", "out", "t_out", "yrs+"]]
              .to_string(index=False, float_format=fmt))

    print("\n\n" + "=" * 132)
    print("RANKED — attack detectors by holdout performance (must also be positive in-sample)")
    print("=" * 132)
    atk = res[res.group != "DEFENSE"].copy()
    survivors = atk[(atk["in"] > 0) & (atk["out"] > 0)].sort_values("out", ascending=False)
    print(survivors[["detector", "group", "n", "fire%", "x6", "t6", "in", "out",
                     "t_out", "win6", "yrs+"]]
          .to_string(index=False, float_format=fmt))
    print(f"\n{len(survivors)} of {len(atk)} attack detectors positive in BOTH periods.")

    print("\n\n" + "=" * 132)
    print("DEFENSE — want strongly NEGATIVE excess (these are avoid/exit signals)")
    print("=" * 132)
    dfn = res[res.group == "DEFENSE"].sort_values("x6")
    print(dfn[["detector", "n", "fire%", "x6", "t6", "in", "out", "win6", "med_dd", "yrs+"]]
          .to_string(index=False, float_format=fmt))

    res.to_csv(LAB / "detector_scores.csv", index=False)


if __name__ == "__main__":
    main()
