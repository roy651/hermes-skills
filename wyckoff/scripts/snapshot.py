#!/usr/bin/env python3
"""Daily point-in-time snapshot of the non-price data we can only observe live.

Analyst recommendation counts, headlines and market cap are served by Finnhub as a *current*
view. The value as at a past date is not retrievable afterwards, and vendors that do sell
point-in-time history charge for it. Every day not captured is permanently lost.

This is the same reasoning as the Reddit archive, and it exists because text and analyst data
are the one candidate signal family we currently cannot backtest — precisely because nobody
was storing them. Cheap to run, and the only way the option stays open.

    python snapshot.py            # holdings + watchlist
    python snapshot.py --dry-run  # print, write nothing
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")

import yaml
import finnhub
import holdings as portfolio

ARCHIVE = Path(__file__).parent.parent / "data" / "fundamentals_history"


def universe() -> list[str]:
    cfg = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())
    names = list(portfolio.load()) + [t.upper() for t in (cfg.get("watchlist") or [])]
    # Israeli mutual funds and TASE tickers are not on Finnhub; skip rather than burn calls.
    return sorted({t for t in names if "." not in t and not t.startswith("YL-")})


def snapshot_one(ticker: str) -> dict:
    row: dict = {}
    for key, fn in (("consensus", lambda: finnhub.analyst_consensus(ticker)),
                    ("market_cap", lambda: finnhub.market_cap(ticker)),
                    ("headlines", lambda: [n["headline"] for n in
                                           finnhub.company_news(ticker, days=2, limit=8)])):
        try:
            row[key] = fn()
        except Exception as e:
            row[key] = None
            print(f"[snapshot] {ticker}.{key}: {str(e)[:60]}", file=sys.stderr)
    return row


def main() -> None:
    dry = "--dry-run" in sys.argv
    tickers = universe()
    now = datetime.now(timezone.utc)
    out = {"captured_at": now.isoformat(), "tickers": {}}

    for t in tickers:
        out["tickers"][t] = snapshot_one(t)

    got = sum(1 for v in out["tickers"].values() if v.get("consensus") not in (None, "unknown"))
    print(f"[snapshot] {len(tickers)} tickers, {got} with a usable consensus", file=sys.stderr)

    if dry:
        print(json.dumps(out, indent=2)[:1200])
        return
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    path = ARCHIVE / f"{now:%Y-%m-%d}.json"
    path.write_text(json.dumps(out, separators=(",", ":")))
    print(f"[snapshot] wrote {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
