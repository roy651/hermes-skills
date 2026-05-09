#!/usr/bin/env python3
"""
Integration test using real point images from test_data/.
For each path-length scenario, finds a start/finish pair with an appropriate
direct distance, then runs generation and reports filter size and CV.
Run from navman/ with: .venv/bin/python test_real.py
"""
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import ingestion
import nav_algorithm as algo

TEST_DATA = Path(__file__).parent / "test_data"

# ---------------------------------------------------------------------------
# CV reference table
# ---------------------------------------------------------------------------

def _print_cv_guide(n_segs=4):
    print("=== CV reference (what the numbers mean in practice) ===")
    print(f"  ({n_segs} segments, n_per_nav={n_segs-1})")
    print(f"  {'CV':>5}  {'Example segments (10km path)':35}  {'Ratio max:min':>14}")
    examples = {
        0.2: [2.0, 2.3, 2.7, 3.0],
        0.4: [1.2, 2.0, 3.0, 3.8],
        0.5: [1.0, 2.0, 3.0, 4.0],
        0.6: [0.5, 1.5, 3.5, 4.5],
        0.8: [0.3, 0.8, 3.8, 5.1],
    }
    for cv, segs in examples.items():
        seg_str = " / ".join(f"{s:.1f}" for s in segs)
        ratio = max(segs) / min(segs) if min(segs) > 0 else float("inf")
        print(f"  {cv:>5.1f}  {seg_str:35}  {ratio:>9.1f}×")
    print()

# ---------------------------------------------------------------------------
# API config
# ---------------------------------------------------------------------------

api_cfg = {
    "key": os.environ.get("VISION_API_KEY") or os.environ.get("OPENROUTER_API_KEY"),
    "url": os.environ.get("VISION_API_URL", "https://openrouter.ai/api/v1/chat/completions"),
    "model": (os.environ.get("VISION_MODELS") or "").split(",")[0].strip()
          or os.environ.get("VISION_MODEL", "qwen/qwen3.5-flash-02-23"),
    "models": [m.strip() for m in (os.environ.get("VISION_MODELS") or "").split(",") if m.strip()],
}

if not api_cfg["key"]:
    print("ERROR: no VISION_API_KEY or OPENROUTER_API_KEY in .env")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Parse point images
# ---------------------------------------------------------------------------

print("=== Parsing point images ===")
image_paths = sorted(str(p) for p in TEST_DATA.glob("points*.jpg"))
if not image_paths:
    print(f"ERROR: no points*.jpg found in {TEST_DATA}")
    sys.exit(1)

points_raw, failed = ingestion.parse_nav_images(image_paths, api_cfg)
if failed:
    print(f"  Warning: parse failed for {failed}")

seen: dict[int, dict] = {}
for p in points_raw:
    if p["id"] not in seen:
        seen[p["id"]] = p
all_points = sorted(seen.values(), key=lambda p: p["id"])

if not all_points:
    print("ERROR: no points parsed")
    sys.exit(1)

print(f"  {len(all_points)} unique points (IDs {all_points[0]['id']}–{all_points[-1]['id']})")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dist_km(a, b):
    return math.sqrt((a["x"] - b["x"])**2 + (a["y"] - b["y"])**2) / 1000

def _seg_lengths(pts):
    return [_dist_km(pts[i], pts[i+1]) for i in range(len(pts) - 1)]

def _cv(vals):
    mean = sum(vals) / len(vals)
    if mean == 0:
        return 0.0
    return math.sqrt(sum((v - mean)**2 for v in vals) / len(vals)) / mean

def find_pair(points, target_km):
    """Find the pair of points whose direct distance is closest to target_km."""
    best = None
    best_delta = float("inf")
    for i, p1 in enumerate(points):
        for p2 in points[i+1:]:
            d = _dist_km(p1, p2)
            delta = abs(d - target_km)
            if delta < best_delta:
                best_delta = delta
                best = (p1, p2, d)
    return best

