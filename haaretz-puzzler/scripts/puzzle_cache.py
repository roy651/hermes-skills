#!/usr/bin/env python3
"""Haaretz Puzzle Cache Manager — On-demand (no polling, no cron)

Called every time someone sends /puzzle. Decides whether to fetch fresh or serve cached.

Output:
    MEDIA:<path>
    ALT_INFO:<title>
    SOURCE:cached|fresh
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = SKILL_DIR / "cache_state.json"
PUZZLE_INDEX = int(os.environ.get("PUZZLE_INDEX", "3"))
RETRY_HOURS = int(os.environ.get("PUZZLE_RETRY_HOURS", "2"))


def now_israel():
    import zoneinfo
    return datetime.now(zoneinfo.ZoneInfo("Asia/Jerusalem"))


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def current_friday_date():
    now = now_israel()
    days_since_fri = (now.weekday() - 4) % 7
    friday = now - timedelta(days=days_since_fri)
    return friday.strftime("%Y-%m-%d")


def fetch_new_puzzle(puzzle_index=3):
    script = SKILL_DIR / "scripts" / "haaretz_browser.py"
    env = os.environ.copy()

    import subprocess
    result = subprocess.run(
        [sys.executable, str(script),
         "--email", os.environ.get("HAARETZ_EMAIL", ""),
         "--password", os.environ.get("HAARETZ_PASSWORD", ""),
         "--index", str(puzzle_index),
         "--output-dir", str(SKILL_DIR / "output")],
        capture_output=True, text=True, timeout=180,
        env=env,
    )

    if result.returncode != 0:
        err = result.stderr.strip()
        if "ERROR:" in err:
            raise Exception(err.split("ERROR:")[1].strip().split("\n")[0])
        raise Exception("Browser script failed")

    path = None
    title = ""
    for line in result.stdout.strip().split("\n"):
        if line.startswith("MEDIA:"):
            path = line[6:]
        elif line.startswith("ALT_INFO:"):
            title = line[9:]

    if not path or not os.path.exists(path):
        raise Exception("No valid image returned")

    return path, title


def serve_cached(state):
    cached_path = state.get("puzzle_file", "")
    print(f"MEDIA:{cached_path}")
    print(f"ALT_INFO:{state.get('puzzle_title', 'תשבץ')}")
    print("SOURCE:cached")


def main():
    state = load_state()
    cached_path = state.get("puzzle_file", "")
    cached_fri = state.get("cached_friday_date", "")
    last_check = state.get("last_check_at", "")
    today_fri = current_friday_date()
    now = now_israel()

    # Case 1: No cache at all — must fetch
    if not cached_path or not os.path.exists(cached_path):
        print("[cache] No cache — fetching", file=sys.stderr)
        print("SOURCE:fresh")
    else:
        # Case 2: Cached puzzle is from this same week — serve it
        if cached_fri == today_fri:
            print(f"[cache] Serving cached from {cached_fri}", file=sys.stderr)
            print(f"MEDIA:{cached_path}")
            print(f"ALT_INFO:{state.get('puzzle_title', 'תשבץ')}")
            print("SOURCE:cached")
            return

        # Case 3: Cached from older week — check cooldown
        if last_check:
            last_check_dt = datetime.fromisoformat(last_check)
            if now < last_check_dt + timedelta(hours=RETRY_HOURS):
                remaining = (last_check_dt + timedelta(hours=RETRY_HOURS) - now)
                mins = int(remaining.seconds / 60)
                print(f"[cache] Cooldown active — retry in {mins}m. Serving cached.", file=sys.stderr)
                serve_cached(state)
                return

        # Cooldown passed — try to fetch new
        print("[cache] New week — attempting fetch", file=sys.stderr)
        print("SOURCE:fresh")

    # Attempt fetch
    print("[cache] Fetching new puzzle...", file=sys.stderr)
    try:
        path, title = fetch_new_puzzle(PUZZLE_INDEX)
        state.update({
            "puzzle_file": path,
            "puzzle_title": title,
            "cached_friday_date": today_fri,
            "last_check_at": now.isoformat(),
        })
        save_state(state)
        print(f"[cache] Fetched successfully: {title}", file=sys.stderr)
        print(f"MEDIA:{path}")
        print(f"ALT_INFO:{title}")
        print("SOURCE:fresh")
    except Exception as e:
        print(f"[cache] Fetch failed: {e}", file=sys.stderr)
        state["last_check_at"] = now.isoformat()
        save_state(state)

        if cached_path and os.path.exists(cached_path):
            print(f"[cache] Serving cached fallback", file=sys.stderr)
            serve_cached(state)
        else:
            print(f"ERROR: No cached puzzle and fetch failed: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
