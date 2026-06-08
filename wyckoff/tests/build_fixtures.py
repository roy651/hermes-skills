#!/usr/bin/env python3
"""Generate committed CSV fixtures for Tier 2 (real OHLCV snapshots). Run on the mini-PC:

    .venv/bin/python tests/build_fixtures.py

Snapshots are frozen once committed, so the Tier 2 tests stay offline and deterministic.
Re-run only to refresh the corpus (and then re-vet the labels in validate_events_tier2.py).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")
import data as md

FIX = Path(__file__).parent / "fixtures"
FIX.mkdir(exist_ok=True)

from datetime import date, timedelta

# Trailing 252-day snapshots (current structure).
TICKERS = ["ROK", "EQIX", "LLY", "NKE", "TDG", "EIX"]

# Historical 252-bar windows ending at a chosen date (review 3 §4b adversarials).
HISTORICAL = [
    ("SMCI_climax_240315", "SMCI", "2024-03-15"),   # climactic rally (effort >> 1.5) → filter rejects
    ("CVNA_failed_211231", "CVNA", "2021-12-31"),   # broke out then collapsed below the breakout level
    ("CVNA_quiettop_210915", "CVNA", "2021-09-15"),  # quiet-rally distribution TOP — known FP (effort < 1.5)
]

for t in TICKERS:
    df = md.fetch_ohlcv(t, days=252).df
    out = FIX / f"{t}_252d.csv"
    df.to_csv(out)
    print(f"wrote {out.name}  ({len(df)} rows, {df.index[0]} → {df.index[-1]})")

for name, ticker, end in HISTORICAL:
    start = (date.fromisoformat(end) - timedelta(days=400)).isoformat()
    df = md.fetch_ohlcv(ticker, start=start, end=end).df.tail(252)
    out = FIX / f"{name}.csv"
    df.to_csv(out)
    print(f"wrote {out.name}  ({len(df)} rows, {df.index[0]} → {df.index[-1]})")
