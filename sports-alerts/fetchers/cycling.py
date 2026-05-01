from __future__ import annotations
from datetime import datetime, date, timedelta, timezone
import re
import requests
from bs4 import BeautifulSoup
from .base import Event, Fetcher

WIKI_API = "https://en.wikipedia.org/w/api.php"
PAGE = "2026_UCI_World_Tour"
HEADERS = {"User-Agent": "sports-alerts/1.0 (personal use)"}

MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}


def _parse_date(s: str, year: int) -> date | None:
    """Parse 'D Month' or 'D' into a date. month_hint used for day-only strings."""
    s = s.strip()
    parts = s.split()
    try:
        if len(parts) >= 2:
            return date(year, MONTHS[parts[-1]], int(re.sub(r"\D", "", parts[0])))
        return None
    except (KeyError, ValueError):
        return None


def _parse_range(date_str: str, year: int) -> tuple[date, date] | None:
    """Return (start_date, end_date) from strings like '28 April – 3 May' or '1 May'."""
    parts = re.split(r"[–\-]", date_str.strip())
    if len(parts) == 1:
        d = _parse_date(parts[0], year)
        return (d, d) if d else None

    raw_start, raw_end = parts[0].strip(), parts[1].strip()
    end = _parse_date(raw_end, year)
    if not end:
        return None

    # Start may be day-only ("28 April – 3 May" → "28" has no month)
    start = _parse_date(raw_start, year)
    if not start:
        # Use end month as hint for the start day
        try:
            day = int(re.sub(r"\D", "", raw_start))
            start = date(year, end.month, day)
            # If start > end, it's cross-month (e.g., Apr 28 – May 3)
            if start > end:
                m = end.month - 1 or 12
                y = year if end.month > 1 else year - 1
                start = date(y, m, day)
        except (ValueError, AttributeError):
            return None

    return (start, end) if start and end else None


class CyclingFetcher(Fetcher):
    def fetch_week(self, start: datetime, end: datetime) -> list[Event]:
        year = start.year
        try:
            r = requests.get(
                WIKI_API,
                params={"action": "parse", "page": PAGE, "format": "json", "prop": "text"},
                headers=HEADERS,
                timeout=15,
            )
            r.raise_for_status()
            html = r.json()["parse"]["text"]["*"]
        except Exception as e:
            print(f"[cycling] fetch error: {e}")
            return []

        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", class_="wikitable")
        if not table:
            print("[cycling] race table not found on Wikipedia page")
            return []

        events: list[Event] = []
        start_date = start.date()
        end_date = end.date()

        for row in table.find_all("tr")[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            race_name = cells[0].get_text(strip=True)
            date_str = cells[1].get_text(strip=True)
            winner = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            # Skip completed races (winner is filled in)
            if winner:
                continue

            parsed = _parse_range(date_str, year)
            if not parsed:
                continue
            race_start, race_end = parsed

            # Include if race overlaps with the week window
            if race_start >= end_date or race_end < start_date:
                continue

            duration = (race_end - race_start).days + 1
            suffix = f" ({duration} ימים)" if duration > 1 else ""
            # For ongoing races, note the full date range
            if race_start < start_date:
                date_label = f" (נמשכת עד {race_end.strftime('%d/%m')})"
                suffix = date_label
            title = f"רכיבה: {race_name}{suffix}"

            dt = datetime(race_start.year, race_start.month, race_start.day,
                          8, 0, 0, tzinfo=timezone.utc)
            eid = f"cycling-{race_start.strftime('%Y%m%d')}-{race_name[:20].replace(' ', '-').lower()}"
            events.append(Event(
                id=eid, sport="cycling", title=title,
                time_utc=dt, has_reminder=False,
            ))

        return events
