#!/usr/bin/env python3
"""Weekly digest of forward-dated Hermes jobs — so a checkpoint never arrives as a surprise.

A dated checkpoint set weeks ago is invisible until the morning it fires. That happened on
2026-08-24: a one-shot created on 2026-08-05 for a legislative deadline went off alongside two
unrelated digests, and nothing anywhere listed it in between. Setting forward jobs on known
catalysts is good practice; it only works if the pending ones are visible while there is still
time to act on them.

This lives in wyckoff because that is where the digest plumbing and the notifier are, even
though it reads Hermes' own job store. It is deliberately read-only.

Recurring daily/weekly jobs are excluded — they are noise here. What is reported is anything
that fires on a *specific date*: one-shots, and cron expressions pinned to a day-of-month or
a month.

Usage:  checkpoints.py [--days N] [--dry-run]
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
import notifier

JOBS_FILE = Path.home() / ".hermes" / "cron" / "jobs.json"
TZ = ZoneInfo("Asia/Jerusalem")
HORIZON_DAYS = 45
IMMINENT_DAYS = 7


def _field_matches(spec: str, value: int) -> bool:
    """One cron field against one value. Handles *, */n, a-b, and comma lists."""
    if spec == "*":
        return True
    for part in spec.split(","):
        if part.startswith("*/"):
            if value % int(part[2:]) == 0:
                return True
        elif "-" in part:
            lo, hi = (int(x) for x in part.split("-", 1))
            if lo <= value <= hi:
                return True
        elif part.isdigit() and int(part) == value:
            return True
    return False


def next_fire(expr: str, after: datetime) -> datetime | None:
    """Next UTC firing of a 5-field cron expression, searched day by day.

    Day-resolution search is enough: every job here fires at most once a day, so once the
    date matches, the hour and minute fields give the time directly.
    """
    try:
        minute, hour, dom, month, dow = expr.split()
        mins = [m for m in range(60) if _field_matches(minute, m)]
        hours = [h for h in range(24) if _field_matches(hour, h)]
    except (ValueError, AttributeError):
        return None
    if not mins or not hours:
        return None

    day = after.replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(HORIZON_DAYS + 2):
        if (_field_matches(dom, day.day) and _field_matches(month, day.month)
                and _field_matches(dow, (day.weekday() + 1) % 7)):   # cron: Sunday = 0
            for h in hours:
                for m in mins:
                    cand = day.replace(hour=h, minute=m)
                    if cand > after:
                        return cand
        day += timedelta(days=1)
    return None


def is_dated(expr: str, repeat: dict) -> bool:
    """A checkpoint, as opposed to routine recurrence."""
    if (repeat or {}).get("times"):          # any one-shot / bounded job is a checkpoint
        return True
    try:
        _, _, dom, month, _ = expr.split()
    except (ValueError, AttributeError):
        return False
    return dom != "*" or month != "*"        # pinned to a date rather than a rhythm


def pending(now: datetime, days: int) -> list[dict]:
    if not JOBS_FILE.exists():
        return []
    jobs = json.loads(JOBS_FILE.read_text()).get("jobs", [])
    if isinstance(jobs, dict):
        jobs = list(jobs.values())

    horizon = now + timedelta(days=days)
    out = []
    for j in jobs:
        if not j.get("enabled", True):
            continue
        repeat = j.get("repeat") or {}
        if repeat.get("times") and repeat.get("completed", 0) >= repeat["times"]:
            continue                          # spent
        expr = (j.get("schedule") or {}).get("expr") or ""
        if not is_dated(expr, repeat):
            continue
        when = next_fire(expr, now)
        if when and when <= horizon:
            out.append({"name": j.get("name") or j.get("id", "?"),
                        "id": (j.get("id") or "")[:12], "when": when,
                        "days": (when - now).days})
    return sorted(out, key=lambda r: r["when"])


def build(rows: list[dict], days: int) -> str:
    if not rows:
        return (f"🗓 <b>Scheduled Checkpoints</b>\n"
                f"<i>Nothing dated in the next {days} days.</i>\n\n"
                f"<i>Forward jobs on known catalysts — earnings, deadlines, expiries — show up "
                f"here while there is still time to act.</i>")

    lines = [f"🗓 <b>Scheduled Checkpoints</b> — next {days} days", ""]
    for r in rows:
        local = r["when"].astimezone(TZ)
        mark = "🔴" if r["days"] <= IMMINENT_DAYS else "•"
        when_txt = local.strftime("%a %d %b, %H:%M")
        lines.append(f"{mark} <b>{r['name']}</b>\n   {when_txt} · in {r['days']}d "
                     f"· <code>{r['id']}</code>")
    imminent = sum(1 for r in rows if r["days"] <= IMMINENT_DAYS)
    if imminent:
        lines += ["", f"🔴 <b>{imminent} fire within {IMMINENT_DAYS} days</b> — "
                      f"decide before they arrive, not when they do."]
    return "\n".join(lines)


def run(days: int = HORIZON_DAYS, dry_run: bool = False) -> None:
    now = datetime.now(timezone.utc)
    rows = pending(now, days)
    msg = build(rows, days)
    print(msg) if dry_run else notifier.send(msg)
    print(f"[checkpoints] {len(rows)} dated job(s) within {days}d", file=sys.stderr)


if __name__ == "__main__":
    n = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else HORIZON_DAYS
    run(days=n, dry_run="--dry-run" in sys.argv)
