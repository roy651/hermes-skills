#!/usr/bin/env python3
"""The only signals that survived validation, applied to real positions.

Everything here is deliberately NEGATIVE or contextual. After seven honest attempts we have no
validated entry signal, so nothing in this module claims a name will go up. What it does is
apply the two things that did clear a significance bar, plus one timing observation:

  * **Negative-surprise veto.** SUE < -1 predicts underperformance at t=-4.44 over 5 days and
    -2.88 over a quarter. A big beat predicts NOTHING at any horizon (t between -1.1 and +0.4),
    which is why this is a veto and not a screen.
  * **Uncertainty flag.** High Loughran-McDonald uncertainty language: median -4.10% over six
    months, t=-2.91. PROVISIONAL — it covers only 263 companies over 19 quarters and was one of
    36 tests, so it is marked as such wherever it appears.
  * **Post-beat day-1 exit.** A beat pops +0.271% on day 1 (t=3.44) then gives back -0.155%
    over days 1-3 (t=-2.35). Useless as an entry, genuinely useful if you already hold it.

Usage:  signals.py --check AAPL MSFT
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CACHE = Path(__file__).parent.parent / "research" / "cache"
SUE_VETO = -1.0
VETO_LOOKBACK_DAYS = 95        # one quarter — the horizon the veto was measured over
BEAT_SUE = 1.0
BEAT_WINDOW_DAYS = 4


def _load(name):
    f = CACHE / name
    if not f.exists():
        return None
    try:
        return pd.read_pickle(f)
    except Exception:
        return None


def sue_flags(tickers: list[str], today: pd.Timestamp | None = None) -> dict:
    """{ticker: {'sue', 'days_ago', 'kind'}} for anything that reported recently."""
    ev = _load("sue_events.pkl")
    if ev is None or ev.empty:
        return {}
    today = today or pd.Timestamp.now().normalize()
    ev = ev[ev.ticker.isin(tickers)].copy()
    ev["age"] = (today - pd.to_datetime(ev.filed)).dt.days
    ev = ev[(ev.age >= 0) & (ev.age <= VETO_LOOKBACK_DAYS)]
    out = {}
    for t, g in ev.groupby("ticker"):
        r = g.sort_values("age").iloc[0]
        kind = ("miss" if r.sue <= SUE_VETO else
                "beat" if r.sue >= BEAT_SUE else "inline")
        out[t] = {"sue": float(r.sue), "days_ago": int(r.age), "kind": kind}
    return out


def uncertainty_flags(tickers: list[str]) -> dict:
    """{ticker: percentile} of filing uncertainty language — provisional, 263 names only."""
    lz = _load("lazy_events.pkl")
    if lz is None or lz.empty or "lm_uncertainty" not in lz.columns:
        return {}
    latest = (lz.sort_values("filed").groupby("ticker").last()
                .reset_index()[["ticker", "lm_uncertainty", "filed"]])
    latest["pct"] = latest.lm_uncertainty.rank(pct=True) * 100
    return {r.ticker: {"pct": float(r.pct), "val": float(r.lm_uncertainty),
                       "filed": str(pd.Timestamp(r.filed).date())}
            for r in latest.itertuples() if r.ticker in set(tickers)}


def position_notes(tickers: list[str], today: pd.Timestamp | None = None) -> list[str]:
    """Validated flags for names already held. Exit-side only, by design."""
    lines, sue, unc = [], sue_flags(tickers, today), uncertainty_flags(tickers)
    for t in sorted(set(tickers)):
        bits = []
        s = sue.get(t)
        if s and s["kind"] == "miss":
            bits.append(f"🔻 missed {s['days_ago']}d ago (SUE {s['sue']:+.1f}) — "
                        f"validated veto, t=-4.44")
        elif s and s["kind"] == "beat" and s["days_ago"] <= BEAT_WINDOW_DAYS:
            bits.append(f"⏱ beat {s['days_ago']}d ago — day 1 is historically the best exit "
                        f"in the next two weeks")
        u = unc.get(t)
        if u and u["pct"] >= 80:
            bits.append(f"🌫 filing uncertainty {u['pct']:.0f}th pct — "
                        f"<i>provisional, 263-name sample</i>")
        if bits:
            lines.append(f"• <b>{t}</b> — " + "; ".join(bits))
    return lines


FLAG_AGE = re.compile(r"(?:missed|beat) (\d+)d ago")


def build_section(tickers: list[str], fresh_days: int | None = None) -> str | None:
    """With `fresh_days`, flags older than that collapse into one trailing line. A veto is
    valid for the whole quarter it was measured over, but repeating the same line for three
    months trains the reader to skip the section; the weekly still prints every flag in full."""
    notes = position_notes(tickers)
    if not notes:
        return None
    carried = []
    if fresh_days is not None:
        fresh = []
        for line in notes:
            m = FLAG_AGE.search(line)
            (carried if m and int(m.group(1)) > fresh_days else fresh).append(line)
        notes = fresh
    body = "\n".join(notes)
    if carried:
        names = ", ".join(f"{re.search(r'<b>(.+?)</b>', l).group(1)} ({FLAG_AGE.search(l).group(1)}d)"
                          for l in carried)
        body += (("\n" if notes else "") +
                 f"• older, still inside the {VETO_LOOKBACK_DAYS}d veto window (full detail in the weekly): {names}")
    return ("🧾 <b>Validated flags on held positions</b>\n" + body +
            "\n<i>Exit-side only. We have no validated entry signal.</i>")


if __name__ == "__main__":
    ts = sys.argv[sys.argv.index("--check") + 1:] if "--check" in sys.argv else ["AAPL"]
    print(build_section(ts) or "(no flags)")


# ------------------------------------------------------------------ weekly entry residue
# There is no validated entry signal, so this does NOT rank candidates by expected return. It
# applies the validated EXCLUSIONS to the universe and reports what is left, ordered by the only
# positive-signed thing that ever cleared anything — mom_12_1, and even that is weak (t=1.98 on
# the widened sample, and worth nothing at all in 2016-2020). The label says so on the report.

MIN_PRICE = 5.0
MIN_DOLLAR_VOL = 5e6
# Sorting by raw momentum puts names up 2,000% at the top — precisely the moonshot tail that
# signal-validation.md §11 showed is a survivorship-contaminated lottery (banding entries to
# 30-100% cut the backtest from 62.4% to 24.5% CAGR, i.e. the tail WAS the return, and it was
# not real). Listing them as candidates would be reproducing the failed strategy.
MOM_BAND = (30.0, 100.0)


def entry_residue(top: int = 12, held: list[str] | None = None) -> str:
    import pickle
    held = set(held or [])
    panel_f = CACHE / "panel.pkl"
    if not panel_f.exists():
        return "📋 <b>Entry residue</b> — <i>panel unavailable.</i>"
    panel = pickle.load(open(panel_f, "rb"))
    us = [t for t, d in panel.items()
          if d is not None and "." not in t and not t.startswith("^") and len(d) > 300]

    rows = []
    for t in us:
        d = panel[t]
        c = d["close"].to_numpy(dtype=float)
        v = d["volume"].to_numpy(dtype=float)
        if len(c) < 260 or not np.isfinite(c[-1]) or c[-1] < MIN_PRICE:
            continue
        dv = np.nanmean(c[-60:] * v[-60:])
        if not np.isfinite(dv) or dv < MIN_DOLLAR_VOL:
            continue
        if not (np.isfinite(c[-252]) and c[-252] > 0):
            continue
        hi = np.nanmax(c[-252:])
        rows.append({"ticker": t, "mom": (c[-22] / c[-252] - 1) * 100,
                     "price": c[-1], "off_hi": (c[-1] / hi - 1) * 100, "advusd": dv})
    if not rows:
        return "📋 <b>Entry residue</b> — <i>no candidates passed the liquidity floor.</i>"
    df = pd.DataFrame(rows)

    n0 = len(df)
    sue = sue_flags(df.ticker.tolist())
    vetoed = {t for t, s in sue.items() if s["kind"] == "miss"}
    unc = uncertainty_flags(df.ticker.tolist())
    unc_vetoed = {t for t, u in unc.items() if u["pct"] >= 90}
    df = df[~df.ticker.isin(vetoed | unc_vetoed) & ~df.ticker.isin(held)]

    n_band = len(df)
    df = df[(df.mom >= MOM_BAND[0]) & (df.mom <= MOM_BAND[1])]
    n_dropped = n_band - len(df)
    df = df.sort_values("mom", ascending=False).head(top)
    lines = ["📋 <b>Entry residue</b> — <i>NOT recommendations</i>",
             f"<i>{n0} liquid names, minus {len(vetoed)} recent-miss vetoes and "
             f"{len(unc_vetoed)} high-uncertainty, minus current holdings, minus "
             f"{n_dropped} outside the 30-100% momentum band. "
             f"Ordered by 12-1 momentum — our weakest surviving signal (t=1.98, and zero in "
             f"2016-2020). Treat as a discussion shortlist, nothing more.</i>", "<pre>",
             f"{'TICKER':<8}{'PRICE':>9}{'MOM':>8}{'OFF-HI':>8}{'ADV$m':>8}"]
    for r in df.itertuples():
        lines.append(f"{r.ticker[:7]:<8}{r.price:>9.2f}{r.mom:>7.0f}%{r.off_hi:>7.0f}%"
                     f"{r.advusd/1e6:>8.0f}")
    lines.append("</pre>")
    return "\n".join(lines)
