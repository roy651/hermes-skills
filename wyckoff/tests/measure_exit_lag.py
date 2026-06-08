#!/usr/bin/env python3
"""Review-4 §5.2 — quantify the cost of a quiet-top markup-pullback FP under the (c)+50%-size
posture: how many bars after the entry (LPS) does the DAILY EXIT-WATCH raise reduce/sell, and
what's the drawdown by then? Also reports a deterministic breakout-stop for a reproducible number.

LLM-based (uses the exit-watch prompt via the local proxy) → run as a one-off:
    .venv/bin/python tests/measure_exit_lag.py
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")
import data as md
import analysis as wyckoff
import events

STRIDE = 3       # evaluate the exit-watch every N forward bars
FWD = 48         # max forward bars to walk

# (label, ticker, entry_date or None=use current markup LPS)
CASES = [
    ("quiettop CVNA", "CVNA", "2021-09-15"),
    ("quiettop PTON", "PTON", "2021-02-15"),
    ("healthy  ROK",  "ROK",  None),
    ("healthy  EQIX", "EQIX", None),
]


def _load(tk, entry):
    if entry:
        ed = date.fromisoformat(entry)
        df = md.fetch_ohlcv(tk, start=(ed - timedelta(days=400)).isoformat(),
                            end=(ed + timedelta(days=150)).isoformat()).df
        edt = ed
    else:
        df = md.fetch_ohlcv(tk, days=252).df
        mp = events.detect_markup_pullback(df, enforce_effort=False)
        edt = date.fromisoformat(str(mp["lps"]["date"])[:10]) if mp else df.index[-1]
    return df, edt


print(f"case            entry        entry_px  breakout  det_stop@  exit_watch@  dd@exit  maxDD")
for label, tk, entry in CASES:
    try:
        df, edt = _load(tk, entry)
        ewin = df[df.index <= edt].tail(252)
        mp = events.detect_markup_pullback(ewin, enforce_effort=False)
        bl = mp["breakout_level"] if mp else None
        entry_px = float(ewin["close"].iloc[-1])
        fwd = df[df.index > edt]
        if not len(fwd):
            print(f"{label:15} {str(edt):10}  no forward bars"); continue
        # deterministic breakout-stop
        below = [k for k, v in enumerate(fwd["close"].values) if bl and v < bl]
        det_stop = below[0] if below else None
        # LLM exit-watch walk
        exit_k = None
        for k in range(0, min(len(fwd), FWD), STRIDE):
            win = df[df.index <= fwd.index[k]].tail(120)
            try:
                r = wyckoff.analyze(tk, win, held=True, name=tk, mode="exit")
            except Exception:
                continue
            if r.get("recommendation") in ("reduce", "sell"):
                exit_k = k
                break
        px_exit = float(fwd["close"].iloc[exit_k]) if exit_k is not None else None
        dd_exit = round((entry_px - px_exit) / entry_px * 100, 1) if px_exit is not None else None
        max_dd = round((entry_px - float(fwd["close"].min())) / entry_px * 100, 1)
        bl_s = f"{bl:8.2f}" if bl is not None else "    None"
        print(f"{label:15} {str(edt):10}  {entry_px:8.2f}  {bl_s}  "
              f"{str(det_stop):9}  {str(exit_k):11}  {str(dd_exit):7}  {max_dd}")
    except Exception as e:
        print(f"{label:15} ERR {e}")
