#!/usr/bin/env python3
"""Does the earnings drift survive being traded? Costs, capacity and all.

The decile study says the signal is real (+0.57% top-minus-bottom over 5 sessions, t=2.95).
That is not a trade. Three things stand between a decile spread and money:

1. **The decile cut peeks.** Ranking within a month needs the whole month's cross-section, which
   you do not have on the day you must act. This uses an ABSOLUTE SUE threshold instead — a rule
   you can apply the moment a filing lands.
2. **Long-only halves it.** We cannot short, so the bottom decile's contribution is unavailable.
3. **A 5-day hold turns over ~50x a year per slot.** At 10bp round trip that is ~5% of capital
   annually in costs against a gross edge measured in tenths of a percent per event.

Events also arrive in bursts — earnings season floods, then nothing — so a fixed slot count is
idle much of the quarter. Idle capital sits in SPY, matching the convention used elsewhere here.

Usage:  pead_sim.py
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CACHE = Path(__file__).parent / "cache"
START_EQUITY = 100.0


def run(ev: pd.DataFrame, close: pd.DataFrame, spy: pd.Series, *, sue_min: float,
        hold_days: int, slots: int, cost_bps: float) -> dict:
    idx = close.index
    by_day: dict[pd.Timestamp, list] = {}
    for r in ev[ev.sue >= sue_min].itertuples():
        by_day.setdefault(r.filed, []).append(r.ticker)

    side = cost_bps / 2 / 10_000
    cash, equity, trades = START_EQUITY, [], 0
    open_pos: list[dict] = []          # {ticker, shares, exit_i}
    spy_sh = 0.0
    first = ev.filed.min()
    days = [d for d in idx if d >= first]

    for i, d in enumerate(idx):
        if d < first:
            continue
        px = close.loc[d]
        sp = spy.get(d, np.nan)

        # exits: fixed holding window, the drift window itself
        for p in [p for p in open_pos if p["exit_i"] <= i]:
            v = px.get(p["ticker"], np.nan)
            if np.isfinite(v):
                cash += p["shares"] * v * (1 - side)
                trades += 1
            open_pos.remove(p)

        cands = [t for t in by_day.get(d, []) if t in close.columns
                 and np.isfinite(px.get(t, np.nan))]
        free = slots - len(open_pos)
        if cands and free > 0:
            if spy_sh and np.isfinite(sp):      # free the parked capital first
                cash += spy_sh * sp * (1 - side)
                spy_sh = 0.0
            mtm = cash + sum(p["shares"] * px.get(p["ticker"], 0) for p in open_pos)
            slot_v = mtm / slots
            for t in cands[:free]:
                spend = min(slot_v, cash)
                if spend <= 0:
                    break
                open_pos.append({"ticker": t, "shares": spend * (1 - side) / px[t],
                                 "exit_i": i + hold_days})
                cash -= spend
                trades += 1

        # park anything idle in SPY rather than pretending cash is free
        if cash > 1e-6 and np.isfinite(sp) and not by_day.get(d):
            spy_sh += cash * (1 - side) / sp
            cash = 0.0

        val = cash + spy_sh * (sp if np.isfinite(sp) else 0) + \
              sum(p["shares"] * px.get(p["ticker"], np.nan) for p in open_pos)
        equity.append((d, val if np.isfinite(val) else (equity[-1][1] if equity else START_EQUITY)))

    eq = pd.Series(dict(equity)).sort_index()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    r = eq.pct_change().dropna()
    b = spy.reindex(eq.index).ffill()
    return {"CAGR%": ((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100,
            "vol%": r.std() * np.sqrt(252) * 100,
            "maxDD%": (eq / eq.cummax() - 1).min() * 100,
            "SPY%": ((b.iloc[-1] / b.iloc[0]) ** (1 / yrs) - 1) * 100,
            "trades/yr": trades / yrs, "equity": eq}


def main():
    ev = pd.read_pickle(CACHE / "sue_events.pkl").dropna(subset=["sue"])
    panel = pickle.load(open(CACHE / "panel.pkl", "rb"))
    us = [t for t, d in panel.items()
          if d is not None and "." not in t and not t.startswith("^") and len(d) > 400]
    close = pd.DataFrame({t: panel[t]["close"] for t in us}).sort_index()
    spy = panel["SPY"]["close"]
    print(f"[sim] {len(ev):,} events, {close.shape[1]} tickers", file=sys.stderr)
    pd.set_option("display.width", 200)

    print("\n### COST SENSITIVITY — SUE>=1.5, 10-day hold, 10 slots")
    rows = []
    for c in (0, 5, 10, 20, 30):
        s = run(ev, close, spy, sue_min=1.5, hold_days=10, slots=10, cost_bps=c)
        s.pop("equity"); rows.append({"cost_bps": c, **s})
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda v: f"{v:8.2f}"))

    print("\n### THRESHOLD x HOLD — at 10bp, 10 slots")
    rows = []
    for sm in (1.0, 1.5, 2.0):
        for hd in (5, 10, 21):
            s = run(ev, close, spy, sue_min=sm, hold_days=hd, slots=10, cost_bps=10)
            s.pop("equity")
            rows.append({"sue_min": sm, "hold": hd, **s})
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda v: f"{v:8.2f}"))


if __name__ == "__main__":
    main()
