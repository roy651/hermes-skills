"""
Participant handling: score normalization, sorting, pairing, task assignment.
"""
import locale
import random
import re


# ---------------------------------------------------------------------------
# Score normalization
# ---------------------------------------------------------------------------

def normalize_score(score_raw: str) -> float:
    """
    Convert various score formats to float in [0, 100].

    Supported: integer, decimal, fraction (7/10), percentage (85%).
    """
    s = str(score_raw).strip().replace(" ", "")

    if "%" in s:
        return float(s.replace("%", ""))

    if "/" in s:
        parts = s.split("/")
        if len(parts) == 2:
            num = float(parts[0])
            den = float(parts[1])
            if den != 0:
                return (num / den) * 100.0

    return float(s)


# ---------------------------------------------------------------------------
# Sort
# ---------------------------------------------------------------------------

def sort_participants(participants: list[dict]) -> list[dict]:
    """
    Attach normalized score, sort descending (best → worst).
    Ties broken by Hebrew name (alphabetical).
    """
    enriched = []
    for p in participants:
        try:
            score = normalize_score(p["score_raw"])
        except (ValueError, ZeroDivisionError):
            score = 0.0
        enriched.append({**p, "score": score})

    enriched.sort(key=lambda p: (-p["score"], p["name"]))
    return enriched


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------

def pair_participants(sorted_participants: list[dict]) -> list[dict]:
    """
    Pair best with worst: index 0 with N-1, index 1 with N-2, etc.

    For odd count: last participant is marked as solo (pair_index set,
    partner is None) — coordinator is alerted separately.

    Returns list of pair dicts:
      {pair_index, p1: participant_dict, p2: participant_dict | None}
    """
    n = len(sorted_participants)
    pairs = []
    lo, hi = 0, n - 1
    pair_idx = 1

    while lo < hi:
        pairs.append({
            "pair_index": pair_idx,
            "p1": sorted_participants[lo],
            "p2": sorted_participants[hi],
        })
        lo += 1
        hi -= 1
        pair_idx += 1

    if lo == hi:  # odd participant
        pairs.append({
            "pair_index": pair_idx,
            "p1": sorted_participants[lo],
            "p2": None,
        })

    return pairs


# ---------------------------------------------------------------------------
# Task assignment
# ---------------------------------------------------------------------------

def assign_tasks(pairs: list[dict], assignments: list[dict]) -> list[dict]:
    """
    Assign one S→I task and one I→F task per pair.

    - Shuffle each section's task list independently (random distribution).
    - For each pair, randomly decide which participant gets S→I and which gets I→F.
    - For solo participants (p2 is None), assign only one task.

    Returns list of pairing dicts ready for export.
    """
    si_tasks = [a for a in assignments if "נה→" in a["section"]]
    if_tasks = [a for a in assignments if "נב→" in a["section"]]

    random.shuffle(si_tasks)
    random.shuffle(if_tasks)

    pairings = []
    task_idx = 0

    for pair in pairs:
        si = si_tasks[task_idx % len(si_tasks)] if si_tasks else None
        if_ = if_tasks[task_idx % len(if_tasks)] if if_tasks else None
        task_idx += 1

        p1 = pair["p1"]
        p2 = pair["p2"]

        # Randomly decide who gets S→I (better or weaker participant)
        if random.random() < 0.5:
            p1_task, p2_task = si, if_
        else:
            p1_task, p2_task = if_, si

        pairings.append({
            "pair_index": pair["pair_index"],
            "p1_name": p1["name"],
            "p1_score": p1.get("score", 0),
            "p1_task_index": p1_task["index"] if p1_task else None,
            "p1_section": p1_task["section"] if p1_task else "",
            "p1_points": p1_task["points"] if p1_task else [],
            "p1_length_km": p1_task["length_km"] if p1_task else 0,
            "p2_name": p2["name"] if p2 else "",
            "p2_score": p2.get("score", 0) if p2 else 0,
            "p2_task_index": p2_task["index"] if p2 and p2_task else None,
            "p2_section": p2_task["section"] if p2 and p2_task else "",
            "p2_points": p2_task["points"] if p2 and p2_task else [],
            "p2_length_km": p2_task["length_km"] if p2 and p2_task else 0,
        })

    return pairings


# ---------------------------------------------------------------------------
# Solo-A: one task per participant (single S→F section)
# ---------------------------------------------------------------------------

