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
from itertools import permutations, product


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

def optimal_path(
    start: dict,
    end: dict,
    intermediates: list[dict],
    dist_cache: dict,
    min_km: float | None = None,
    max_km: float | None = None,
) -> tuple[float, list[int]]:
    """
    Return (length_km, ordered_intermediate_ids) for the best permutation.
    When min_km/max_km are given, prefers the most evenly spaced permutation
    within bounds over the shortest one. Falls back to shortest if none fit.
    """
    if not intermediates:
        return dist_cache.get((start["id"], end["id"]), _euclidean_km(start, end)), []

    best_len = float("inf")
    best_len_order = []
    best_even: tuple[float, float, list[int]] | None = None  # (variance, length, order)

    for perm in permutations(intermediates):
        pts = [start] + list(perm) + [end]
        segs = [
            dist_cache.get((pts[i]["id"], pts[i + 1]["id"]), _euclidean_km(pts[i], pts[i + 1]))
            for i in range(len(pts) - 1)
        ]
        total = sum(segs)
        order = [p["id"] for p in perm]

        if total < best_len:
            best_len = total
            best_len_order = order

        if min_km is not None and max_km is not None and min_km <= total <= max_km:
            mean = total / len(segs)
            var = sum((s - mean) ** 2 for s in segs) / len(segs)
            if best_even is None or var < best_even[0]:
                best_even = (var, total, order)

    if best_even is not None:
        return best_even[1], best_even[2]
    return best_len, best_len_order


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
# Ideal-position subset selection
# ---------------------------------------------------------------------------

def _ideal_sample(
    pool: list[dict],
    n: int,
    start: dict,
    end: dict,
    usage_count: dict,
    k: int = 3,
) -> list[dict]:
    """
    Select n points from pool by placing n ideal target positions evenly along
    the start→end straight line and picking the nearest available pool point
    to each target. Among the 9 nearest candidates per target, prefers the
    k least-used ones (random choice among them for variety across tries).
    """
    if len(pool) <= n:
        return pool[:]

    selected: list[dict] = []
    selected_ids: set[int] = set()

    for i in range(n):
        frac = (i + 1) / (n + 1)
        ideal_x = start["x"] + frac * (end["x"] - start["x"])
        ideal_y = start["y"] + frac * (end["y"] - start["y"])

        available = [p for p in pool if p["id"] not in selected_ids]
        available.sort(key=lambda p: (p["x"] - ideal_x) ** 2 + (p["y"] - ideal_y) ** 2)

        # Among 9 nearest, prefer least-used; randomize among top k
        candidates = available[:9]
        candidates.sort(key=lambda p: usage_count.get(p["id"], 0))
        if not candidates:
            break
        pick = random.choice(candidates[:k])
        selected.append(pick)
        selected_ids.add(pick["id"])

    return selected


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

            best_even_score = float("inf")
            best_even_result: tuple[float, list[int]] | None = None
            valid_found = 0

            if len(sorted_pool) < n_per_nav:
                break

            for _ in range(max_tries):
                subset = _ideal_sample(sorted_pool, n_per_nav, start, end, usage_count)
                if len(subset) < n_per_nav:
                    break

                length, ordered_ids = optimal_path(start, end, subset, dist_cache, lo, hi)

                if _is_valid_length(length, lo, hi):
                    pts = [start] + [pool_map[pid] for pid in ordered_ids] + [end]
                    segs = [
                        dist_cache.get((pts[i]["id"], pts[i + 1]["id"]), _euclidean_km(pts[i], pts[i + 1]))
                        for i in range(len(pts) - 1)
                    ]
                    mean = sum(segs) / len(segs) if segs else 1.0
                    score = sum((s - mean) ** 2 for s in segs)
                    if score < best_even_score:
                        best_even_score = score
                        best_even_result = (length, ordered_ids)
                    valid_found += 1
                    if valid_found >= 15:
                        break

            if best_even_result:
                length, ordered_ids = best_even_result
                for pid in ordered_ids:
                    usage_count[pid] = usage_count.get(pid, 0) + 1
                assignments.append({
                    "index": len(assignments) + 1,
                    "section": section,
                    "points": ordered_ids,
                    "length_km": round(length, 3),
                })
                found = True

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
    lines = [f"נוצרו {len(assignments)} משימות:"]
    for a in assignments:
        pts_str = " → ".join(str(pid) for pid in a["points"])
        lines.append(f"  {a['index']}. [{a['section']}] {pts_str} ({a['length_km']:.2f} ק\"מ)")
    by_section: dict[str, list] = {}
    for a in assignments:
        by_section.setdefault(a["section"], []).append(a)
    unique = len({pid for a in assignments for pid in a["points"]})
    section_summary = "  ".join(f"{sec}: {len(tasks)} משימות" for sec, tasks in sorted(by_section.items()))
    lines.append(f"\nסיכום: {section_summary} | {unique} נקודות ייחודיות")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Solo-A: single section, start → finish (no intermediate)
