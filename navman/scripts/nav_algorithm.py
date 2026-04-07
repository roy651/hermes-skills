"""
Navigation assignment generator.

Algorithm:
1. Precompute all pairwise ITM Euclidean distances (meters → km).
2. For a given set of n intermediate points between fixed start and end,
   find the optimal ordering via brute-force permutations (n ≤ 5 → max 120).
3. Greedy seed: for each assignment slot, pick n least-used valid points.
4. Simulated annealing: swap points between assignments to maximize coverage.

Output: list of assignment dicts with section, ordered point IDs, and length_km.
"""
import math
import random
import sys
from itertools import permutations


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------

def _euclidean_km(p1: dict, p2: dict) -> float:
    dx = p2["x"] - p1["x"]
    dy = p2["y"] - p1["y"]
    return math.sqrt(dx * dx + dy * dy) / 1000.0


def build_dist_cache(points: list[dict]) -> dict:
    """Return dict keyed by (id1, id2) → distance_km (symmetric)."""
    cache = {}
    for i, p1 in enumerate(points):
        for p2 in points[i:]:
            d = _euclidean_km(p1, p2)
            cache[(p1["id"], p2["id"])] = d
            cache[(p2["id"], p1["id"])] = d
    return cache


# ---------------------------------------------------------------------------
# Optimal path length for fixed start/end + n intermediate points
# ---------------------------------------------------------------------------

def optimal_path(start: dict, end: dict, intermediates: list[dict], dist_cache: dict) -> tuple[float, list[int]]:
    """
    Return (min_length_km, ordered_intermediate_ids) for the best permutation.
    With n ≤ 5 intermediates, brute force is fine (max 120 permutations).
    """
    if not intermediates:
        return dist_cache.get((start["id"], end["id"]), _euclidean_km(start, end)), []

    best_len = float("inf")
    best_order = []

    for perm in permutations(intermediates):
        pts = [start] + list(perm) + [end]
        total = sum(
            dist_cache.get((pts[i]["id"], pts[i + 1]["id"]), _euclidean_km(pts[i], pts[i + 1]))
            for i in range(len(pts) - 1)
        )
        if total < best_len:
            best_len = total
            best_order = [p["id"] for p in perm]

    return best_len, best_order


# ---------------------------------------------------------------------------
# Feasibility filter
# ---------------------------------------------------------------------------

def _is_valid_length(length_km: float, min_km: float, max_km: float) -> bool:
    return min_km <= length_km <= max_km


def filter_feasible_points(
    pool: list[dict],
    start: dict,
    end: dict,
    dist_cache: dict,
    n_per_nav: int,
    min_km: float,
    max_km: float,
    n_samples: int = 300,
) -> list[dict]:
    """
    Return points from pool for which at least one valid n-subset exists
    (path within [min_km, max_km]).

    Uses random sampling when pool is large to avoid combinatorial explosion.
    """
    if len(pool) <= n_per_nav:
        return pool

    feasible = set()
    pool_ids = [p["id"] for p in pool]
    pool_map = {p["id"]: p for p in pool}

    # For each point, check if it can appear in at least one valid subset
    for target in pool:
        others = [p for p in pool if p["id"] != target["id"]]
        if len(others) < n_per_nav - 1:
            feasible.add(target["id"])
            continue

        found = False
        for _ in range(n_samples):
            companions = random.sample(others, min(n_per_nav - 1, len(others)))
            subset = [target] + companions
            length, _ = optimal_path(start, end, subset, dist_cache)
            if _is_valid_length(length, min_km, max_km):
                found = True
                break

        if found:
            feasible.add(target["id"])

    return [p for p in pool if p["id"] in feasible]


# ---------------------------------------------------------------------------
# Greedy seed
# ---------------------------------------------------------------------------

