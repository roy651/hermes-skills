#!/usr/bin/env python3
"""Deterministic Fibonacci retracement/extension grid — a confluence helper, NOT a trigger.

Takes a swing (high + low) — either passed explicitly or auto-detected from the lookback
window — and emits the retracement grid (potential support/resistance inside the swing) and
the extension grid (measured-move targets beyond the terminal pivot). Pure arithmetic: no LLM,
no Telegram, zero credits. The output is meant to be read alongside the Wyckoff structure and,
where a fib level lines up with a Wyckoff decision level, seeded into `watchlist_levels` so the
daily tripwire pings at that confluence. Per the arsenal discipline: fib levels only *confirm*
a Wyckoff signal or mark invalidation — they never generate an entry on their own.

Direction (auto or --dir) sets the interpretation:
  UP  swing (low → high): retracements are pullback SUPPORT below the high;
                          extensions are upside TARGETS above the high.
  DOWN swing (high → low): retracements are bounce RESISTANCE above the low;
                          extensions are downside TARGETS below the low.

Usage:
  fib.py <TICKER>                       auto-detect the dominant swing over 1y
  fib.py <TICKER> --lookback 400        widen the auto-detect window (trading days)
  fib.py <TICKER> --high 651.73 --low 365.74 [--dir down]   pin the swing by hand
  fib.py <TICKER> --json                machine-readable output
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")

import data as market_data

RETRACEMENTS = [0.236, 0.382, 0.5, 0.618, 0.786]
EXTENSIONS = [1.272, 1.618, 2.0, 2.618]
_SYM = {"USD": "$", "ILS": "₪"}


def _detect_swing(df) -> tuple[float, float, str]:
    """Dominant swing = the extreme high and low over the window; direction from their order
    (which pivot printed *last* is the terminal, so the leg runs toward it)."""
    hi = float(df["high"].max())
    lo = float(df["low"].min())
    hi_date = df["high"].idxmax()
    lo_date = df["low"].idxmin()
    direction = "up" if lo_date < hi_date else "down"
    return hi, lo, direction


def compute(hi: float, lo: float, direction: str) -> dict:
    if hi <= lo:
        raise ValueError(f"high ({hi}) must be greater than low ({lo})")
    rng = hi - lo
    if direction == "up":
        # pullback supports below the high; targets above it
        retr = {r: hi - r * rng for r in RETRACEMENTS}
        ext = {r: hi + (r - 1) * rng for r in EXTENSIONS}
        retr_label, ext_label = "pullback support", "upside target"
    else:
        # bounce resistances above the low; targets below it
        retr = {r: lo + r * rng for r in RETRACEMENTS}
        ext = {r: lo - (r - 1) * rng for r in EXTENSIONS}
        retr_label, ext_label = "bounce resistance", "downside target"
    return {
        "high": hi, "low": lo, "range": round(rng, 4), "direction": direction,
        "retracements": {r: round(v, 2) for r, v in retr.items()},
        # a deep down-extension can imply a sub-zero price — drop the nonsensical levels
        "extensions": {r: round(v, 2) for r, v in ext.items() if v > 0},
        "retr_label": retr_label, "ext_label": ext_label,
    }


def _bracket(price: float, grid: dict) -> dict:
    """Nearest fib level below and above the current price → a watchlist_levels suggestion."""
    levels = sorted(grid["retracements"].values()) + sorted(grid["extensions"].values())
    levels = sorted(set(levels))
    below = [v for v in levels if v <= price]
    above = [v for v in levels if v >= price]
    return {
        "support": below[-1] if below else None,
        "resistance": above[0] if above else None,
    }


def run(ticker: str, hi: float | None, lo: float | None, direction: str | None,
        lookback: int, as_json: bool) -> None:
    ticker = ticker.upper()
    td = market_data.fetch_ohlcv(ticker, days=lookback)
    df = td.df
    price = float(df["close"].iloc[-1])
    sym = _SYM.get(td.currency, td.currency + " ")

    if hi is not None and lo is not None:
        if direction is None:
            direction = "up" if lo <= price else "down"
        src = "manual"
    else:
        hi, lo, det_dir = _detect_swing(df)
        direction = direction or det_dir
        src = f"auto ({df.index[0]}→{df.index[-1]}, {len(df)}d)"

    grid = compute(hi, lo, direction)
    suggestion = _bracket(price, grid)

    if as_json:
        print(json.dumps({
            "ticker": ticker, "currency": td.currency, "price": round(price, 2),
            "swing_source": src, **grid, "watchlist_levels_suggestion": suggestion,
        }, indent=2, default=str))
        return

    out = [
        f"📐 Fibonacci grid — {ticker} ({td.name})",
        f"   price {sym}{price:.2f} · currency {td.currency}",
        f"   swing: {direction.upper()}  high {sym}{hi:.2f} → low {sym}{lo:.2f}  (range {grid['range']:g})  [{src}]",
        "",
        f"   Retracements — {grid['retr_label']}:",
    ]
    for r in RETRACEMENTS:
        v = grid["retracements"][r]
        here = "  ← price here" if abs(v - price) / price < 0.01 else ""
        out.append(f"     {r*100:5.1f}%   {sym}{v:.2f}{here}")
    out.append(f"   Extensions — {grid['ext_label']} (measured move beyond the pivot):")
    for r in EXTENSIONS:
        if r not in grid["extensions"]:
            continue
        v = grid["extensions"][r]
        out.append(f"     {r*100:5.1f}%   {sym}{v:.2f}")
    out.append("")
    sup = suggestion["support"]
    res = suggestion["resistance"]
    sup_s = f"{sym}{sup:.2f}" if sup is not None else "—"
    res_s = f"{sym}{res:.2f}" if res is not None else "—"
    out.append(f"   Nearest bracket → support {sup_s} · resistance {res_s}")
    out.append("   (confluence only — seed into watchlist_levels ONLY where it aligns with a Wyckoff decision level)")
    print("\n".join(out))


def main() -> None:
    ap = argparse.ArgumentParser(description="Fibonacci retracement/extension grid (no-LLM confluence helper)")
    ap.add_argument("ticker")
    ap.add_argument("--high", type=float, help="swing high (pin the swing by hand)")
    ap.add_argument("--low", type=float, help="swing low")
    ap.add_argument("--dir", choices=["up", "down"], help="swing direction (default: inferred)")
    ap.add_argument("--lookback", type=int, default=252, help="auto-detect window in trading days (default 252)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if (args.high is None) != (args.low is None):
        ap.error("--high and --low must be given together")
    run(args.ticker, args.high, args.low, args.dir, args.lookback, args.json)


if __name__ == "__main__":
    main()
