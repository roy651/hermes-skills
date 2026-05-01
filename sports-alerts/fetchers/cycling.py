from __future__ import annotations
from datetime import datetime, date, timezone
import requests
from bs4 import BeautifulSoup
from .base import Event, Fetcher

CALENDAR_URL = "https://www.procyclingstats.com/races.php?year={year}&circuit=1&offset=0&filter=Filter"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.procyclingstats.com/",
}


def _parse_start_date(date_str: str, year: int) -> date | None:
    # Formats: "01.05" (single day) or "01.05 - 10.05" (stage race)
    try:
        start = date_str.strip().split(" - ")[0].strip()
        day, month = start.split(".")
        return date(year, int(month), int(day))
    except Exception:
        return None


def _race_duration(date_str: str) -> int:
    parts = date_str.strip().split(" - ")
    if len(parts) < 2:
        return 1
    try:
        start_day = int(parts[0].strip().split(".")[0])
        end_day = int(parts[1].strip().split(".")[0])
        return max(1, end_day - start_day + 1)
    except Exception:
        return 1


class CyclingFetcher(Fetcher):
    def fetch_week(self, start: datetime, end: datetime) -> list[Event]:
        year = start.year
        try:
            r = requests.get(CALENDAR_URL.format(year=year), headers=HEADERS, timeout=20)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            print(f"[cycling] fetch error: {e}")
            return []

        events: list[Event] = []
        table = soup.find("table", class_=lambda c: c and "basic" in c)
        if not table:
            # Fallback: find any table with race data
            table = soup.find("table")
        if not table:
            print("[cycling] could not find race table")
            return []

        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            date_str = cells[0].get_text(strip=True)
            # Race name is typically in the 3rd column (index 2), may contain a link
            name_cell = cells[2] if len(cells) > 2 else cells[-1]
            race_name = name_cell.get_text(strip=True)
            if not race_name or not date_str:
                continue

            start_date = _parse_start_date(date_str, year)
            if not start_date:
                continue

            # Use 08:00 UTC as approximate start time (European races)
            dt = datetime(start_date.year, start_date.month, start_date.day,
                          8, 0, 0, tzinfo=timezone.utc)
            if not (start <= dt < end):
                continue

            duration = _race_duration(date_str)
            suffix = f" ({duration} ימים)" if duration > 1 else ""
            title = f"רכיבה: {race_name}{suffix}"
            eid = f"cycling-{start_date.strftime('%Y%m%d')}-{race_name[:20].replace(' ', '-').lower()}"
            events.append(Event(
                id=eid, sport="cycling", title=title,
                time_utc=dt, has_reminder=False,  # no 5-min reminder for cycling
            ))

        return events
