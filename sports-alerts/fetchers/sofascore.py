from __future__ import annotations
from datetime import datetime, timezone
import requests
from .base import Event, Fetcher

HAPOEL_BASKETBALL_ID = 82179
API = "https://api.sofascore.com/api/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; sports-alerts/1.0)",
    "Accept": "application/json",
}


def _fetch_page(team_id: int, page: int) -> list[dict]:
    r = requests.get(f"{API}/team/{team_id}/events/next/{page}",
                     headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json().get("events", [])


class SofascoreFetcher(Fetcher):
    def fetch_week(self, start: datetime, end: datetime) -> list[Event]:
        events: list[Event] = []
        page = 0
        while True:
            try:
                raw = _fetch_page(HAPOEL_BASKETBALL_ID, page)
            except Exception as e:
                print(f"[sofascore] error page {page}: {e}")
                break
            if not raw:
                break

            past_end = False
            for ev in raw:
                ts = ev.get("startTimestamp", 0)
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                if dt >= end:
                    past_end = True
                    break
                if dt < start:
                    continue
                home = ev.get("homeTeam", {}).get("name", "?")
                away = ev.get("awayTeam", {}).get("name", "?")
                tournament = ev.get("tournament", {}).get("name", "")
                title = f"כדורסל: {home} נגד {away}"
                if tournament:
                    title += f" ({tournament})"
                eid = f"basketball-{ev.get('id', 'x')}"
                events.append(Event(id=eid, sport="hapoel_basketball",
                                    title=title, time_utc=dt))

            if past_end or not raw:
                break
            page += 1

        return events
