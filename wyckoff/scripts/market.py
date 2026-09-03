#!/usr/bin/env python3
"""Market snapshot for the daily brief: indexes, rates, credit, dollar, gold, vol, and the
sector ETFs ranked by the week. Mechanical only — the read layer interprets it. Uses
data.fetch_ohlcv so the numbers come from the same feed (and the same rate-limit backoff)
as every other section."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import data as market_data

BENCHMARKS = [("SPY", "SPY"), ("QQQ", "QQQ"), ("IWM", "IWM"), ("TLT", "TLT"), ("HYG", "HYG"),
              ("GLD", "GLD"), ("DXY", "DX-Y.NYB"), ("VIX", "^VIX")]
SECTORS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"]
HORIZONS = [("1d", 1), ("5d", 5), ("20d", 20)]


def _changes(symbol: str) -> dict | None:
    """{'last', '1d', '5d', '20d'} in percent, or None when the feed has no usable history."""
    try:
        close = market_data.fetch_ohlcv(symbol, days=60).df["close"].dropna()
    except Exception as e:
        print(f"[market] {symbol}: {e}", file=sys.stderr)
        return None
    if len(close) < 21:
        return None
    last = float(close.iloc[-1])
    out = {"last": last}
    for label, bars in HORIZONS:
        out[label] = (last / float(close.iloc[-1 - bars]) - 1) * 100
    return out


def build_section() -> str:
    rows = []
    for label, symbol in BENCHMARKS:
        c = _changes(symbol)
        if c is None:
            rows.append(f"{label:<4} {'n/a':>8}")
        else:
            rows.append(f"{label:<4} {c['last']:>8.2f} {c['1d']:>+6.1f} {c['5d']:>+6.1f} {c['20d']:>+6.1f}")

    ranked = []
    for symbol in SECTORS:
        c = _changes(symbol)
        if c is not None:
            ranked.append((symbol, c["5d"]))
    ranked.sort(key=lambda sc: sc[1], reverse=True)
    sectors = " · ".join(f"{s} {chg:+.1f}" for s, chg in ranked) or "n/a"

    return (f"🌐 <b>Market</b> — last · 1d · 5d · 20d (%)\n<pre>\n" + "\n".join(rows) + "\n</pre>\n"
            f"<b>Sectors, 5d:</b> {sectors}")


if __name__ == "__main__":
    print(build_section())
