#!/usr/bin/env python3
"""Daily stop monitor — the genuinely-daily risk control (the weekly exit-watch is close-based and lags).

For each held position it recomputes the trailing stop via risk.py. The hard signal is a CLOSE through
the stop (an exit); an intraday low that dips to/through the stop but recovers into the close is reported
separately as an informational heads-up, not an exit. No LLM, no full Wyckoff analysis — just the
deterministic stop. It also keeps the trail (highest_high) current day-to-day, so this and the weekly
exit-watch share exactly one stop.

Usage:  stop_check.py [--dry-run]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")

import data as market_data
import holdings as portfolio
import risk
import notifier


def run(dry_run: bool = False) -> None:
    held = portfolio.load()
    if not held:
        print("[stop_check] no holdings", file=sys.stderr)
        return

    state = risk.load_state()
    breaches = []   # CLOSE through the stop — a hard exit signal
    touches = []    # intraday low dipped to/through the stop but recovered into the close — informational
    for ticker, h in held.items():
        try:
            td = market_data.fetch_ohlcv(ticker, days=120)
            rk = risk.assess(ticker, td.df, h["qty"], state=state)   # updates the trail (highest_high)
            sym = {"USD": "$", "ILS": "₪"}.get(td.currency, td.currency + " ")
            low = float(td.df["low"].iloc[-1])
            if rk["stop_hit"]:                                        # close through the stop — actionable
                breaches.append((ticker, sym, rk["price"], rk["stop"]))
            elif low <= rk["stop"]:                                  # intraday dip that recovered by the close
                touches.append((ticker, sym, round(low, 2), rk["stop"]))
        except Exception as e:
            print(f"[stop_check] {ticker}: {e}", file=sys.stderr)

    if not dry_run:
        risk.save_state(state)                                       # keep the trail current day-to-day

    if breaches or touches:
        lines = []
        if breaches:
            lines.append("🛑 <b>Wyckoff Stop Check</b> — stop breached on the close:")
            for tk, sym, px, st in breaches:
                lines.append(f"• <b>{tk}</b> — closed {sym}{px} below stop {sym}{st}")
        if touches:
            if breaches:
                lines.append("")
            lines.append("⚠️ <b>Intraday stop touch</b> (recovered by the close — heads-up, not an exit):")
            for tk, sym, lo, st in touches:
                lines.append(f"• <b>{tk}</b> — low {sym}{lo} ≤ stop {sym}{st}")
        lines.append("\n<i>The close-through-stop is the hard exit; the weekly exit-watch is close-based and may lag.</i>")
        msg = "\n".join(lines)
        print(msg) if dry_run else notifier.send(msg)
        print(f"[stop_check] {len(breaches)} breach(es), {len(touches)} touch(es)", file=sys.stderr)
    else:
        print("[stop_check] no breaches", file=sys.stderr)


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