def assign_tasks_solo_a(participants: list[dict], assignments: list[dict]) -> list[dict]:
    """Random 1:1 assignment. Each participant gets one S→F task."""
    sorted_parts = sort_participants(participants)
    tasks = list(assignments)
    random.shuffle(tasks)

    result = []
    for i, p in enumerate(sorted_parts):
        task = tasks[i % len(tasks)]
        result.append({
            "index": i + 1,
            "name": p["name"],
            "score": p.get("score", 0),
            "task_index": task["index"],
            "section": task["section"],
            "points": task["points"],
            "length_km": task["length_km"],
        })
    return result


def format_solo_a_preview(assignments: list[dict], max_rows: int = 10) -> str:
    lines = [f"נוצרו {len(assignments)} שיבוצים יחידניים:"]
    for a in assignments[:max_rows]:
        pts_str = "→".join(str(pid) for pid in a["points"])
        lines.append(
            f"  {a['index']}. {a['name']} — משימה {a['task_index']}: {pts_str} ({a['length_km']:.2f} ק\"מ)"
        )
    if len(assignments) > max_rows:
        lines.append(f"  ... ועוד {len(assignments) - max_rows} שיבוצים")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Solo-mid: two tasks per participant (S→I and I→F, N paths per section)
# ---------------------------------------------------------------------------

def assign_tasks_solo_mid(participants: list[dict], assignments: list[dict]) -> list[dict]:
    """Random 1:1 assignment. Each participant gets one S→I task and one I→F task."""
    sorted_parts = sort_participants(participants)
    si_tasks = [a for a in assignments if "נה→" in a["section"]]
    if_tasks = [a for a in assignments if "נב→" in a["section"]]

    random.shuffle(si_tasks)
    random.shuffle(if_tasks)

    result = []
    for i, p in enumerate(sorted_parts):
        si = si_tasks[i % len(si_tasks)] if si_tasks else None
        ift = if_tasks[i % len(if_tasks)] if if_tasks else None
        result.append({
            "index": i + 1,
            "name": p["name"],
            "score": p.get("score", 0),
            "si_task_index": si["index"] if si else None,
            "si_section": si["section"] if si else "",
            "si_points": si["points"] if si else [],
            "si_length_km": si["length_km"] if si else 0,
            "if_task_index": ift["index"] if ift else None,
            "if_section": ift["section"] if ift else "",
            "if_points": ift["points"] if ift else [],
            "if_length_km": ift["length_km"] if ift else 0,
        })
    return result


def format_solo_mid_preview(assignments: list[dict], max_rows: int = 10) -> str:
    lines = [f"נוצרו {len(assignments)} שיבוצים יחידניים עם ביניים:"]
    for a in assignments[:max_rows]:
        si_pts = "→".join(str(pid) for pid in a["si_points"])
        if_pts = "→".join(str(pid) for pid in a["if_points"])
        lines.append(
            f"  {a['index']}. {a['name']} — "
            f"נה→נב: משימה {a['si_task_index']} ({si_pts}, {a['si_length_km']:.2f}ק\"מ) | "
            f"נב→נס: משימה {a['if_task_index']} ({if_pts}, {a['if_length_km']:.2f}ק\"מ)"
        )
    if len(assignments) > max_rows:
        lines.append(f"  ... ועוד {len(assignments) - max_rows} שיבוצים")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Preview (duo mode)
# ---------------------------------------------------------------------------

def format_pairings_preview(pairings: list[dict], max_rows: int = 10) -> str:
    lines = [f"נוצרו {len(pairings)} זוגות:"]
    for pr in pairings[:max_rows]:
        p1_pts = "→".join(str(i) for i in pr["p1_points"])
        p2_pts = "→".join(str(i) for i in pr["p2_points"]) if pr["p2_points"] else "—"
        lines.append(
            f"  זוג {pr['pair_index']}: "
            f"{pr['p1_name']} (משימה {pr['p1_task_index']}: {p1_pts}, {pr['p1_length_km']:.2f}ק\"מ) | "
            f"{pr['p2_name']} (משימה {pr['p2_task_index']}: {p2_pts}, {pr['p2_length_km']:.2f}ק\"מ)"
        )
    if len(pairings) > max_rows:
        lines.append(f"  ... ועוד {len(pairings) - max_rows} זוגות")
    return "\n".join(lines)