# ---------------------------------------------------------------------------

def generate_solo_a_assignments(
    points_db: list[dict],
    filtered_point_ids: list[int],
    special: dict,
    n_per_nav: int,
    min_km: float,
    max_km: float,
    n_participants: int,
) -> list[dict]:
    """
    Generate solo-A navigation assignments (no intermediate point).
    All n_participants paths are start → finish, section "נה→נס".
    """
    if n_participants < 1:
        raise ValueError("מספר המשתתפים חייב להיות לפחות 1")

    start_id = special.get("start_id")
    finish_id = special.get("finish_id")

    if not all([start_id, finish_id]):
        raise ValueError("נקודות מיוחדות (התחלה/סיום) לא הוגדרו")

    pt_map = {p["id"]: p for p in points_db}
    for label, pid in [("התחלה", start_id), ("סיום", finish_id)]:
        if pid not in pt_map:
            raise ValueError(f"נקודת {label} (ID={pid}) לא נמצאה בבסיס הנתונים")

    start_pt = pt_map[start_id]
    finish_pt = pt_map[finish_id]

    special_ids = {start_id, finish_id}
    pool = [pt_map[pid] for pid in filtered_point_ids if pid not in special_ids and pid in pt_map]

    if len(pool) < n_per_nav:
        raise ValueError(
            f"אין מספיק נקודות בבריכת הניווט ({len(pool)}) עבור {n_per_nav} נקודות למסלול"
        )

    dist_cache = build_dist_cache(list(pt_map.values()))

    pool_sf = filter_feasible_points(pool, start_pt, finish_pt, dist_cache, n_per_nav, min_km, max_km)
    if len(pool_sf) < n_per_nav:
        raise ValueError("לא נמצאו נקודות מתאימות למסלול נה→נס (בדוק את הגדרות המרחק)")

    usage: dict[int, int] = {}
    assignments = _greedy_assignment(
        pool_sf, start_pt, finish_pt, dist_cache, n_per_nav, min_km, max_km,
        n_participants, usage, "נה→נס",
    )

    if not assignments:
        raise ValueError("לא הצלחתי לייצר אף מסלול — בדוק את הגדרות המרחק")

    assignments, _ = _simulated_annealing(
        assignments, [],
        pool_sf, [],
        start_pt, finish_pt,
        None, None,
        dist_cache, min_km, max_km,
    )

    for i, a in enumerate(assignments):
        a["index"] = i + 1

    unique = len({pid for a in assignments for pid in a["points"]})
    print(
        f"[nav_algorithm] Generated {len(assignments)} solo-A assignments (נה→נס), "
        f"{unique} unique points used",
        file=sys.stderr,
    )
    return assignments


# ---------------------------------------------------------------------------
# Solo-mid: two sections, separate waypoint counts, N paths per section
# ---------------------------------------------------------------------------

