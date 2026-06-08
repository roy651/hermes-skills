#!/usr/bin/env python3
"""Tier 2 — curated REAL fixtures (committed CSV snapshots from build_fixtures.py).

The six trailing-snapshot fixtures were independently re-labeled and CONFIRMED as ground truth by
the reviewer (review 3 §1). The three historical fixtures are review-3 §4b adversarials:
  • SMCI_climax_240315   — a real climactic top; the effort filter must reject it (dist).
  • CVNA_failed_211231   — a real breakout that closed back below the level (dist).
  • CVNA_quiettop_210915 — a committed KNOWN false positive: a quiet-rally distribution top the
                           effort filter does NOT catch (open limitation; flagged, not hidden).

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
    # — reviewer-confirmed trailing snapshots (ground truth) —
    ("ROK_252d", "markup_pullback", {"markup_pullback": True, "has": True},
     "Cleared its ceiling, ~4.6% pullback holding above the breakout on lighter volume (effort 0.75×)."),
    ("EQIX_252d", "markup_pullback", {"markup_pullback": True, "has": True},
     "Post-breakout shallow pullback holding above the breakout (effort 0.76×)."),
    ("LLY_252d", "clear_not", {"range": False, "has": False},
     "Steady uptrend near highs, no base — no range, no entry event."),
    ("NKE_252d", "clear_not", {"range": False, "has": False},
     "Deep markdown (~46% off) — must not green-light any entry."),
    ("TDG_252d", "accumulation_unconfirmed", {"range": True, "spring": True, "has": False},
     "Range + Spring, no SOS — lone Spring stays Watch."),
    ("EIX_252d", "accumulation_unconfirmed", {"range": True, "spring": True, "has": False},
     "Range + Spring near highs, no confirming SOS."),
    # — review-3 §4b real adversarials —
    ("SMCI_climax_240315", "dist", {"markup_pullback": False, "has": False},
     "Climactic rally (effort ~4.1× the prior base) into the peak — the effort filter rejects it; SMCI later collapsed."),
    ("CVNA_failed_211231", "dist", {"markup_pullback": False, "has": False},
     "Broke out then closed back below the breakout level — a real failed breakout (no entry)."),
    ("CVNA_quiettop_210915", "known_fp", {"markup_pullback": True, "has": True},
     "KNOWN FP: a quiet-rally (effort ~0.9×) distribution top the lane fires on; CVNA then fell ~$66→$35. "
     "The effort filter does NOT catch quiet-rally tops — open limitation (see review-3 response)."),
]


def main() -> int:
    rows, failures, known_fps = [], [], []
    cm = {"TP": 0, "FP": 0, "FN": 0, "TN": 0}
    fp_on_negatives = 0   # FPs on dist / clear_not classes (the hard gate)

    for name, cls, exp, why in CASES:
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

        if cls == "known_fp":
            known_fps.append((name, why))
            rows.append((name, cls, "documents-FP" if ok else "FAIL"))
            continue

        if "has" in exp:
            lt, lp = exp["has"], got["has"]
            cm["TP" if (lt and lp) else "FN" if lt else "FP" if lp else "TN"] += 1
            if cls in ("dist", "clear_not") and lp:
                fp_on_negatives += 1
        rows.append((name, cls, "ok" if ok else "FAIL"))

    print("Tier 2 — REAL fixtures (6 reviewer-confirmed + 3 review-3 adversarials):")
    for name, cls, status in rows:
        print(f"  [{status:12}] {name:22} ({cls})")

    print(f"\nhas_entry_event confusion matrix (excl. known-FP): {cm}")
    print(f"false positives on dist / clear_not classes: {fp_on_negatives} (hard gate: 0)")

    if known_fps:
        print("\n⚠️  KNOWN OPEN FALSE POSITIVES (effort filter insufficient — flagged for follow-up):")
        for name, why in known_fps:
            print(f"   • {name}: {why}")

    passed = not failures and fp_on_negatives == 0
    if failures:
        print("\nMismatches vs labels:")
        for f in failures:
            print("  ", f)
    print("\nRESULT:", "matches labels (known FPs flagged) ✅" if passed else "MISMATCH ❌")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
