from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

import notifier
from fetchers.base import Event, TZ_IL, SPORT_EMOJI

ROOT = Path(__file__).parent
REMINDERS_FILE = ROOT / "data" / "reminders.json"

# Fire reminder if event is within this many seconds (covers any 5-min cron slot)
FIRE_WINDOW_SECS = 600
# Allow up to 60s past start in case of slight cron delay
GRACE_SECS = 60


def main() -> None:
    if not REMINDERS_FILE.exists():
        return

    with open(REMINDERS_FILE) as f:
        raw = json.load(f)

    now = datetime.now(tz=timezone.utc)
    events = [Event.from_dict(d) for d in raw]
    changed = False

    for ev in events:
        if not ev.has_reminder or not ev.enabled or ev.fired:
            continue
        secs = (ev.time_utc - now).total_seconds()
        if not (-GRACE_SECS <= secs <= FIRE_WINDOW_SECS):
            continue

        local = ev.time_utc.astimezone(TZ_IL)
        emoji = SPORT_EMOJI.get(ev.sport, "🏅")
        msg = (
            f"{emoji} <b>תזכורת ספורט</b>\n"
            f"{ev.title}\n"
            f"⏰ מתחיל ב-{local.strftime('%H:%M')}"
        )
        try:
            notifier.send(msg)
            ev.fired = True
            changed = True
            print(f"[watchdog] fired: {ev.id}", flush=True)
        except Exception as e:
            print(f"[watchdog] send error {ev.id}: {e}", flush=True)

    if changed:
        with open(REMINDERS_FILE, "w") as f:
            json.dump([e.to_dict() for e in events], f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
