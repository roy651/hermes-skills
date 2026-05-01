from __future__ import annotations
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from fetchers.base import Event, TZ_IL, DAY_NAMES_HE, SPORT_EMOJI
from fetchers.espn import ESPNFetcher
from fetchers.cycling import CyclingFetcher
from fetchers.sofascore import SofascoreFetcher
import notifier

ROOT = Path(__file__).parent
REMINDERS_FILE = ROOT / "data" / "reminders.json"


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def fetch_all(sports_cfg: dict, start: datetime, end: datetime) -> list[Event]:
    events: list[Event] = []
    events += ESPNFetcher(sports_cfg).fetch_week(start, end)
    if sports_cfg.get("cycling", {}).get("enabled"):
        events += CyclingFetcher(sports_cfg["cycling"]).fetch_week(start, end)
    if sports_cfg.get("hapoel_basketball", {}).get("enabled"):
        events += SofascoreFetcher(sports_cfg["hapoel_basketball"]).fetch_week(start, end)

    # Apply reminder flag from config
    for ev in events:
        ev.has_reminder = sports_cfg.get(ev.sport, {}).get("reminder", True)

    return sorted(events, key=lambda e: e.time_utc)


def merge_with_existing(new_events: list[Event]) -> list[Event]:
    """Keep manual enable/disable overrides for events already in the queue."""
    if not REMINDERS_FILE.exists():
        return new_events
    with open(REMINDERS_FILE) as f:
        existing = {e["id"]: e for e in json.load(f)}
    for ev in new_events:
        if ev.id in existing:
            ex = existing[ev.id]
            ev.enabled = ex.get("enabled", True)
            ev.fired = ex.get("fired", False)
    return new_events


def save(events: list[Event]) -> None:
    REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REMINDERS_FILE, "w") as f:
        json.dump([e.to_dict() for e in events], f, ensure_ascii=False, indent=2)


def format_digest(events: list[Event]) -> str:
    lines = ["<b>📅 לוח אירועי ספורט לשבוע הקרוב</b>"]
    if not events:
        lines.append("\nאין אירועים מתוכננים השבוע.")
        return "\n".join(lines)

    last_date = None
    for ev in events:
        local = ev.time_utc.astimezone(TZ_IL)
        if local.date() != last_date:
            last_date = local.date()
            day = DAY_NAMES_HE[local.weekday()]
            lines.append(f"\n<b>יום {day} {local.strftime('%d/%m')}</b>")

        emoji = SPORT_EMOJI.get(ev.sport, "🏅")
        time_str = local.strftime("%H:%M")
        muted = " ⏸" if not ev.enabled else ""
        lines.append(f"  {emoji} {time_str} — {ev.title}{muted}")

    reminder_count = sum(1 for e in events if e.has_reminder and e.enabled)
    lines.append(f"\n<i>⏰ {reminder_count} תזכורות מוגדרות (5 דקות לפני כל אירוע)</i>")
    return "\n".join(lines)


def main() -> None:
    cfg = load_config()
    now = datetime.now(tz=timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)

    print(f"[weekly] fetching {start.date()} → {end.date()}", flush=True)
    events = fetch_all(cfg["sports"], start, end)
    print(f"[weekly] {len(events)} events found", flush=True)

    events = merge_with_existing(events)
    save(events)

    notifier.send(format_digest(events))
    print("[weekly] digest sent", flush=True)


if __name__ == "__main__":
    main()
