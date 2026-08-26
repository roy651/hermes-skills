#!/usr/bin/env python3
"""Phase 1 of the earnings-event sleeve: quarterly EPS keyed on the date it became public.

This is the piece everything else waits on. The plan's entry signal is SUE — the earnings
surprise, standardised by its own history — and the version that needs no paid analyst data
compares reported EPS to the SAME QUARTER A YEAR AGO. That requires two things our existing
fundamentals archive cannot give:

  1. **History.** The archive began 2026-08-19 and only grows forward.
  2. **Point-in-time honesty.** Vendor figures are *restated*. Using today's corrected number to
     predict a 2019 return is lookahead wearing a feature's clothes. EDGAR stamps every fact
     with the date it was FILED, so we can reconstruct exactly what was knowable on any date.

SEC EDGAR is free and needs no key — only a descriptive User-Agent and 10 req/s, both already
handled by scripts/edgar.py, whose client this reuses rather than duplicating.

    python fundamentals.py --probe AAPL MSFT KO      # inspect what EDGAR returns for a few names
    python fundamentals.py --build                   # build the panel-wide event table
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import edgar

CACHE = Path(__file__).parent / "cache"
FACTS = CACHE / "edgar_facts"
TICKER_MAP = CACHE / "cik_map.json"

# Filers are inconsistent about which EPS concept they tag, and some report only one. Try in
# order of preference: diluted is the figure the market quotes.
EPS_TAGS = ["EarningsPerShareDiluted", "EarningsPerShareBasic",
            "IncomeLossFromContinuingOperationsPerDilutedShare"]
QUARTERLY_FORMS = {"10-Q", "10-K"}
# A genuine quarterly report lands within a quarter or so of period end. The probe found filings
# stamped 396 and 602 days after their period — those are PRIOR-PERIOD COMPARATIVES carried
# inside a later filing, not fresh news. Treating one as an announcement would invent an event
# on a date when nothing was actually announced.
MAX_FILING_LAG_DAYS = 120


def cik_map() -> dict[str, str]:
    """ticker -> zero-padded CIK, from the SEC's own published mapping."""
    if TICKER_MAP.exists():
        return json.loads(TICKER_MAP.read_text())
    raw = json.loads(edgar._get("https://www.sec.gov/files/company_tickers.json"))
    out = {r["ticker"].upper().replace(".", "-"): str(r["cik_str"]).zfill(10)
           for r in raw.values()}
    TICKER_MAP.parent.mkdir(parents=True, exist_ok=True)
    TICKER_MAP.write_text(json.dumps(out))
    print(f"[fundamentals] cik map: {len(out)} tickers", file=sys.stderr)
    return out


def company_facts(ticker: str, cik: str) -> dict | None:
    FACTS.mkdir(parents=True, exist_ok=True)
    f = FACTS / f"{ticker}.json"
    if f.exists():
        return json.loads(f.read_text())
    try:
        txt = edgar._get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
    except Exception as e:
        print(f"[fundamentals] {ticker}: {str(e)[:70]}", file=sys.stderr)
        return None
    f.write_text(txt)
    return json.loads(txt)


