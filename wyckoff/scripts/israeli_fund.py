#!/usr/bin/env python3
"""NAV lookup for Israeli mutual funds (קרן נאמנות), which Yahoo Finance does not carry.

TASE *ETFs* (קרן סל, e.g. TCH-F3.TA) are on Yahoo and go through data.py as normal. Plain mutual
funds — money-market/kaspit funds in particular — are not, so a holding in one is invisible to the
whole pipeline. Two public pages carry the NAV, no API key:

  globes  — spot NAV in a clean element; keyed by the Globes instrument id
  funder  — an embedded dated NAV series; keyed by the 7-digit fund number

Used only for holdings that declare `globes_id` / `fund_id`; nothing else changes.

    python israeli_fund.py --globes-id 617583
    python israeli_fund.py --fund-id 5141452 --history 5
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from datetime import datetime, timezone

import requests

_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
_GLOBES = "https://www.globes.co.il/portal/instrument.aspx?instrumentid={id}"
_FUNDER = "https://www.funder.co.il/fund/{id}"
_TIMEOUT = 20


def nav_from_globes(globes_id: str | int) -> float:
    """Current NAV in agorot, from the quote block on the Globes instrument page."""
    html = requests.get(_GLOBES.format(id=globes_id), headers=_UA, timeout=_TIMEOUT).text
    m = re.search(r'id="bgLastDeal"[^>]*>\s*([\d.,]+)\s*<', html)
    if not m:
        raise ValueError(f"globes: NAV element not found for instrument {globes_id}")
    return float(m.group(1).replace(",", ""))


def nav_history(fund_id: str | int) -> list[tuple[str, float]]:
    """[(iso_date, nav), ...] ascending, from the chart series embedded in the funder page."""
    html = requests.get(_FUNDER.format(id=fund_id), headers=_UA, timeout=_TIMEOUT).text
    points = re.findall(r'\{"c":"(\d{4}-\d{2}-\d{2})","p":([\d.]+)\}', html)
    if not points:
        raise ValueError(f"funder: no NAV series found for fund {fund_id}")
    return sorted((d, float(p)) for d, p in points)


def as_ticker_data(fund_id: str | int | None, globes_id: str | int | None, name: str = ""):
    """Adapt a fund's published NAV into the same TickerData shape data.py returns for Yahoo, so a
    holding in one flows through the existing pipeline untouched.

    NAV is quoted in agorot, exactly like a .TA listing — normalise to ILS (÷100) to match how
    data.py already handles TASE prices, so avg_cost (also stored in agorot) lines up downstream."""
    import pandas as pd
    from data import TickerData

    if fund_id:
        series = nav_history(fund_id)
    elif globes_id:
        today = datetime.now(timezone.utc).date().isoformat()
        series = [(today, nav_from_globes(globes_id))]
    else:
        raise ValueError("need fund_id or globes_id")

    df = pd.DataFrame(
        {"Date": [datetime.fromisoformat(d).date() for d, _ in series],
         "close": [nav / 100.0 for _, nav in series]}
    ).set_index("Date")
    for col in ("open", "high", "low"):          # a NAV has no intraday range; mirror the close
        df[col] = df["close"]
    df["volume"] = 0
    return TickerData(df=df, name=name or f"fund {fund_id or globes_id}", currency="ILS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--globes-id", help="Globes instrument id (spot NAV)")
    ap.add_argument("--fund-id", help="7-digit Israeli fund number (NAV history)")
    ap.add_argument("--history", type=int, default=0, help="print the last N history points")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    out: dict = {}
    if args.globes_id:
        out["nav"] = nav_from_globes(args.globes_id)
    if args.fund_id:
        hist = nav_history(args.fund_id)
        out["history_points"] = len(hist)
        out["latest"] = {"date": hist[-1][0], "nav": hist[-1][1]}
        if args.history:
            out["recent"] = [{"date": d, "nav": p} for d, p in hist[-args.history:]]
    if not out:
        ap.error("pass --globes-id and/or --fund-id")

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
