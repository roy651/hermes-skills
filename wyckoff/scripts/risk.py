#!/usr/bin/env python3
"""Deterministic risk overlay for held wyckoff positions (no LLM).

Per holding it computes a mechanical stop — the *tighter* of an ATR chandelier
(trails up with price) and a structure stop (recent swing low) — and maintains
persistent per-position state: entry date, baseline quantity, highest-high since
entry (for the trail), and the worst exit-ladder stage reached. This overlays the
LLM Wyckoff read: a breached stop is a hard exit regardless of phase.

Profit targets (Wyckoff P&F / measured-move) and the scale-in/out ladder are
layered on in later phases; this module owns stops, the trail, and the state.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

STATE_FILE = Path(__file__).parent.parent / "data" / "positions_state.json"
ATR_PERIOD = 14
CHANDELIER_MULT = 3.0
STRUCTURE_LOOKBACK = 20
STRUCTURE_BUFFER_ATR = 0.25      # cushion below the swing low so a wick fakeout doesn't trip the stop


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> float:
    h, l, c = df["high"], df["low"], df["close"]
    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    val = tr.rolling(period).mean().iloc[-1]
    return float(val) if pd.notna(val) else float(tr.mean())


def chandelier_stop(df: pd.DataFrame, highest_high: float, mult: float = CHANDELIER_MULT) -> float:
    return highest_high - mult * atr(df)


def structure_stop(df: pd.DataFrame, lookback: int = STRUCTURE_LOOKBACK) -> float:
    """Floor of the *prior* ``lookback`` sessions, minus a small ATR buffer.

    Today's bar is excluded on purpose: if it were included, a fresh low would just
    redefine the floor to today's low (close >= low >= floor, always), making the
    level unbreakable and emitting a tautological "touch" every new-low day. Using
    the prior sessions' low gives a fixed level today can genuinely close through;
    the buffer keeps a one-tick wick from counting as a break.
    """
    prior = df["low"].iloc[-lookback - 1:-1]
    floor = float(prior.min()) if len(prior) else float(df["low"].iloc[-lookback:].min())
    return floor - STRUCTURE_BUFFER_ATR * atr(df)


def assess(ticker: str, df: pd.DataFrame, qty: float, *, today: date | None = None,
           state: dict | None = None) -> dict:
    """Risk overlay for one holding; updates its persistent state in place.

    Pass a shared ``state`` dict to batch many holdings (caller saves once);
    omit it to load/save the state file here.
    """
    today = today or date.today()
    standalone = state is None
    st = load_state() if standalone else state
    rec = st.setdefault(ticker, {})

    price = float(df["close"].iloc[-1])
    highest_high = max(rec.get("highest_high", price), float(df["high"].iloc[-1]), price)

    if "entry_date" not in rec:                  # first time we see this holding
        rec["entry_date"] = today.isoformat()
        rec["baseline_qty"] = qty
        rec["max_stage"] = 0
    if qty > rec.get("baseline_qty", qty):       # user added -> re-commit, reset the exit ladder
        rec["baseline_qty"] = qty
        rec["max_stage"] = 0
    rec["highest_high"] = highest_high

    chand = chandelier_stop(df, highest_high)
    struct = structure_stop(df)
    stop, stop_type = (chand, "chandelier") if chand >= struct else (struct, "structure")  # tighter = higher

    if standalone:
        save_state(st)

    return {
        "ticker": ticker,
        "price": round(price, 2),
        "stop": round(stop, 2),
        "stop_type": stop_type,
        "atr": round(atr(df), 2),
        "distance_pct": round((price - stop) / price * 100, 2) if price else 0.0,
        "stop_hit": price < stop,
        "highest_high": round(highest_high, 2),
        "entry_date": rec["entry_date"],
        "baseline_qty": rec["baseline_qty"],
        "max_stage": rec["max_stage"],
    }


if __name__ == "__main__":  # self-test: synthetic data, shared state (no file writes)
    import numpy as np

    n = 60
    close = pd.Series(100 + np.arange(n) * 0.5)          # steady uptrend
    df = pd.DataFrame({"high": close + 0.8, "low": close - 0.8, "close": close,
                       "volume": pd.Series([1000] * n)})

    shared: dict = {}
    r = assess("TEST", df, qty=10, today=date(2026, 6, 22), state=shared)
    print(json.dumps(r, indent=2))
    assert r["stop"] < r["price"], "stop must sit below price in an uptrend"
    assert not r["stop_hit"], "no stop hit in a clean uptrend"
    assert r["stop_type"] in ("chandelier", "structure")
    assert shared["TEST"]["baseline_qty"] == 10 and shared["TEST"]["max_stage"] == 0

    assess("TEST", df, qty=15, today=date(2026, 6, 23), state=shared)   # adding
    assert shared["TEST"]["baseline_qty"] == 15, "adding should ratchet baseline up + reset ladder"
    print("\n[self-test OK]")