def eps_history(facts: dict) -> pd.DataFrame:
    """One row per reported quarter: period end, the date it was FILED, and the EPS.

    A single quarter is often reported several times (original 10-Q, then restated inside a
    later filing). We keep the EARLIEST filing of each period end — that is the number the
    market actually saw first, which is the only one a point-in-time signal may use.
    """
    units = facts.get("facts", {}).get("us-gaap", {})
    rows = []
    for rank, tag in enumerate(EPS_TAGS):
        if tag not in units:
            continue
        for unit, entries in units[tag].get("units", {}).items():
            for e in entries:
                if e.get("form") not in QUARTERLY_FORMS or not e.get("filed"):
                    continue
                start, end = e.get("start"), e.get("end")
                if not start or not end:
                    continue
                span = (pd.Timestamp(end) - pd.Timestamp(start)).days
                if not (60 <= span <= 120):        # a QUARTER, not a full year or a half
                    continue
                filed = pd.Timestamp(e["filed"])
                if (filed - pd.Timestamp(end)).days > MAX_FILING_LAG_DAYS:
                    continue                       # a comparative, not an announcement
                rows.append({"period_end": pd.Timestamp(end), "filed": filed,
                             "eps": float(e["val"]), "tag": tag, "form": e["form"],
                             "rank": rank})
        # Do NOT stop at the first tag that returns anything. XOM tags a single quarter under
        # the preferred concept and the rest under another; breaking early left it with one
        # usable quarter out of seventeen years. Collect every tag, then prefer by rank.
    if not rows:
        return pd.DataFrame(columns=["period_end", "filed", "eps"])
    # Per period: preferred concept first (rank), then the EARLIEST filing of it — that is the
    # number the market actually saw, not a later restatement.
    df = pd.DataFrame(rows).sort_values(["period_end", "rank", "filed"])
    return df.groupby("period_end", as_index=False).first().drop(columns=["rank"])


def annual_eps(facts: dict) -> pd.DataFrame:
    """Full-year EPS, same point-in-time discipline. Needed to recover the missing quarter."""
    units = facts.get("facts", {}).get("us-gaap", {})
    rows = []
    for rank, tag in enumerate(EPS_TAGS):
        for unit, entries in units.get(tag, {}).get("units", {}).items():
            for e in entries:
                if e.get("form") != "10-K" or not e.get("filed"):
                    continue
                start, end = e.get("start"), e.get("end")
                if not start or not end:
                    continue
                if not (330 <= (pd.Timestamp(end) - pd.Timestamp(start)).days <= 400):
                    continue
                filed = pd.Timestamp(e["filed"])
                if (filed - pd.Timestamp(end)).days > MAX_FILING_LAG_DAYS:
                    continue
                rows.append({"fy_end": pd.Timestamp(end), "fy_start": pd.Timestamp(start),
                             "filed": filed, "eps": float(e["val"]), "rank": rank})
    if not rows:
        return pd.DataFrame(columns=["fy_end", "fy_start", "filed", "eps"])
    df = pd.DataFrame(rows).sort_values(["fy_end", "rank", "filed"])
    return df.groupby("fy_end", as_index=False).first().drop(columns=["rank"])


def with_q4(quarters: pd.DataFrame, annuals: pd.DataFrame) -> pd.DataFrame:
    """Reconstruct the missing fiscal Q4 as FY minus the three quarters inside it.

    Every filer reports Q1-Q3 in 10-Qs and then folds Q4 into the annual 10-K, so a naive
    quarterly scrape silently loses ~25% of all earnings events — and the annual report is the
    highest-attention one of the year. The derived quarter inherits the 10-K's FILING date,
    which is exactly when that number became public.
    """
    if quarters.empty or annuals.empty:
        return quarters
    out = [quarters]
    for a in annuals.itertuples():
        inside = quarters[(quarters.period_end > a.fy_start) & (quarters.period_end <= a.fy_end)]
        if len(inside) != 3:                    # need exactly Q1-Q3 to subtract cleanly
            continue
        if (quarters.period_end == a.fy_end).any():
            continue                            # already reported standalone
        out.append(pd.DataFrame([{
            "period_end": a.fy_end, "filed": a.filed,
            "eps": round(a.eps - inside.eps.sum(), 4),
            "tag": "derived", "form": "10-K-derived"}]))
    res = pd.concat(out, ignore_index=True).sort_values("period_end")
    return res.groupby("period_end", as_index=False).first()


