#!/usr/bin/env python3
"""Broker cash balance, in ILS — the part of net worth that holdings.json cannot see.

Kept in its own file (data/cash.json, gitignored) because holdings.json is overwritten by every
broker import. trade_log.py adjusts it on each logged fill: a sell adds the proceeds, a buy
subtracts the cost. USD fills are converted at the day's USD/ILS rate, which is not the rate the
broker used, so the balance drifts a little with every forex trade — re-anchor it with --set
from the broker's own figure whenever it has wandered.

Usage:
  cash.py                         show the balance
  cash.py --set 50000 [--note]     re-anchor to the broker's figure
  cash.py --adjust -500 [--note]  manual correction (deposit, withdrawal, fee, dividend)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Jerusalem")
_FILE = Path(__file__).parent.parent / "data" / "cash.json"
_HISTORY_KEEP = 200


def load() -> dict | None:
    """{"ils": float, "updated": iso, "history": [...]}, or None when cash was never set."""
    try:
        return json.loads(_FILE.read_text())
    except FileNotFoundError:
        return None


def balance_ils() -> float | None:
    c = load()
    return float(c["ils"]) if c else None


def _write(ils: float, kind: str, delta: float, note: str) -> dict:
    c = load() or {"ils": 0.0, "history": []}
    now = datetime.now(tz=TZ).isoformat(timespec="seconds")
    c["history"] = (c.get("history") or [])[-(_HISTORY_KEEP - 1):] + [
        {"at": now, "kind": kind, "delta": round(delta, 2), "balance": round(ils, 2), "note": note}]
    c["ils"] = round(ils, 2)
    c["updated"] = now
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, indent=1, ensure_ascii=False))
    tmp.replace(_FILE)
    return c


def set_balance(ils: float, note: str = "") -> dict:
    prev = balance_ils()
    return _write(ils, "set", ils - prev if prev is not None else 0.0, note)


def adjust(delta_ils: float, note: str = "") -> dict | None:
    """Apply a delta. Does nothing until a balance has been set, so a fill logged before cash
    tracking began cannot invent a balance out of one trade."""
    prev = balance_ils()
    if prev is None:
        return None
    return _write(prev + delta_ils, "adjust", delta_ils, note)


def main() -> None:
    p = argparse.ArgumentParser(description="Broker cash balance (ILS)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--set", type=float, help="re-anchor to the broker's balance")
    g.add_argument("--adjust", type=float, help="add (or subtract) an amount")
    p.add_argument("--note", default="")
    a = p.parse_args()
    if a.set is not None:
        c = set_balance(a.set, a.note)
    elif a.adjust is not None:
        c = adjust(a.adjust, a.note)
        if c is None:
            sys.exit("no balance yet — run cash.py --set <ILS> first")
    else:
        c = load()
        if c is None:
            print("cash not set")
            return
    print(f"cash: ₪{c['ils']:,.2f}  (updated {c.get('updated', '?')})")


if __name__ == "__main__":
    main()