def _greedy_assignment(
    pool: list[dict],
    start: dict,
    end: dict,
    dist_cache: dict,
    n_per_nav: int,
    min_km: float,
    max_km: float,
    n_slots: int,
    usage_count: dict,
    section: str,
    max_tries: int = 600,
) -> list[dict]:
    """
    Greedily fill n_slots assignments for a given section.
    Prefers least-used points; expands range by 10% if no valid subset found.
    """
    assignments = []
    pool_map = {p["id"]: p for p in pool}

    for slot in range(n_slots):
        # Sort pool by usage (least used first), break ties randomly
        sorted_pool = sorted(pool, key=lambda p: (usage_count.get(p["id"], 0), random.random()))

        found = False
        # Try increasingly relaxed ranges
        for relaxation in [0, 0.1, 0.2, 0.3]:
            lo = min_km * (1 - relaxation)
            hi = max_km * (1 + relaxation)

            for _ in range(max_tries):
                # Pick candidates weighted by low usage
                candidates = sorted_pool[:max(n_per_nav * 3, 15)]
                if len(candidates) < n_per_nav:
                    candidates = sorted_pool
                if len(candidates) < n_per_nav:
                    break

                subset = random.sample(candidates, n_per_nav)
                length, ordered_ids = optimal_path(start, end, subset, dist_cache)

                if _is_valid_length(length, lo, hi):
                    for pid in ordered_ids:
                        usage_count[pid] = usage_count.get(pid, 0) + 1
                    assignments.append({
                        "index": len(assignments) + 1,
                        "section": section,
                        "points": ordered_ids,
                        "length_km": round(length, 3),
                    })
                    found = True
                    break

            if found:
                break

        if not found:
            print(
                f"[nav_algorithm] Warning: could not fill slot {slot+1} for {section} after relaxation",
                file=sys.stderr,
            )

    return assignments


# ---------------------------------------------------------------------------
# Simulated Annealing refinement
# ---------------------------------------------------------------------------

