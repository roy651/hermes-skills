"""
Override sports reminder settings from the command line or Hermes tool.

Usage:
  override.py list
  override.py enable  <query>   — re-enable a muted event
  override.py disable <query>   — mute an event (no reminder, hidden in digest)
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from fetchers.base import Event, TZ_IL

ROOT = Path(__file__).parent
REMINDERS_FILE = ROOT / "data" / "reminders.json"


def _load() -> list[Event]:
    if not REMINDERS_FILE.exists():
        return []
    with open(REMINDERS_FILE) as f:
        return [Event.from_dict(d) for d in json.load(f)]


def _save(events: list[Event]) -> None:
    with open(REMINDERS_FILE, "w") as f:
        json.dump([e.to_dict() for e in events], f, ensure_ascii=False, indent=2)


def _match(events: list[Event], query: str) -> list[Event]:
    q = query.strip().lower()
    exact = [e for e in events if e.id == q]
    if exact:
        return exact
    return [e for e in events if q in e.id.lower() or q in e.title.lower()]


def cmd_list(events: list[Event]) -> None:
    if not events:
        print("אין אירועים בתור השבוע.")
        return
    for ev in events:
        local = ev.time_utc.astimezone(TZ_IL)
        status = "✅" if ev.enabled else "⏸ "
        fired = " ✓" if ev.fired else ""
        print(f"{status} {local.strftime('%d/%m %H:%M')}  {ev.title}  [{ev.id}]{fired}")


def cmd_set(events: list[Event], query: str, enabled: bool) -> None:
    matches = _match(events, query)
    if not matches:
        print(f"לא נמצאו אירועים התואמים: '{query}'")
        sys.exit(1)
    for ev in matches:
        ev.enabled = enabled
        word = "הופעל" if enabled else "הושהה"
        print(f"{word}: {ev.title}")
    _save(events)


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0].lower() if args else "list"

    if cmd in ("list", "רשימה", "ls"):
        cmd_list(_load())
        return

    if len(args) < 2:
        print(__doc__)
        sys.exit(1)

    query = " ".join(args[1:])
    events = _load()

    if cmd in ("enable", "הפעל"):
        cmd_set(events, query, True)
    elif cmd in ("disable", "השהה", "דלג", "skip"):
        cmd_set(events, query, False)
    else:
        print(f"פקודה לא ידועה: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
