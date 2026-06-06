#!/usr/bin/env python3
"""Re-runnable calibration harness for events.py — synthetic controls.

Controls:
  1. valid accumulation  : range → Spring → single-bar SOS → LPS    (all fire, has_event=True)
  2. out-of-order         : SOS then a *later* Spring                 (SOS dropped, has_event=False)
  3. multi-bar SOS + LPS  : 3-bar push clears resistance (no +4% bar);
                            LPS low sits well below the SOS high      (SOS kind=multi, LPS fires)
  4. uptrend              : steady markup                             (no range)

Run on the mini-PC (needs pandas):  .venv/bin/python tests/validate_events.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import pandas as pd
import events


def _bar(o, h, l, c, v):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _hover(rows, level, n, spread=0.6, vol=1_000_000):
    for _ in range(n):
        rows.append(_bar(level, level + spread, level - spread, level, vol))


def _ramp(rows, a, b, n, vol=1_000_000):
    for k in range(n):
        p = a + (b - a) * ((k + 1) / n)
        rows.append(_bar(p, p + 0.5, p - 0.5, p, vol))


def _base_range(rows):
    """Downtrend into a 3-wave trading range ~80–92 (distinct, time-separated band touches)."""
    for i in range(30):
        p = 100 - 18 * (i / 29)               # 100 → 82
        rows.append(_bar(p, p + 0.4, p - 0.4, p, 1_000_000))
    for lo, hi in [(80.5, 91.5), (80.7, 91.8), (80.6, 91.6)]:
        _hover(rows, lo, 4)                   # support visit
        _ramp(rows, lo, hi, 4)
        _hover(rows, hi, 4)                   # resistance visit
        _ramp(rows, hi, lo, 4)


def _df(rows):
    return pd.DataFrame(rows, index=pd.date_range("2026-01-01", periods=len(rows), freq="D"))


def acc_valid():
    rows = []
    _base_range(rows)
    rows.append(_bar(81, 82, 79.0, 82.0, 1_400_000))     # Spring (pierce <support, recover)
    rows.append(_bar(82, 84, 81.5, 83.5, 1_100_000))     # confirm
    rows.append(_bar(83.5, 85, 83, 84.0, 1_000_000))     # confirm
    rows.append(_bar(84, 89.5, 84, 88.8, 2_600_000))     # SOS (single bar, +5.7%, 2.6x vol)
    rows.append(_bar(88.8, 89, 86.5, 87.2, 800_000))
    rows.append(_bar(87.2, 88, 86.8, 87.6, 600_000))     # LPS (low vol, holds > mid, < SOS high)
    return _df(rows)


def out_of_order():
    rows = []
    _base_range(rows)
    rows.append(_bar(84, 89.5, 84, 88.8, 2_600_000))     # SOS first (earlier)
    _ramp(rows, 88.8, 81.0, 6)                           # falls back into the range
    _hover(rows, 81.0, 3)
    rows.append(_bar(81, 82, 79.0, 82.0, 1_400_000))     # Spring LATER (more recent than SOS)
    rows.append(_bar(82, 84, 81.5, 83.5, 1_100_000))     # confirm
    return _df(rows)


def multi_sos_lps():
    rows = []
    _base_range(rows)
    rows.append(_bar(86, 88, 85, 87.5, 1_400_000))       # 3-bar push, no single +4% bar
    rows.append(_bar(87.5, 90, 87, 89.5, 1_500_000))
    rows.append(_bar(89.5, 93.5, 89, 93.0, 1_600_000))   # clears resistance ~92 (multi SOS)
    rows.append(_bar(93, 93.2, 88.0, 89.5, 700_000))     # LPS: low 88 (well below SOS high 93.5), low vol
    return _df(rows)


def uptrend():
    rows = [_bar(p, p + 1, p - 1, p, 1_000_000) for p in (50 + 50 * (i / 119) for i in range(120))]
    return _df(rows)


def main() -> int:
    ok = True

    a = events.detect_events(acc_valid())
    c1 = all([a["range"], a["spring"], a["sos"], a["lps"], events.has_entry_event(a)])
    print(f"1. valid accumulation  → {'PASS' if c1 else 'FAIL'}  {events.event_summary(a)[1]}")
    if not c1:
        print("   ", a); ok = False

    b = events.detect_events(out_of_order())
    c2 = b["range"] and b["spring"] and b["sos"] is None and not events.has_entry_event(b)
    print(f"2. out-of-order (drop SOS) → {'PASS' if c2 else 'FAIL'}  sos={b['sos']} has_event={events.has_entry_event(b)}")
    if not c2:
        print("   ", b); ok = False

    m = events.detect_events(multi_sos_lps())
    c3 = m["sos"] and m["sos"].get("kind") == "multi" and m["lps"] and events.has_entry_event(m)
    print(f"3. multi-bar SOS + LPS → {'PASS' if c3 else 'FAIL'}  sos={m['sos']} lps={m['lps']}")
    if not c3:
        print("   ", m); ok = False

    u = events.detect_events(uptrend())
    c4 = u["range"] is None
    print(f"4. uptrend (no range)  → {'PASS' if c4 else 'FAIL'}  range={u['range']}")
    if not c4:
        ok = False

    print("\nRESULT:", "ALL PASS ✅" if ok else "FAILURES — recalibrate ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