def probe(tickers: list[str]) -> None:
    cm = cik_map()
    for t in tickers:
        cik = cm.get(t.upper())
        if not cik:
            print(f"{t}: NOT IN SEC MAP"); continue
        facts = company_facts(t.upper(), cik)
        if not facts:
            print(f"{t}: no facts"); continue
        h = with_q4(eps_history(facts), annual_eps(facts))
        if h.empty:
            print(f"{t}: CIK {cik} — no quarterly EPS found"); continue
        lag = (h.filed - h.period_end).dt.days
        print(f"\n{t}  CIK {cik}  ({facts.get('entityName','')[:40]})")
        print(f"  {len(h)} quarters, {h.period_end.min().date()} .. {h.period_end.max().date()}")
        print(f"  filing lag: median {lag.median():.0f}d, min {lag.min()}d, max {lag.max()}d")
        print(h.tail(4)[["period_end", "filed", "eps", "form"]].to_string(index=False))


def drop_mistagged(df: pd.DataFrame) -> pd.DataFrame:
    """Remove facts where a TOTAL was tagged under a PER-SHARE concept.

    A handful of filers tag net income in dollars against an EPS element — FITB reports
    "955,000,000" for a quarter. It is rare (~0.06% of events) but corrosive: SUE divides the
    surprise by the standard deviation of a company's own history, so one 10^9 outlier inflates
    that denominator and silently zeroes the signal for every quarter of that ticker.

    The test is per-ticker and scale-free — an absolute cap would wrongly discard genuinely
    high-EPS names — and it compares each value to that company's own typical magnitude.
    """
    med = df.groupby("ticker").eps.transform(lambda s: s.abs().median()).clip(lower=0.10)
    bad = df.eps.abs() > 100 * med
    if bad.any():
        print(f"[clean] dropped {bad.sum()} mis-tagged event(s) across "
              f"{df.loc[bad, 'ticker'].nunique()} ticker(s)", file=sys.stderr)
    return df[~bad].copy()


def build() -> pd.DataFrame:
    """Panel-wide event table: one row per (ticker, reported quarter, filing date)."""
    import pickle
    panel = pickle.load(open(CACHE / "panel.pkl", "rb"))
    us = sorted(t for t, d in panel.items()
                if d is not None and "." not in t and not t.startswith("^"))
    cm = cik_map()
    rows, miss, thin = [], 0, 0
    for n, t in enumerate(us):
        cik = cm.get(t.upper().replace(".", "-"))
        if not cik:
            miss += 1
            continue
        facts = company_facts(t, cik)
        if not facts:
            miss += 1
            continue
        try:
            h = with_q4(eps_history(facts), annual_eps(facts))
        except Exception as e:
            print(f"[build] {t}: {str(e)[:60]}", file=sys.stderr)
            continue
        if len(h) < 8:
            thin += 1
            continue
        h = h.assign(ticker=t)
        rows.append(h[["ticker", "period_end", "filed", "eps", "form"]])
        if n % 100 == 0:
            print(f"[build] {n}/{len(us)}  events={sum(len(r) for r in rows):,}", file=sys.stderr)
    out = drop_mistagged(pd.concat(rows, ignore_index=True).sort_values(["ticker", "period_end"]))
    out.to_pickle(CACHE / "eps_events.pkl")
    print(f"[build] {len(out):,} events across {out.ticker.nunique()} tickers "
          f"({miss} no-facts, {thin} thin)", file=sys.stderr)
    print(f"[build] derived Q4 rows: {(out.form == '10-K-derived').sum():,} "
          f"({(out.form == '10-K-derived').mean()*100:.1f}%)", file=sys.stderr)
    print(f"[build] span {out.period_end.min().date()} .. {out.period_end.max().date()}",
          file=sys.stderr)
    return out


if __name__ == "__main__":
    if "--build" in sys.argv:
        build()
    elif "--probe" in sys.argv:
        names = sys.argv[sys.argv.index("--probe") + 1:]
        probe(names or ["AAPL", "MSFT", "KO"])
    else:
        print(__doc__)
