#!/usr/bin/env python3
"""Screen candidate historical windows for CONFIRMED markup-pullback false positives (buying-climax
tops) and failed breakouts, to freeze as Tier-2 fixtures. Run on the mini-PC (needs market data):

    .venv/bin/python tests/screen_historical.py

A confirmed climax FP = the markup geometry fires (effort bypassed) AND the rally was climactic
(effort_ratio > MP_EFFORT_X) AND price closed back below the breakout level in the aftermath.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")
import data as md
import events

# (ticker, fixture_end ≈ first pullback after the climax peak) — targeting volume blow-offs
CANDIDATES = [
    ("GME", "2021-02-01"), ("GME", "2021-03-15"),
    ("AMC", "2021-06-15"), ("AMC", "2021-06-30"),
    ("BBBY", "2022-08-30"), ("BBBY", "2022-08-23"),
    ("BB", "2021-02-01"), ("MARA", "2021-02-12"), ("RIOT", "2021-02-12"),
    ("SMCI", "2024-03-15"), ("SMCI", "2024-08-30"),
    ("NVDA", "2024-07-15"), ("CVNA", "2021-09-15"), ("PTON", "2021-02-15"),
]

print("ticker  end          n   fires effort  breakout  broke_below_after  after_min")
for tk, fe in CANDIDATES:
    fe_d = date.fromisoformat(fe)
    start = (fe_d - timedelta(days=560)).isoformat()
    end = (fe_d + timedelta(days=120)).isoformat()
    try:
        df = md.fetch_ohlcv(tk, start=start, end=end).df
        fix = df[df.index <= fe_d].tail(252)
        after = df[df.index > fe_d]
        mp = events.detect_markup_pullback(fix, enforce_effort=False)
        if not mp:
            print(f"{tk:6}  {fe}  {len(fix):4}  geometry not present")
            continue
        bl = mp["breakout_level"]
        amin = float(after["close"].min()) if len(after) else None
        broke = bool(amin is not None and amin < bl)
        print(f"{tk:6}  {fe}  {len(fix):4}  YES   {mp['effort_ratio']:<6}  {bl:<8}  {str(broke):17}  {amin}")
    except Exception as e:
        print(f"{tk:6}  {fe}  ERR {e}")