def generate_solo_mid_assignments(
    points_db: list[dict],
    filtered_point_ids: list[int],
    special: dict,
    n_si_pts: int,
    n_if_pts: int,
    min_km: float,
    max_km: float,
    n_participants: int,
) -> list[dict]:
    """
    Generate solo-mid navigation assignments.
    n_participants S→I paths (n_si_pts waypoints each) +
    n_participants I→F paths (n_if_pts waypoints each).
    Each participant gets one path from each section.
    """
    if n_participants < 1:
        raise ValueError("מספר המשתתפים חייב להיות לפחות 1")

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

    special_ids = {start_id, mid_id, finish_id}
    pool = [pt_map[pid] for pid in filtered_point_ids if pid not in special_ids and pid in pt_map]

    if len(pool) < max(n_si_pts, n_if_pts):
        raise ValueError(
            f"אין מספיק נקודות בבריכת הניווט ({len(pool)}) עבור המסלולים המבוקשים"
        )

    dist_cache = build_dist_cache(list(pt_map.values()))

    pool_si = filter_feasible_points(pool, start_pt, mid_pt, dist_cache, n_si_pts, min_km, max_km)
    pool_if = filter_feasible_points(pool, mid_pt, finish_pt, dist_cache, n_if_pts, min_km, max_km)

    if len(pool_si) < n_si_pts:
        raise ValueError("לא נמצאו נקודות מתאימות למקטע נה→נב (בדוק את הגדרות המרחק)")
    if len(pool_if) < n_if_pts:
        raise ValueError("לא נמצאו נקודות מתאימות למקטע נב→נס (בדוק את הגדרות המרחק)")

    usage_si: dict[int, int] = {}
    usage_if: dict[int, int] = {}

    si_assignments = _greedy_assignment(
        pool_si, start_pt, mid_pt, dist_cache, n_si_pts, min_km, max_km,
        n_participants, usage_si, "נה→נב",
    )
    if_assignments = _greedy_assignment(
        pool_if, mid_pt, finish_pt, dist_cache, n_if_pts, min_km, max_km,
        n_participants, usage_if, "נב→נס",
    )

    if not si_assignments and not if_assignments:
        raise ValueError("לא הצלחתי לייצר אף מסלול — בדוק את הגדרות המרחק")

    si_assignments, if_assignments = _simulated_annealing(
        si_assignments, if_assignments,
        pool_si, pool_if,
        start_pt, mid_pt,
        mid_pt, finish_pt,
        dist_cache, min_km, max_km,
    )

    all_assignments = si_assignments + if_assignments
    for i, a in enumerate(all_assignments):
        a["index"] = i + 1

    unique = len({pid for a in all_assignments for pid in a["points"]})
    print(
        f"[nav_algorithm] Generated {len(all_assignments)} solo-mid assignments "
        f"({len(si_assignments)} נה→נב [{n_si_pts} pts], "
        f"{len(if_assignments)} נב→נס [{n_if_pts} pts]), "
        f"{unique} unique points used",
        file=sys.stderr,
    )
    return all_assignments


# ---------------------------------------------------------------------------
# Zone-based alternative: ratio-grouped random sampling (solo A only)
# ---------------------------------------------------------------------------

