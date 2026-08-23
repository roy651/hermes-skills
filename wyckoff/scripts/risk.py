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


def structure_stop(df: pd.DataFrame, lookback: int = STRUCTURE_LOOKBACK,
                   entry_date: date | None = None) -> float | None:
    """Floor of the *prior* ``lookback`` sessions since entry, minus a small ATR buffer.

    Two bars are excluded on purpose:

    - **Today's** bar: if it were included, a fresh low would just redefine the floor
      to today's low (close >= low >= floor, always), making the level unbreakable and
      emitting a tautological "touch" every new-low day. Using the *prior* sessions' low
      gives a fixed level today can genuinely close through; the buffer keeps a one-tick
      wick from counting as a break.
    - **Pre-entry** bars: the lookback is clamped to sessions from ``entry_date`` onward.
      A stop derived from price action *before you owned the position* is not a stop on
      your trade — buy into a one-day crash and the old (higher) range would place the
      "structure" floor above your entry and fire an instant, nonsensical breach (this is
      exactly what put IBM's stop above its entry on a same-day add). Only the range you
      have actually held through defines your structure risk.

    Returns ``None`` when there is not yet a prior *owned* session (entered today), so the
    caller falls back to the chandelier trail until a real owned range exists.
    """
    owned = df.loc[df.index >= entry_date] if entry_date is not None else df
    prior = owned["low"].iloc[-lookback - 1:-1]
    if not len(prior):
        return None
    return float(prior.min()) - STRUCTURE_BUFFER_ATR * atr(df)


def assess(ticker: str, df: pd.DataFrame, qty: float, *, today: date | None = None,
           state: dict | None = None, manual_stop: float | None = None) -> dict:
    """Risk overlay for one holding; updates its persistent state in place.

    Pass a shared ``state`` dict to batch many holdings (caller saves once);
    omit it to load/save the state file here.

    ``manual_stop`` overrides the computed level outright. The mechanical stop is
    max(chandelier, structure) — deliberately the TIGHTER of the two — which sits a median ~3%
    below price and so trips on ordinary noise. When the decision is to give a position room
    on purpose, that intent has to be recorded somewhere the engine reads, not just agreed in
    conversation; otherwise the next run silently reverts to the tight level.
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

    if manual_stop is not None:
        # A level chosen deliberately replaces the ratchet outright — including downward,
        # which is the whole point of setting one by hand.
        stop, stop_type = float(manual_stop), "manual"
        rec["stop_floor"] = stop
    else:
        chand = chandelier_stop(df, highest_high)
        struct = structure_stop(df, entry_date=date.fromisoformat(rec["entry_date"]))
        if struct is None or chand >= struct:    # no owned range yet, or chandelier is tighter (higher)
            stop, stop_type = chand, "chandelier"
        else:
            stop, stop_type = struct, "structure"

        # RATCHET. A trailing stop must never loosen. Both inputs can fall on their own:
        # the chandelier is highest_high - 3*ATR, so a volatility spike alone widens it, and
        # the structure floor drops when a lower swing low rolls into the lookback. Either way
        # protection would relax at exactly the moment risk rises — and a breach could be
        # un-breached simply by the market getting noisier, which is what happened to XLF
        # (stop 57.20 -> 56.76 on an unchanged highest_high, purely from ATR 0.40 -> 0.55).
        floor = rec.get("stop_floor")
        if floor is not None and float(floor) > stop:
            stop, stop_type = float(floor), stop_type + "-held"
        rec["stop_floor"] = stop

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
        "stop_floor": round(rec.get("stop_floor", stop), 2),
    }


if __name__ == "__main__":  # self-test: synthetic data, shared state (no file writes)
    import numpy as np
    from datetime import timedelta

    n = 60
    close = 100 + np.arange(n) * 0.5                     # steady uptrend
    idx = [date(2026, 4, 1) + timedelta(days=i) for i in range(n)]   # date index, like real data
    df = pd.DataFrame({"high": close + 0.8, "low": close - 0.8, "close": close,
                       "volume": [1000] * n}, index=pd.Index(idx, name="Date"))

    shared: dict = {}
    r = assess("TEST", df, qty=10, today=idx[-1], state=shared)
    print(json.dumps(r, indent=2))
    assert r["stop"] < r["price"], "stop must sit below price in an uptrend"
    assert not r["stop_hit"], "no stop hit in a clean uptrend"
    assert r["stop_type"] in ("chandelier", "structure")
    assert shared["TEST"]["baseline_qty"] == 10 and shared["TEST"]["max_stage"] == 0

    assess("TEST", df, qty=15, today=idx[-1] + timedelta(days=1), state=shared)   # adding
    assert shared["TEST"]["baseline_qty"] == 15, "adding should ratchet baseline up + reset ladder"

    # Same-day entry into a crash: prior 20-session range sits ABOVE today's price. The structure
    # stop must NOT anchor to that pre-entry range — it falls back to the chandelier, below price.
    crash = df.copy()
    crash.iloc[-1, crash.columns.get_indexer(["high", "low", "close"])] = [92, 80, 82]   # gap-down day
    fresh: dict = {}
    rc = assess("CRASH", crash, qty=10, today=idx[-1], state=fresh)   # entry_date == today
    assert rc["stop_type"] == "chandelier", "same-day entry must fall back to chandelier, not pre-entry structure"
    assert rc["stop"] < rc["price"], f"a fresh-entry stop must sit below price, got {rc['stop']} vs {rc['price']}"
    assert not rc["stop_hit"], "a same-day entry into a dip must not self-trigger a breach"
    # The ratchet: a volatility spike must not widen an existing stop. Same highest_high,
    # a much wider ATR — the stop has to hold its previous level, not fall with the bands.
    calm: dict = {}
    r1 = assess("RATCHET", df, qty=10, today=idx[-1], state=calm)
    noisy = df.copy()
    noisy["high"] = noisy["close"] + 4.0            # ATR expands ~5x; highest_high is unchanged
    noisy["low"] = noisy["close"] - 4.0
    noisy.iloc[-1, noisy.columns.get_indexer(["high"])] = df["high"].iloc[-1]   # no new high
    r2 = assess("RATCHET", noisy, qty=10, today=idx[-1] + timedelta(days=1), state=calm)
    assert chandelier_stop(noisy, r1["highest_high"]) < r1["stop"], "test setup: ATR must widen the raw stop"
    assert r2["stop"] >= r1["stop"], f"stop loosened under volatility: {r1['stop']} -> {r2['stop']}"
    assert r2["stop_type"].endswith("-held"), f"expected a held stop, got {r2['stop_type']}"

    # A manual level still overrides in both directions.
    r3 = assess("RATCHET", df, qty=10, today=idx[-1] + timedelta(days=2), state=calm, manual_stop=1.0)
    assert r3["stop"] == 1.0 and r3["stop_type"] == "manual", "manual stop must override the ratchet"

    print("\n[self-test OK]")
