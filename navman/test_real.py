#!/usr/bin/env python3
"""
Integration test using real point images from test_data/.
Parses images via LLM, then runs generation with tight and wide range variants.
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

print(f"  Images: {[Path(p).name for p in image_paths]}")
points, failed = ingestion.parse_nav_images(image_paths, api_cfg)
if failed:
    print(f"  Warning: parse failed for {failed}")

# Deduplicate by ID, keep first occurrence
seen: dict[int, dict] = {}
for p in points:
    if p["id"] not in seen:
        seen[p["id"]] = p
points = sorted(seen.values(), key=lambda p: p["id"])

if not points:
    print("ERROR: no points parsed")
    sys.exit(1)

print(f"\n  {len(points)} unique points (IDs {points[0]['id']}–{points[-1]['id']}):")
for p in points:
    print(f"    #{p['id']:>4}: ({p['x']:.0f}, {p['y']:.0f})  {p.get('description', '')}")

# ---------------------------------------------------------------------------
# Pick special points by geographic spread (X axis = easting)
# ---------------------------------------------------------------------------

sorted_by_x = sorted(points, key=lambda p: p["x"])
start_pt  = sorted_by_x[0]
finish_pt = sorted_by_x[-1]
mid_pt    = sorted_by_x[len(sorted_by_x) // 2]

special_ids = {start_pt["id"], mid_pt["id"], finish_pt["id"]}
pool = [p for p in points if p["id"] not in special_ids]
pool_ids = [p["id"] for p in pool]
pt_map = {p["id"]: p for p in points}

special_duo  = {"start_id": start_pt["id"], "mid_id": mid_pt["id"], "finish_id": finish_pt["id"]}
special_solo = {"start_id": start_pt["id"], "finish_id": finish_pt["id"]}

print(f"\n  Start:  #{start_pt['id']} ({start_pt['x']:.0f}, {start_pt['y']:.0f})")
print(f"  Mid:    #{mid_pt['id']}  ({mid_pt['x']:.0f}, {mid_pt['y']:.0f})")
print(f"  Finish: #{finish_pt['id']} ({finish_pt['x']:.0f}, {finish_pt['y']:.0f})")

d_si = math.sqrt((mid_pt["x"]-start_pt["x"])**2 + (mid_pt["y"]-start_pt["y"])**2) / 1000
d_if = math.sqrt((finish_pt["x"]-mid_pt["x"])**2 + (finish_pt["y"]-mid_pt["y"])**2) / 1000
d_sf = math.sqrt((finish_pt["x"]-start_pt["x"])**2 + (finish_pt["y"]-start_pt["y"])**2) / 1000
print(f"\n  Direct distances:  start→mid {d_si:.1f}km  |  mid→finish {d_if:.1f}km  |  start→finish {d_sf:.1f}km")
print(f"  Pool: {len(pool)} points")

if len(pool) < 3:
    print("ERROR: not enough pool points (need at least 3)")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seg_lengths(pts):
    return [
        math.sqrt((pts[i+1]["x"] - pts[i]["x"])**2 + (pts[i+1]["y"] - pts[i]["y"])**2) / 1_000
        for i in range(len(pts) - 1)
    ]

def _cv(vals):
    mean = sum(vals) / len(vals)
    if mean == 0:
        return 0.0
    return math.sqrt(sum((v - mean)**2 for v in vals) / len(vals)) / mean

def _report(assignments, section_endpoints, min_km, max_km):
    errors = []
    cvs = []
    for a in assignments:
        s, e = section_endpoints[a["section"]]
        pts = [s] + [pt_map[pid] for pid in a["points"]] + [e]
        segs = _seg_lengths(pts)
        total = sum(segs)
        cv = _cv(segs)
        cvs.append(cv)
        in_bounds = min_km <= total <= max_km
        seg_str = "  ".join(f"{x:.2f}" for x in segs)
        flag = "✓" if in_bounds else "✗ OUT OF RANGE"
        print(f"  [{a['section']}] #{a['index']:>2}: {total:.2f}km  [{seg_str}]  CV={cv:.2f}  {flag}")
        if not in_bounds:
            errors.append(f"#{a['index']} {total:.2f}km out of [{min_km},{max_km}]")
        if set(a["points"]) & special_ids:
            errors.append(f"#{a['index']} contains special point!")
    unique = len({pid for a in assignments for pid in a["points"]})
    mean_cv = sum(cvs) / len(cvs) if cvs else 0
    print(f"  → {unique}/{len(pool)} pool points used  |  mean CV={mean_cv:.3f}")
    if errors:
        print(f"  FAIL: {errors}")
    else:
        print("  PASS")
    return mean_cv, errors

# ---------------------------------------------------------------------------
# Scenarios: tight margin vs. wide margin
# ---------------------------------------------------------------------------

N_PER_NAV = 3
N_PARTICIPANTS = min(8, len(pool) // N_PER_NAV)

# Suggest appropriate ranges based on actual section distances
DUO_TIGHT  = (round(d_si * 0.9, 1), round(d_si * 1.3, 1))
DUO_WIDE   = (round(d_si * 0.6, 1), round(d_si * 1.8, 1))
SOLO_TIGHT = (round(d_sf * 0.9, 1), round(d_sf * 1.3, 1))
SOLO_WIDE  = (round(d_sf * 0.6, 1), round(d_sf * 1.8, 1))

duo_scenarios = [
    ("tight", *DUO_TIGHT),
    ("wide",  *DUO_WIDE),
]
solo_scenarios = [
    ("tight", *SOLO_TIGHT),
    ("wide",  *SOLO_WIDE),
]

print(f"\n{'='*65}")
print(f"Running {N_PARTICIPANTS} participants, {N_PER_NAV} pts/path")

for label, min_km, max_km in duo_scenarios:
    print(f"\n--- DUO [{label}]  range=[{min_km}–{max_km}km] ---")
    try:
        assignments = algo.generate_assignments(
            points_db=points,
            filtered_point_ids=pool_ids,
            special=special_duo,
            n_per_nav=N_PER_NAV,
            min_km=min_km,
            max_km=max_km,
            n_participants=N_PARTICIPANTS,
        )
        _report(assignments, {
            "נה→נב": (pt_map[special_duo["start_id"]], pt_map[special_duo["mid_id"]]),
            "נב→נס": (pt_map[special_duo["mid_id"]], pt_map[special_duo["finish_id"]]),
        }, min_km, max_km)
    except ValueError as e:
        print(f"  SKIP: {e}")

for label, min_km, max_km in solo_scenarios:
    print(f"\n--- SOLO [{label}]  range=[{min_km}–{max_km}km] ---")
    try:
        assignments = algo.generate_solo_a_assignments(
            points_db=points,
            filtered_point_ids=pool_ids,
            special=special_solo,
            n_per_nav=N_PER_NAV,
            min_km=min_km,
            max_km=max_km,
            n_participants=N_PARTICIPANTS,
        )
        _report(assignments, {
            "נה→נס": (pt_map[special_solo["start_id"]], pt_map[special_solo["finish_id"]]),
        }, min_km, max_km)
    except ValueError as e:
        print(f"  SKIP: {e}")

print("\nDone.")
