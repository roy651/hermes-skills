from __future__ import annotations
import re
from datetime import datetime, date, timedelta, timezone
import requests
from bs4 import BeautifulSoup
from .base import Event, Fetcher, TZ_IL

AJAX_URL = "https://www.sport5.co.il/Ajax/GetBroadcastSheetData.aspx"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.sport5.co.il/html/pages/broadcastsheet.html",
    "X-Requested-With": "XMLHttpRequest",
}
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")


def _fetch_day(d: date) -> list[tuple[str, str, str]]:
    """Return (time_str, channel, title) for the given date, deduplicated."""
    r = requests.get(AJAX_URL, headers=HEADERS,
                     params={"date": d.strftime("%Y-%m-%d")}, timeout=12)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    results: list[tuple[str, str, str]] = []
    current_channel = "ערוץ הספורט"
    seen: set[tuple[str, str]] = set()

    for row in soup.find_all("tr"):
        if "tr-header" in row.get("class", []):
            current_channel = row.get_text(strip=True)
            continue
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        time_str = cells[0].get_text(strip=True)
        title = cells[-1].get_text(strip=True)
        if not TIME_RE.match(time_str) or not title:
            continue
        key = (time_str, title[:50])
        if key in seen:
            continue
        seen.add(key)
        results.append((time_str, current_channel, title))

    return results


def _matches(title: str, include: list[str], require: list[str]) -> bool:
    return (any(kw in title for kw in include) and
            (not require or any(kw in title for kw in require)))


class Sport5Fetcher(Fetcher):
    """
    Fetches the Israeli sports broadcast schedule from sport5's TV guide.
    The guide covers sport5, sport1, sport2, sport3, sport4, ערוץ One and more.

    cfg is the sports-alerts config dict (cfg["sport5"]["filters"]).
    Each filter entry: { enabled, include, require, max_local_hour, reminder, sport }
    """

    def fetch_week(self, start: datetime, end: datetime) -> list[Event]:
        filters: dict = self.cfg  # sport_key -> filter config

        events: list[Event] = []
        day = start.date()

        while day < end.date():
            try:
                entries = _fetch_day(day)
            except Exception as e:
                print(f"[sport5] {day} error: {e}")
                day += timedelta(days=1)
                continue

            for time_str, channel, title in entries:
                for sport_key, fcfg in filters.items():
                    if not fcfg.get("enabled", True):
                        continue
                    include = fcfg.get("include", [])
                    require = fcfg.get("require", [])
                    if not _matches(title, include, require):
                        continue

                    h, m = map(int, time_str.split(":"))
                    # Apply hour cutoff (same logic as NFL: hours < 8 are post-midnight)
                    max_hour = fcfg.get("max_local_hour", 27)
                    effective_hour = h if h >= 8 else h + 24
                    if effective_hour >= max_hour:
                        continue

                    dt_local = datetime(day.year, day.month, day.day, h, m, tzinfo=TZ_IL)
                    dt_utc = dt_local.astimezone(timezone.utc)
                    # Skip if clearly outside the week window
                    if dt_utc >= end or dt_utc < start - timedelta(hours=4):
                        continue

                    sport = fcfg.get("sport", sport_key)
                    eid = f"sport5-{sport_key}-{day.strftime('%Y%m%d')}-{h:02d}{m:02d}"
                    events.append(Event(
                        id=eid, sport=sport, title=title,
                        time_utc=dt_utc,
                        has_reminder=fcfg.get("reminder", True),
                    ))
                    break  # one sport match per title is enough

            day += timedelta(days=1)

        return events
