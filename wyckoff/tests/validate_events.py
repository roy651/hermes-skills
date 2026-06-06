#!/usr/bin/env python3
"""Re-runnable validation harness for events.py — synthetic positive & negative controls.

Positive control: a hand-built accumulation series (range → Spring → SOS → LPS) — every
detector must fire. Negative control: a steady uptrend — no range should be found.

Run on the mini-PC (needs pandas):  .venv/bin/python tests/validate_events.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import pandas as pd
import events


def _bar(o, h, l, c, v):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def synth_accumulation() -> pd.DataFrame:
    rows = []
    # 1) downtrend into the base: 100 → 82 over 50 bars
    for i in range(50):
        p = 100 - (100 - 82) * (i / 49)
        rows.append(_bar(p, p + 0.5, p - 0.5, p, 1_000_000))
    # 2) trading range ~80–92, repeated touches of both bands
    pattern = [
        (80.5, 92.0, 81.0, 91.5),
        (91.0, 92.2, 86.0, 81.5),
        (81.2, 87.0, 80.2, 86.0),
        (86.0, 92.1, 85.0, 91.8),
        (91.5, 92.0, 82.0, 81.0),
        (81.0, 86.0, 80.3, 85.0),
    ]
    for k in range(54):
        o, h, l, c = pattern[k % len(pattern)]
        rows.append(_bar(o, h, l, c, 1_000_000))
    # 3) Spring — pierce below the support band, close back inside
    rows.append(_bar(81, 82.5, 79.0, 82.0, 1_400_000))
    rows.append(_bar(82, 84.0, 81.5, 83.5, 1_100_000))   # confirm
    rows.append(_bar(83.5, 85.0, 83.0, 84.0, 1_000_000))  # confirm
    # 4) SOS — +5% on expanded volume, above mid
    rows.append(_bar(84, 89.0, 84.0, 88.5, 2_600_000))
    # 5) LPS — quiet higher-low pullback near the SOS high
    rows.append(_bar(88.5, 89.0, 87.0, 87.5, 900_000))
    rows.append(_bar(87.5, 88.2, 87.0, 88.0, 500_000))
    rows.append(_bar(88.0, 89.0, 87.5, 88.2, 700_000))
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="D")
    return pd.DataFrame(rows, index=idx)


def synth_uptrend() -> pd.DataFrame:
    rows = [_bar(p, p + 1, p - 1, p, 1_000_000) for p in (50 + 50 * (i / 119) for i in range(120))]
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="D")
    return pd.DataFrame(rows, index=idx)


def main() -> int:
    acc = events.detect_events(synth_accumulation())
    score, labels = events.event_summary(acc)
    print(f"ACCUMULATION control → score={score} {labels}")
    checks = {k: acc[k] is not None for k in ("range", "spring", "sos", "lps")}
    for k, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}: {k}  {acc[k] if acc[k] else ''}")

    up = events.detect_events(synth_uptrend())
    neg_ok = up["range"] is None
    print(f"UPTREND control → range={up['range']}  {'PASS' if neg_ok else 'FAIL'} (expect None)")

    ok = all(checks.values()) and neg_ok
    print("\nRESULT:", "ALL PASS ✅" if ok else "FAILURES — recalibrate ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
