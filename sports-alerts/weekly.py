from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from fetchers.base import Event, TZ_IL, DAY_NAMES_HE, SPORT_EMOJI
from fetchers.espn import ESPNFetcher
from fetchers.cycling import CyclingFetcher
from fetchers.sofascore import SofascoreFetcher
from fetchers.sport5 import Sport5Fetcher
import notifier

ROOT = Path(__file__).parent
REMINDERS_FILE = ROOT / "data" / "reminders.json"


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def fetch_all(cfg: dict, start: datetime, end: datetime) -> list[Event]:
    events: list[Event] = []

    # 1. sport5 — Israeli TV broadcast schedule (primary)
    if cfg.get("sport5", {}).get("enabled"):
        s5 = Sport5Fetcher(cfg["sport5"]["filters"]).fetch_week(start, end)
        events += s5
        print(f"[weekly] sport5: {len(s5)} events")

    # Track which (sport, date) combos are already covered by sport5
    sport5_covered: set[tuple[str, str]] = {
        (e.sport, e.time_utc.astimezone(TZ_IL).strftime("%Y-%m-%d"))
        for e in events
    }

    sports_cfg = cfg.get("sports", {})

    # 2. ESPN — fallback for events not broadcast in Israel
    espn = ESPNFetcher(sports_cfg).fetch_week(start, end)
    for ev in espn:
        day_key = ev.time_utc.astimezone(TZ_IL).strftime("%Y-%m-%d")
        if (ev.sport, day_key) not in sport5_covered:
            events.append(ev)
    print(f"[weekly] espn: {len(espn)} raw, added {sum(1 for e in events if 'nfl-' in e.id or 'nba-' in e.id or 'f1-' in e.id or 'soccer-' in e.id)}")

    # 3. Cycling — Wikipedia WorldTour calendar
    if sports_cfg.get("cycling", {}).get("enabled"):
        cyc = CyclingFetcher(sports_cfg["cycling"]).fetch_week(start, end)
        for ev in cyc:
            ev.has_reminder = sports_cfg["cycling"].get("reminder", False)
        events += cyc
        print(f"[weekly] cycling: {len(cyc)} events")

    # 4. Sofascore (Hapoel basketball) — currently blocked, kept as no-op
    if sports_cfg.get("hapoel_basketball", {}).get("enabled"):
        try:
            sfs = SofascoreFetcher(sports_cfg["hapoel_basketball"]).fetch_week(start, end)
            for ev in sfs:
                day_key = ev.time_utc.astimezone(TZ_IL).strftime("%Y-%m-%d")
                if (ev.sport, day_key) not in sport5_covered:
                    events.append(ev)
        except Exception:
            pass

    return sorted(events, key=lambda e: e.time_utc)


def merge_with_existing(new_events: list[Event]) -> list[Event]:
    """Preserve manual enable/disable overrides for events already in the queue."""
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
    events = fetch_all(cfg, start, end)
    print(f"[weekly] {len(events)} total events", flush=True)

    # Dedup by ID, keeping last occurrence (named-channel sport5 entry overwrites unnamed)
    seen_ids: dict[str, Event] = {}
    for ev in events:
        seen_ids[ev.id] = ev
    events = sorted(seen_ids.values(), key=lambda e: e.time_utc)

    events = merge_with_existing(events)
    save(events)

    notifier.send(format_digest(events))
    print("[weekly] digest sent", flush=True)


if __name__ == "__main__":
    main()
