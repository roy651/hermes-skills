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

TICKERS = ["ROK", "EQIX", "LLY", "NKE", "TDG", "EIX"]

for t in TICKERS:
    df = md.fetch_ohlcv(t, days=252).df
    out = FIX / f"{t}_252d.csv"
    df.to_csv(out)
    print(f"wrote {out.name}  ({len(df)} rows, {df.index[0]} → {df.index[-1]})")
