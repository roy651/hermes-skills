#!/usr/bin/env python3
"""Review-4 §5.3 probe — does an effort-vs-result read separate healthy markups from quiet-tops
BEFORE we build a gate on it? Computes, over the post-breakout window:
  • up/down volume balance  = Σ vol(up days) / Σ vol(down days)   (>1 demand, <1 supply)
  • close location          = mean((close-low)/(high-low))         (near 1 = closes strong)
Report only — no gate is shipped here.

Run on the mini-PC:  .venv/bin/python tests/probe_refined_b.py
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")
import data as md
import events

# (label, ticker, end) — healthy current pullbacks vs known quiet-top FPs
CASES = [
    ("healthy  ROK", "ROK", None),
    ("healthy  EQIX", "EQIX", None),
    ("quiettop CVNA", "CVNA", "2021-09-15"),
    ("quiettop PTON", "PTON", "2021-02-15"),
]

print("case            n_post  up/down_vol  close_loc  breakout")
for label, tk, end in CASES:
    if end:
        start = (date.fromisoformat(end) - timedelta(days=400)).isoformat()
        df = md.fetch_ohlcv(tk, start=start, end=end).df.tail(252)
    else:
        df = md.fetch_ohlcv(tk, days=252).df
    mp = events.detect_markup_pullback(df, enforce_effort=False)
    if not mp:
        print(f"{label:15} geometry not present")
        continue
    bl = mp["breakout_level"]
    c = df["close"].values
    # post-breakout window: from the first close above the breakout level to the end
    above = [i for i, v in enumerate(c) if v > bl]
    s = above[0] if above else len(c) - 1
    post = df.iloc[s:]
    pc = post["close"]
    up = post["volume"][pc.diff() > 0].sum()
    dn = post["volume"][pc.diff() < 0].sum()
    ud = float(up / dn) if dn else float("inf")
    rng = (post["high"] - post["low"]).replace(0, 1e-9)
    cl = float(((post["close"] - post["low"]) / rng).mean())
    print(f"{label:15} {len(post):6}  {ud:11.2f}  {cl:9.2f}  {bl}")