def run_scenario(label, min_km, max_km, n_per_nav, n_participants):
    # Target direct distance: slightly below min_km so waypoints push it into range
    target_direct = min_km * 0.85
    pair = find_pair(all_points, target_direct)
    if pair is None:
        print(f"  SKIP: no suitable point pair found")
        return

    start_pt, finish_pt, actual_direct = pair
    special_ids = {start_pt["id"], finish_pt["id"]}
    pool = [p for p in all_points if p["id"] not in special_ids]
    pool_ids = [p["id"] for p in pool]
    pt_map = {p["id"]: p for p in all_points}

    print(f"  Start:  #{start_pt['id']} ({start_pt['x']:.0f}, {start_pt['y']:.0f})")
    print(f"  Finish: #{finish_pt['id']} ({finish_pt['x']:.0f}, {finish_pt['y']:.0f})")
    print(f"  Direct: {actual_direct:.2f}km  (target was {target_direct:.2f}km)  Pool: {len(pool)} pts")

    # Check feasibility filter
    dist_cache = algo.build_dist_cache(all_points)
    filtered = algo.filter_feasible_points(
        pool, start_pt, finish_pt, dist_cache, n_per_nav, min_km, max_km
    )
    print(f"  Filter: {len(filtered)}/{len(pool)} pts pass [{min_km}–{max_km}km]")

    if len(filtered) < n_per_nav:
        print("  SKIP: fewer feasible points than waypoints needed")
        return

    try:
        assignments = algo.generate_solo_a_assignments(
            points_db=all_points,
            filtered_point_ids=pool_ids,
            special={"start_id": start_pt["id"], "finish_id": finish_pt["id"]},
            n_per_nav=n_per_nav,
            min_km=min_km,
            max_km=max_km,
            n_participants=n_participants,
        )
    except ValueError as e:
        print(f"  SKIP: {e}")
        return

    errors = []
    cvs = []
    used_ids: set[int] = set()

    for a in assignments:
        pts = [start_pt] + [pt_map[pid] for pid in a["points"]] + [finish_pt]
        segs = _seg_lengths(pts)
        total = sum(segs)
        cv = _cv(segs)
        cvs.append(cv)
        used_ids.update(a["points"])
        in_bounds = min_km <= total <= max_km
        seg_str = "  ".join(f"{s:.2f}" for s in segs)
        flag = "✓" if in_bounds else "✗"
        print(f"    #{a['index']:>2}: {total:.2f}km  [{seg_str}]  CV={cv:.2f}  {flag}")
        if not in_bounds:
            errors.append(f"#{a['index']} out of bounds")
        if set(a["points"]) & special_ids:
            errors.append(f"#{a['index']} uses special point!")

    max_unique = n_participants * n_per_nav
    mean_cv = sum(cvs) / len(cvs) if cvs else 0
    print(f"  Coverage: {len(used_ids)}/{len(filtered)} filtered  [{len(used_ids)}/{max_unique} unique]")
    print(f"  Mean CV:  {mean_cv:.3f}")
    if errors:
        print(f"  FAIL: {errors}")
    else:
        print("  PASS")

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

_print_cv_guide()

N_PER_NAV = 3
N_PARTICIPANTS = 8

scenarios = [
    # label,     min_km, max_km
    ("3–4 km",    3.0,   4.0),
    ("4–5 km",    4.0,   5.0),
    ("5–7 km",    5.0,   7.0),
    ("6–8 km",    6.0,   8.0),
    ("7–9 km",    7.0,   9.0),
    ("9–11 km",   9.0,  11.0),   # original tight for reference
    ("11–13 km", 11.0,  13.0),   # best CV from previous run
]

print(f"SOLO test — {N_PER_NAV} waypoints, {N_PARTICIPANTS} participants")
print("(start/finish picked automatically to match each range)\n")

for label, min_km, max_km in scenarios:
    print(f"{'='*60}")
    print(f"Scenario: {label}  [{min_km}–{max_km}km]")
    run_scenario(label, min_km, max_km, N_PER_NAV, N_PARTICIPANTS)
    print()

print("Done.")
