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


def probe(tickers: list[str]) -> None:
    cm = cik_map()
    for t in tickers:
        cik = cm.get(t.upper())
        if not cik:
            print(f"{t}: NOT IN SEC MAP"); continue
        facts = company_facts(t.upper(), cik)
        if not facts:
            print(f"{t}: no facts"); continue
        h = eps_history(facts)
        if h.empty:
            print(f"{t}: CIK {cik} — no quarterly EPS found"); continue
        lag = (h.filed - h.period_end).dt.days
        print(f"\n{t}  CIK {cik}  ({facts.get('entityName','')[:40]})")
        print(f"  {len(h)} quarters, {h.period_end.min().date()} .. {h.period_end.max().date()}")
        print(f"  filing lag: median {lag.median():.0f}d, min {lag.min()}d, max {lag.max()}d")
        print(h.tail(4)[["period_end", "filed", "eps", "form"]].to_string(index=False))


if __name__ == "__main__":
    if "--probe" in sys.argv:
        names = sys.argv[sys.argv.index("--probe") + 1:]
        probe(names or ["AAPL", "MSFT", "KO"])
    else:
        print(__doc__)
