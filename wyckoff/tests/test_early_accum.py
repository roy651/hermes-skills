#!/usr/bin/env python3
"""Regression tests for events.detect_early_accumulation — the early-accumulation lane.

Deterministic + offline. Guards the two defects fixed after the lane's first review:
  1. INVALIDATION is enforced — a base whose price has since CLOSED below the Selling-Climax
     low is discarded, not returned (previously the docstring promised this but nothing checked
     it, so already-broken names like ENPH were admitted as fresh candidates).
  2. The Automatic Rally is the FIRST rally leg (local), not the global post-SC maximum — so a
     name that has begun to recover is NOT suppressed, and the read no longer flickers run-to-run
     as later highs move the AR pointer past a valid Secondary Test.

Run:  .venv/bin/python tests/test_early_accum.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import pandas as pd
import events


def _base(tail_closes: list[float]) -> pd.DataFrame:
    """A clean markdown → Selling-Climax → Automatic-Rally → Secondary-Test sequence, then
    `tail_closes` appended after the ST. high/low bracket each close by ±0.5 unless the bar is
    the climax (wide range) or the test (a genuine dip to the SC low). SC low = 65."""
    rows: list[dict] = []

    def bar(c, v, hi=None, lo=None):
        rows.append({"open": c, "high": hi if hi is not None else c + 0.5,
                     "low": lo if lo is not None else c - 0.5, "close": c, "volume": v})

    for _ in range(30):                       # 1) flat baseline → sets the 20-bar vol/range average
        bar(100.0, 1_000_000)
    for c in range(99, 71, -1):               # 2) real markdown 99 → 72 (prior high ≫ SC low)
        bar(float(c), 1_000_000)
    bar(71.0, 3_000_000, hi=73.0, lo=65.0)    # 3) SELLING CLIMAX: wide range, 3× vol, closes off low
    for c in (72, 74, 76, 78):                # 4) AUTOMATIC RALLY off the SC low (>5% above 65)
        bar(float(c), 1_000_000)
    for c in (76, 74, 72, 70):                # 5) drift back down toward the SC low on avg volume
        bar(float(c), 1_000_000)
    bar(67.0, 500_000, lo=66.0)               # 6) SECONDARY TEST: dips to 66 (near 65) on light vol, holds
    for c in tail_closes:                     # 7) whatever happens after the base forms
        bar(float(c), 1_000_000)

    idx = pd.date_range("2026-01-01", periods=len(rows), freq="B")
    return pd.DataFrame(rows, index=idx)


def test_valid_base_hits():
    r = events.detect_early_accumulation(_base([68, 69, 68, 70, 69]))   # holds above SC low
    assert r is not None, "a clean SC→AR→ST base that holds must be detected"
    assert r["sc"]["low"] == 65.0, r
    assert r["ar"]["high"] >= 65.0 * 1.05, "AR must clear the SC low by the AR minimum"
    print("  ok  valid base holding above SC low -> HIT")


def test_recovered_base_still_hits():
    """Regression for defect #2: a later recovery rally (higher highs) must NOT suppress the base.
    Under the old global-argmax AR this returned None."""
    r = events.detect_early_accumulation(_base([70, 74, 80, 86, 92, 98]))   # strong recovery
    assert r is not None, "a base that has begun to recover must still be detected (local AR)"
    # the AR is the first rally leg (~78), NOT the 98 recovery high
    assert r["ar"]["high"] < 90.0, f"AR must be the first rally leg, not the later recovery high: {r['ar']}"
    print("  ok  recovered base -> HIT (AR stays local, not the recovery high)")


def test_broken_base_discarded():
    """Regression for defect #1: a close back below the SC low voids the base."""
    r = events.detect_early_accumulation(_base([66, 63, 61, 60, 59]))   # closes fall below 65
    assert r is None, f"a base that closed below its SC low must be discarded, got {r}"
    print("  ok  base that closed below the SC low -> None (invalidated)")


def test_no_climax_no_signal():
    rows = [{"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1_000_000}
            for _ in range(80)]                                          # flat, no climax
    df = pd.DataFrame(rows, index=pd.date_range("2026-01-01", periods=80, freq="B"))
    assert events.detect_early_accumulation(df) is None
    print("  ok  flat noise (no selling climax) -> None")


if __name__ == "__main__":
    test_valid_base_hits()
    test_recovered_base_still_hits()
    test_broken_base_discarded()
    test_no_climax_no_signal()
    print("\nall early-accumulation regression tests passed.")
