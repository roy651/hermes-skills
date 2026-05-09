#!/usr/bin/env python3
"""
Test nav_algorithm: generate paths and verify length bounds and segment evenness.
Run from navman/ with: .venv/bin/python test_gen.py
"""
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import nav_algorithm as algo

# ---------------------------------------------------------------------------
# Synthetic test data — ITM-scale coordinates (meters), 10km × 10km area
# ---------------------------------------------------------------------------

random.seed(42)

BASE_X, BASE_Y = 650_000, 3_400_000

def _pt(pid, x, y):
    return {"id": pid, "x": x, "y": y}

START  = _pt(1, BASE_X,          BASE_Y)
MID    = _pt(2, BASE_X + 5_000,  BASE_Y + 5_000)
FINISH = _pt(3, BASE_X + 10_000, BASE_Y + 10_000)

# 40 pool points spread across the area (avoiding edges so paths aren't degenerate)
POOL = [
    _pt(100 + i, BASE_X + random.randint(500, 9_500), BASE_Y + random.randint(500, 9_500))
    for i in range(40)
]

ALL_POINTS = [START, MID, FINISH] + POOL
POOL_IDS   = [p["id"] for p in POOL]
SPECIAL    = {"start_id": 1, "mid_id": 2, "finish_id": 3}

N_PER_NAV = 3

# Duo/solo_mid cover half-legs (~7km diagonal each); solo covers the full diagonal (~14km)
DUO_MIN, DUO_MAX   = 5.0, 14.0
SOLO_MIN, SOLO_MAX = 10.0, 22.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seg_lengths(pts):
    return [
        math.sqrt((pts[i+1]["x"] - pts[i]["x"])**2 + (pts[i+1]["y"] - pts[i]["y"])**2) / 1_000
        for i in range(len(pts) - 1)
    ]

def _cv(vals):
    """Coefficient of variation (std/mean). Lower = more evenly spaced."""
    mean = sum(vals) / len(vals)
    if mean == 0:
        return 0.0
    std = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
    return std / mean

def _check_assignments(assignments, pt_map, section_endpoints, min_km, max_km, label):
    """Assert length bounds and print evenness stats. Returns list of error strings."""
    errors = []
    cvs = []
    special_ids = {1, 2, 3}

    for a in assignments:
        s, e = section_endpoints[a["section"]]
        pts = [s] + [pt_map[pid] for pid in a["points"]] + [e]
        segs = _seg_lengths(pts)
        total = sum(segs)
        cv = _cv(segs)
        cvs.append(cv)

        in_bounds = min_km <= total <= max_km
        seg_str = "  ".join(f"{s:.2f}" for s in segs)
        flag = "✓" if in_bounds else "✗ OUT OF RANGE"
        print(f"  [{a['section']}] #{a['index']:>2}: {total:.2f}km  [{seg_str}]  CV={cv:.2f}  {flag}")

        if not in_bounds:
            errors.append(f"path {a['index']} {total:.2f}km not in [{min_km},{max_km}]")

        overlap = set(a["points"]) & special_ids
        if overlap:
            errors.append(f"path {a['index']} contains special point(s): {overlap}")

    unique = len({pid for a in assignments for pid in a["points"]})
    mean_cv = sum(cvs) / len(cvs) if cvs else 0
    print(f"  → {unique}/{len(POOL)} unique pool points used  |  mean CV={mean_cv:.3f} (0=perfectly even)")
    return errors

# ---------------------------------------------------------------------------
# Test: duo (start→mid and mid→finish)
# ---------------------------------------------------------------------------

def test_duo():
    print("\n=== DUO (8 participants) ===")
    pt_map = {p["id"]: p for p in ALL_POINTS}
    assignments = algo.generate_assignments(
        points_db=ALL_POINTS,
        filtered_point_ids=POOL_IDS,
        special=SPECIAL,
        n_per_nav=N_PER_NAV,
        min_km=DUO_MIN,
        max_km=DUO_MAX,
        n_participants=8,
    )
    assert len(assignments) == 8, f"Expected 8, got {len(assignments)}"
    endpoints = {"נה→נב": (START, MID), "נב→נס": (MID, FINISH)}
    errors = _check_assignments(assignments, pt_map, endpoints, DUO_MIN, DUO_MAX, "duo")
    assert not errors, f"FAIL: {errors}"
    print("  PASS")

# ---------------------------------------------------------------------------
# Test: solo (start→finish, no intermediate)
# ---------------------------------------------------------------------------

def test_solo():
    print("\n=== SOLO (6 participants) ===")
    pt_map = {p["id"]: p for p in ALL_POINTS}
    special = {"start_id": 1, "finish_id": 3}
    assignments = algo.generate_solo_a_assignments(
        points_db=ALL_POINTS,
        filtered_point_ids=POOL_IDS,
        special=special,
        n_per_nav=N_PER_NAV,
        min_km=SOLO_MIN,
        max_km=SOLO_MAX,
        n_participants=6,
    )
    assert len(assignments) == 6, f"Expected 6, got {len(assignments)}"
    endpoints = {"נה→נס": (START, FINISH)}
    errors = _check_assignments(assignments, pt_map, endpoints, SOLO_MIN, SOLO_MAX, "solo")
    assert not errors, f"FAIL: {errors}"
    print("  PASS")

# ---------------------------------------------------------------------------
# Test: solo_mid (both sections, each participant gets one path per section)
# ---------------------------------------------------------------------------

def test_solo_mid():
    print("\n=== SOLO_MID (6 participants, 2 pts SI / 3 pts IF) ===")
    pt_map = {p["id"]: p for p in ALL_POINTS}
    assignments = algo.generate_solo_mid_assignments(
        points_db=ALL_POINTS,
        filtered_point_ids=POOL_IDS,
        special=SPECIAL,
        n_si_pts=2,
        n_if_pts=3,
        min_km=DUO_MIN,
        max_km=DUO_MAX,
        n_participants=6,
    )
    assert len(assignments) == 12, f"Expected 12, got {len(assignments)}"
    endpoints = {"נה→נב": (START, MID), "נב→נס": (MID, FINISH)}
    errors = _check_assignments(assignments, pt_map, endpoints, DUO_MIN, DUO_MAX, "solo_mid")
    assert not errors, f"FAIL: {errors}"
    print("  PASS")

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_duo()
    test_solo()
    test_solo_mid()
    print("\nAll tests passed.")