def _simulated_annealing(
    si_assignments: list[dict],
    if_assignments: list[dict],
    si_pool: list[dict],
    if_pool: list[dict],
    si_start: dict,
    si_end: dict,
    if_start: dict,
    if_end: dict,
    dist_cache: dict,
    min_km: float,
    max_km: float,
    iterations: int = 2000,
) -> tuple[list[dict], list[dict]]:
    """
    Improve coverage by swapping points between assignments using SA.
    Objective: maximize number of unique points used across all assignments.
    """
    si_pool_map = {p["id"]: p for p in si_pool}
    if_pool_map = {p["id"]: p for p in if_pool}

    def coverage(assignments):
        return len({pid for a in assignments for pid in a["points"]})

    def total_coverage():
        return coverage(si_assignments) + coverage(if_assignments)

    current_obj = total_coverage()
    T = 1.0
    T_min = 0.005
    alpha = (T_min / T) ** (1.0 / max(iterations, 1))

    for _ in range(iterations):
        # Pick a random section to mutate
        use_si = bool(si_assignments) and (not if_assignments or random.random() < 0.5)
        assignments = si_assignments if use_si else if_assignments
        pool_map = si_pool_map if use_si else if_pool_map
        start = si_start if use_si else if_start
        end = si_end if use_si else if_end

        if not assignments:
            T *= alpha
            continue

        a_idx = random.randrange(len(assignments))
        a = assignments[a_idx]
        if not a["points"]:
            T *= alpha
            continue

        pt_idx = random.randrange(len(a["points"]))
        old_id = a["points"][pt_idx]

        # Replace with a random pool point not already in this assignment
        candidates = [pid for pid in pool_map if pid not in a["points"]]
        if not candidates:
            T *= alpha
            continue

        new_id = random.choice(candidates)
        new_ids_list = a["points"][:]
        new_ids_list[pt_idx] = new_id
        new_pts = [pool_map[pid] for pid in new_ids_list]

        length, ordered_ids = optimal_path(start, end, new_pts, dist_cache)

        if _is_valid_length(length, min_km, max_km):
            new_a = {**a, "points": ordered_ids, "length_km": round(length, 3)}
            # Compute new objective
            old_a = assignments[a_idx]
            assignments[a_idx] = new_a
            new_obj = total_coverage()

            delta = new_obj - current_obj
            if delta >= 0 or random.random() < math.exp(delta / T):
                current_obj = new_obj  # accept
            else:
                assignments[a_idx] = old_a  # revert

        T *= alpha

    # Re-index
    for i, a in enumerate(si_assignments):
        a["index"] = i + 1
    for i, a in enumerate(if_assignments):
        a["index"] = i + 1

    return si_assignments, if_assignments


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_assignments(
    points_db: list[dict],
    filtered_point_ids: list[int],
    special: dict,
    n_per_nav: int,
    avg_km: float,
    min_km: float,
    max_km: float,
    n_participants: int,
) -> list[dict]:
    """
    Generate navigation assignments.

    Returns a flat list of assignment dicts:
      {index, section, points:[id,...], length_km}

    Half are section "מ-ס לנקה" (start→intermediate),
    half are section "מנקה ל-ס" (intermediate→finish).

    Total assignments = n_participants.
    """
    if n_participants < 2:
        raise ValueError("מספר המשתתפים חייב להיות לפחות 2")

    start_id = special.get("start_id")
    mid_id = special.get("mid_id")
    finish_id = special.get("finish_id")

    if not all([start_id, mid_id, finish_id]):
        raise ValueError("נקודות מיוחדות (התחלה/אמצע/סיום) לא הוגדרו")

    pt_map = {p["id"]: p for p in points_db}
    for label, pid in [("התחלה", start_id), ("אמצע", mid_id), ("סיום", finish_id)]:
        if pid not in pt_map:
            raise ValueError(f"נקודת {label} (ID={pid}) לא נמצאה בבסיס הנתונים")

    start_pt = pt_map[start_id]
    mid_pt = pt_map[mid_id]
    finish_pt = pt_map[finish_id]

    # Build pool (exclude special points)
    special_ids = {start_id, mid_id, finish_id}
    pool_ids = [pid for pid in filtered_point_ids if pid not in special_ids]
    pool = [pt_map[pid] for pid in pool_ids if pid in pt_map]

    if len(pool) < n_per_nav:
        raise ValueError(
            f"אין מספיק נקודות בבריכת הניווט ({len(pool)}) עבור {n_per_nav} נקודות למסלול"
        )

    # Precompute distances across all relevant points
    all_pts = list(pt_map.values())
    dist_cache = build_dist_cache(all_pts)

    # Split assignments: half S→I, half I→F
    n_si = n_participants // 2
    n_if = n_participants - n_si  # handles odd

    # Feasibility filter per section
    pool_si = filter_feasible_points(pool, start_pt, mid_pt, dist_cache, n_per_nav, min_km, max_km)
    pool_if = filter_feasible_points(pool, mid_pt, finish_pt, dist_cache, n_per_nav, min_km, max_km)

    if len(pool_si) < n_per_nav:
        raise ValueError(
            f"לא נמצאו נקודות מתאימות למקטע נה→נב (ייתכן שמרחקי המסלול קצרים מדי/ארוכים מדי)"
        )
    if len(pool_if) < n_per_nav:
        raise ValueError(
            f"לא נמצאו נקודות מתאימות למקטע נב→נס (ייתכן שמרחקי המסלול קצרים מדי/ארוכים מדי)"
        )

    # Greedy seed
    usage_si: dict[int, int] = {}
    usage_if: dict[int, int] = {}

    si_assignments = _greedy_assignment(
        pool_si, start_pt, mid_pt, dist_cache, n_per_nav, min_km, max_km,
        n_si, usage_si, "נה→נב",
    )
    if_assignments = _greedy_assignment(
        pool_if, mid_pt, finish_pt, dist_cache, n_per_nav, min_km, max_km,
        n_if, usage_if, "נב→נס",
    )

    if not si_assignments and not if_assignments:
        raise ValueError("לא הצלחתי לייצר אף מסלול — בדוק את הגדרות המרחק")

    # Simulated annealing refinement
    si_assignments, if_assignments = _simulated_annealing(
        si_assignments, if_assignments,
        pool_si, pool_if,
        start_pt, mid_pt,
        mid_pt, finish_pt,
        dist_cache, min_km, max_km,
        iterations=2000,
    )

    all_assignments = si_assignments + if_assignments
    # Global re-index
    for i, a in enumerate(all_assignments):
        a["index"] = i + 1

    unique = len({pid for a in all_assignments for pid in a["points"]})
    print(
        f"[nav_algorithm] Generated {len(all_assignments)} assignments "
        f"({len(si_assignments)} נה→נב, {len(if_assignments)} נב→נס), "
        f"{unique} unique points used",
        file=sys.stderr,
    )

    return all_assignments


def format_assignments_preview(assignments: list[dict], points_db: list[dict]) -> str:
    pt_map = {p["id"]: p for p in points_db}
    lines = [f"נוצרו {len(assignments)} משימות:"]
    for a in assignments:
        pts_str = " → ".join(str(pid) for pid in a["points"])
        lines.append(f"  {a['index']}. [{a['section']}] {pts_str} ({a['length_km']:.2f} ק\"מ)")
    si = [a for a in assignments if "נה→" in a["section"]]
    fi = [a for a in assignments if "נב→" in a["section"]]
    unique = len({pid for a in assignments for pid in a["points"]})
    lines.append(f"\nסיכום: {len(si)} מקטעי נה→נב, {len(fi)} מקטעי נב→נס, {unique} נקודות ייחודיות")
    return "\n".join(lines)