def generate_zone_based_assignments(
    points_db: list[dict],
    filtered_point_ids: list[int],
    special: dict,
    n_per_nav: int,
    min_km: float,
    max_km: float,
    n_participants: int,
    n_generate: int = 20_000,
) -> list[dict]:
    """
    Alternative to generate_solo_a_assignments using zone-based sampling.

    Strategy:
    1. Rank each pool point by its ratio along the start→finish axis:
         ratio = d(start,p) / (d(start,p) + d(p,finish))
    2. Sort by ratio; split into n_per_nav equal-size bands.
    3. Generate routes by sampling one point per band (enumerate all combos
       if total ≤ 100,000; otherwise random-sample n_generate routes).
    4. Visit waypoints in band order (natural A→B progression).
    5. Filter routes within [min_km, max_km]; score by CV of segment lengths.
    6. Select n_participants routes greedily: most new unique points first,
       break ties by lowest CV.
    """
    if n_participants < 1:
        raise ValueError("מספר המשתתפים חייב להיות לפחות 1")

    start_id = special.get("start_id")
    finish_id = special.get("finish_id")
    if not all([start_id, finish_id]):
        raise ValueError("נקודות מיוחדות (התחלה/סיום) לא הוגדרו")

    pt_map = {p["id"]: p for p in points_db}
    start_pt = pt_map[start_id]
    finish_pt = pt_map[finish_id]

    special_ids = {start_id, finish_id}
    pool = [pt_map[pid] for pid in filtered_point_ids if pid not in special_ids and pid in pt_map]

    if len(pool) < n_per_nav:
        raise ValueError(
            f"אין מספיק נקודות בבריכת הניווט ({len(pool)}) עבור {n_per_nav} נקודות למסלול"
        )

    dist_cache = build_dist_cache(list(pt_map.values()))

    # 1. Compute ratio for each pool point along start→finish axis
    def _ratio(p: dict) -> float:
        d_s = dist_cache.get((start_id, p["id"]), _euclidean_km(start_pt, p))
        d_f = dist_cache.get((p["id"], finish_id), _euclidean_km(p, finish_pt))
        total = d_s + d_f
        return d_s / total if total > 0 else 0.5

    pool_sorted = sorted(pool, key=_ratio)

    # 2. Split into n_per_nav equal bands
    n = len(pool_sorted)
    band_size = n / n_per_nav
    bands: list[list[dict]] = []
    for b in range(n_per_nav):
        lo_idx = round(b * band_size)
        hi_idx = round((b + 1) * band_size)
        bands.append(pool_sorted[lo_idx:hi_idx])

    # Drop empty bands — can happen with very small pools
    bands = [b for b in bands if b]
    if len(bands) < n_per_nav:
        raise ValueError("לא ניתן לחלק את הנקודות לקבוצות — הבריכה קטנה מדי")

    # 3. Generate candidate routes
    total_combos = 1
    for b in bands:
        total_combos *= len(b)

    def _route_stats(route: tuple[dict, ...]) -> tuple[float, float] | None:
        """Return (total_km, cv) for a route visited in band order, or None if out of bounds."""
        pts = [start_pt] + list(route) + [finish_pt]
        segs = [
            dist_cache.get((pts[i]["id"], pts[i + 1]["id"]), _euclidean_km(pts[i], pts[i + 1]))
            for i in range(len(pts) - 1)
        ]
        total = sum(segs)
        if not (min_km <= total <= max_km):
            return None
        mean = total / len(segs)
        cv = math.sqrt(sum((s - mean) ** 2 for s in segs) / len(segs)) / mean if mean > 0 else 0.0
        return total, cv

    # Each candidate: (tuple of point-ids in band order, total_km, cv)
    valid_routes: list[tuple[tuple[int, ...], float, float]] = []

    if total_combos <= 100_000:
        for combo in product(*bands):
            result = _route_stats(combo)
            if result is not None:
                ids = tuple(p["id"] for p in combo)
                valid_routes.append((ids, result[0], result[1]))
    else:
        seen_combos: set[tuple[int, ...]] = set()
        attempts = 0
        max_attempts = n_generate * 5
        while len(seen_combos) < n_generate and attempts < max_attempts:
            attempts += 1
            combo = tuple(random.choice(b) for b in bands)
            ids = tuple(p["id"] for p in combo)
            if ids in seen_combos:
                continue
            seen_combos.add(ids)
            result = _route_stats(combo)
            if result is not None:
                valid_routes.append((ids, result[0], result[1]))

    print(
        f"[zone_based] {len(valid_routes)} valid routes from "
        f"{'all ' + str(total_combos) if total_combos <= 100_000 else str(n_generate) + ' sampled'} combos",
        file=sys.stderr,
    )

    if not valid_routes:
        raise ValueError("לא נמצאו מסלולים תקינים בשיטת הזונות — בדוק את הגדרות המרחק")

    # 4. Greedy selection: max new unique points, break ties by lowest CV
    selected: list[dict] = []
    used_ids: set[int] = set()

    for slot in range(n_participants):
        best_route = None
        best_new = -1
        best_cv = float("inf")

        for ids, length, cv in valid_routes:
            new_pts = len(set(ids) - used_ids)
            if new_pts > best_new or (new_pts == best_new and cv < best_cv):
                best_new = new_pts
                best_cv = cv
                best_route = (ids, length, cv)

        if best_route is None:
            print(
                f"[zone_based] Warning: could not fill slot {slot + 1}",
                file=sys.stderr,
            )
            break

        ids, length, cv = best_route
        used_ids.update(ids)
        selected.append({
            "index": slot + 1,
            "section": "נה→נס",
            "points": list(ids),
            "length_km": round(length, 3),
        })

    unique = len({pid for a in selected for pid in a["points"]})
    print(
        f"[zone_based] Generated {len(selected)} assignments (נה→נס), "
        f"{unique} unique points used",
        file=sys.stderr,
    )
    return selected


# ---------------------------------------------------------------------------
# Auto: zone-based with greedy+SA fallback (solo A)
# ---------------------------------------------------------------------------

def generate_solo_a_assignments_auto(
    points_db: list[dict],
    filtered_point_ids: list[int],
    special: dict,
    n_per_nav: int,
    min_km: float,
    max_km: float,
    n_participants: int,
    n_generate: int = 20_000,
) -> list[dict]:
    """
    Generate solo-A assignments using zone-based sampling; falls back to
    greedy+SA if zone-based finds no valid routes (e.g. very tight ranges).
    """
    try:
        result = generate_zone_based_assignments(
            points_db, filtered_point_ids, special,
            n_per_nav, min_km, max_km, n_participants, n_generate,
        )
        if result:
            print("[nav_algorithm] solo-A: using zone-based", file=sys.stderr)
            return result
    except ValueError:
        pass

    print("[nav_algorithm] solo-A: zone-based failed, falling back to greedy+SA", file=sys.stderr)
    return generate_solo_a_assignments(
        points_db, filtered_point_ids, special,
        n_per_nav, min_km, max_km, n_participants,
    )
