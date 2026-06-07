#!/usr/bin/env python3
"""Tier 2 — curated REAL historical fixtures (committed CSV snapshots from build_fixtures.py).

⚠️ The labels below are PROPOSED (snapshot 2026-06-08) — picked from price structure + detector
output, NOT yet vetted by a human/reviewer. They are starting assertions; the reviewer should
confirm or amend them. Tier 1 (validate_events.py) remains the threshold-level ground truth.

Run:  .venv/bin/python tests/validate_events_tier2.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import events

FIX = Path(__file__).parent / "fixtures"

# (fixture, class, partial-expected, rationale). Only the keys in `expected` are asserted.
CASES = [
    ("ROK_252d", "markup_pullback", {"markup_pullback": True, "has": True},
     "Cleared its prior ceiling and pulled back ~4.6% holding above the breakout on lighter volume."),
    ("EQIX_252d", "markup_pullback", {"markup_pullback": True, "has": True},
     "Post-breakout shallow pullback (~4.2% off high) holding above the breakout level."),
    ("LLY_252d", "clear_not", {"range": False, "has": False},
     "Steady uptrend near highs (~3% off) with no horizontal base — expect no range and no entry event."),
    ("NKE_252d", "clear_not", {"range": False, "has": False},
     "Deep markdown (~46% off high), broken chart — must NOT green-light any entry (no false positive)."),
    ("TDG_252d", "accumulation_unconfirmed", {"range": True, "spring": True, "has": False},
     "Range + Spring but no SOS/LPS — a lone Spring must stay BORDERLINE/Watch, never STRONG."),
    ("EIX_252d", "accumulation_unconfirmed", {"range": True, "spring": True, "has": False},
     "Range + Spring near highs, no confirming SOS — same lone-Spring policy check."),
]


def main() -> int:
    rows, failures = [], []
    cm = {"TP": 0, "FP": 0, "FN": 0, "TN": 0}
    clear_not_fp = 0

    for name, cls, exp, _why in CASES:
        path = FIX / f"{name}.csv"
        if not path.exists():
            print(f"  MISSING {path.name} — run tests/build_fixtures.py on the mini-PC")
            failures.append(name)
            continue
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        ev = events.detect_events(df)
        got = {
            "range": ev["range"] is not None,
            "spring": ev["spring"] is not None,
            "sos": ev["sos"] is not None,
            "lps": ev["lps"] is not None,
            "markup_pullback": ev["markup_pullback"] is not None,
            "has": events.has_entry_event(ev),
        }
        ok = all(got[k] == exp[k] for k in exp)
        if not ok:
            failures.append((name, {k: (exp[k], got[k]) for k in exp if got[k] != exp[k]}))
        if "has" in exp:
            lt, lp = exp["has"], got["has"]
            cm["TP" if (lt and lp) else "FN" if lt else "FP" if lp else "TN"] += 1
            if cls == "clear_not" and lp:
                clear_not_fp += 1
        rows.append((name, cls, "ok" if ok else "FAIL"))

    print("Tier 2 — REAL fixtures (PROPOSED labels; reviewer to vet). Snapshot 2026-06-08:")
    for name, cls, status in rows:
        print(f"  [{status:4}] {name:12} ({cls})")
    print(f"\nhas_entry_event confusion matrix: {cm}")
    print(f"false positives on broken/clear_not charts: {clear_not_fp} (target 0)")

    passed = not failures and clear_not_fp == 0
    if failures:
        print("\nMismatches vs PROPOSED labels (re-examine the label OR the detector):")
        for f in failures:
            print("  ", f)
    print("\nRESULT:", "matches proposed labels ✅" if passed else "MISMATCH ❌")
    print("\nGAPS for the reviewer to supply real examples (covered today only by Tier-1 synthetic):")
    print("  • a clean range→Spring→SOS→LPS 'clear STRONG' chain")
    print("  • an explicit failed-breakout (broke out, then collapsed back below the breakout)")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
