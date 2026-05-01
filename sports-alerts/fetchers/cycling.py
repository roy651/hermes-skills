from __future__ import annotations
from datetime import datetime, date, timezone
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


def _parse_start(date_str: str, year: int) -> date | None:
    # Handles: "1 February", "20–25 January", "20-25 January"
    s = re.split(r"[–\-]", date_str.strip())[0].strip()
    # s is like "20 January" or "1 February"
    parts = s.split()
    if len(parts) < 2:
        return None
    try:
        day = int(re.sub(r"\D", "", parts[0]))
        month = MONTHS.get(parts[-1])
        if not month:
            return None
        return date(year, month, day)
    except (ValueError, IndexError):
        return None


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
        for row in table.find_all("tr")[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            race_name = cells[0].get_text(strip=True)
            date_str = cells[1].get_text(strip=True)
            # Skip past races (winner cell is filled) — future races have empty winner
            winner = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            if winner:
                continue

            race_date = _parse_start(date_str, year)
            if not race_date:
                continue

            dt = datetime(race_date.year, race_date.month, race_date.day,
                          8, 0, 0, tzinfo=timezone.utc)
            if not (start <= dt < end):
                continue

            # Multi-day: detect "X–Y Month" pattern
            if "–" in date_str or ("-" in date_str and any(c.isalpha() for c in date_str)):
                parts = re.split(r"[–\-]", date_str.strip())
                try:
                    d1 = int(re.sub(r"\D", "", parts[0]))
                    d2 = int(re.sub(r"\D", "", parts[1].split()[0]))
                    duration = max(1, d2 - d1 + 1)
                except Exception:
                    duration = 1
                suffix = f" ({duration} ימים)" if duration > 1 else ""
            else:
                suffix = ""

            title = f"רכיבה: {race_name}{suffix}"
            eid = f"cycling-{race_date.strftime('%Y%m%d')}-{race_name[:20].replace(' ', '-').lower()}"
            events.append(Event(
                id=eid, sport="cycling", title=title,
                time_utc=dt, has_reminder=False,
            ))

        return events
