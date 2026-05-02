from __future__ import annotations
from datetime import datetime, date, timedelta, timezone
import re
import unicodedata
import requests
from bs4 import BeautifulSoup
from .base import Event, Fetcher

WIKI_API = "https://en.wikipedia.org/w/api.php"
PAGE = "2026_UCI_World_Tour"
WIKI_HEADERS = {"User-Agent": "sports-alerts/1.0 (personal use)"}

TNT_BASE = "https://www.tntsports.co.uk/cycling"
TNT_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# Overrides where auto-slug doesn't match TNT Sports URL
SLUG_OVERRIDES = {
    "Paris-Roubaix": "paris-roubaix-men",
    "Tour of Flanders": "tour-of-flanders-men",
    "Milan-San Remo": "milano-sanremo",
    "Liège-Bastogne-Liège": "liege-bastogne-liege-men",
    "Flèche Wallonne": "fleche-wallonne-men",
    "Amstel Gold Race": "amstel-gold-race-men",
    "Strade Bianche": "strade-bianche-men",
    "Omloop Het Nieuwsblad": "omloop-het-nieuwsblad-men",
    "E3 Saxo Bank Classic": "e3-saxo-classic",
    "Eschborn-Frankfurt": "eschborn-frankfurt",
}

MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}


def _to_slug(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", ascii_str.lower()).strip("-")


def _parse_date(s: str, year: int) -> date | None:
    s = s.strip()
    parts = s.split()
    try:
        if len(parts) >= 2:
            return date(year, MONTHS[parts[-1]], int(re.sub(r"\D", "", parts[0])))
        return None
    except (KeyError, ValueError):
        return None


def _parse_range(date_str: str, year: int) -> tuple[date, date] | None:
    parts = re.split(r"[–\-]", date_str.strip())
    if len(parts) == 1:
        d = _parse_date(parts[0], year)
        return (d, d) if d else None

    raw_start, raw_end = parts[0].strip(), parts[1].strip()
    end = _parse_date(raw_end, year)
    if not end:
        return None

    start = _parse_date(raw_start, year)
    if not start:
        try:
            day = int(re.sub(r"\D", "", raw_start))
            start = date(year, end.month, day)
            if start > end:
                m = end.month - 1 or 12
                y = year if end.month > 1 else year - 1
                start = date(y, m, day)
        except (ValueError, AttributeError):
            return None

    return (start, end) if start and end else None


def _tnt_stage_events(race_name: str, year: int, week_start: date, week_end: date,
                       has_reminder: bool) -> list[Event]:
    """Fetch per-stage events from TNT Sports for stages in the given week window."""
    slug = SLUG_OVERRIDES.get(race_name) or _to_slug(race_name)
    cal_url = f"{TNT_BASE}/{slug}/{year}/calendar-results.shtml"
    try:
        r = requests.get(cal_url, headers=TNT_HEADERS, timeout=15)
        if r.status_code == 404:
            # Try with -men suffix
            r = requests.get(cal_url.replace(f"/{slug}/", f"/{slug}-men/"), headers=TNT_HEADERS, timeout=15)
        if r.status_code != 200:
            return []
    except Exception as e:
        print(f"[cycling:tnt] {race_name}: {e}")
        return []

    # Extract unique stage slugs with dates — context search for date near the slug
    seen_slugs: set[str] = set()
    stage_entries: list[tuple[date, str, str]] = []  # (date, label, slug_path)

    for m in re.finditer(r"([a-z0-9_-]+_mtc\d+/live\.shtml)", r.text):
        slug_path = m.group(1)
        if slug_path in seen_slugs:
            continue
        seen_slugs.add(slug_path)

        ctx = r.text[max(0, m.start() - 2500):m.end() + 100]
        # Find the last (closest) date in context — date headers precede their stage cards
        date_matches = list(re.finditer(r"(\d{2}/\d{2}/\d{4})", ctx))
        date_m = date_matches[-1] if date_matches else None
        if not date_m:
            continue
        d_str = date_m.group(1)
        try:
            stage_date = date(int(d_str[6:]), int(d_str[3:5]), int(d_str[:2]))
        except ValueError:
            continue

        if not (week_start <= stage_date < week_end):
            continue

        label_m = re.search(r"(?i)(stage \d+|prologue)", ctx)
        label = label_m.group(1).capitalize() if label_m else "Stage"
        stage_entries.append((stage_date, label, slug_path))

    if not stage_entries:
        return []

    events: list[Event] = []
    for stage_date, label, slug_path in stage_entries:
        stage_url = f"{TNT_BASE}/{slug}/{year}/{slug_path}"
        start_dt: datetime | None = None
        try:
            sr = requests.get(stage_url, headers=TNT_HEADERS, timeout=15)
            if sr.status_code == 200:
                for jm in re.finditer(r'"startDate"\s*:\s*"(2\d{3}[^"]+)"', sr.text):
                    try:
                        start_dt = datetime.fromisoformat(
                            jm.group(1).replace("Z", "+00:00"))
                        break
                    except ValueError:
                        pass
        except Exception as e:
            print(f"[cycling:tnt] stage fetch error: {e}")

        if not start_dt:
            start_dt = datetime(stage_date.year, stage_date.month, stage_date.day,
                                8, 0, tzinfo=timezone.utc)

        eid = f"cycling-{_to_slug(race_name)[:15]}-{stage_date.strftime('%Y%m%d')}"
        title = f"רכיבה: {race_name} — {label}"
        events.append(Event(id=eid, sport="cycling", title=title,
                            time_utc=start_dt, has_reminder=has_reminder))

    return events


class CyclingFetcher(Fetcher):
    def fetch_week(self, start: datetime, end: datetime) -> list[Event]:
        year = start.year
        has_reminder = self.cfg.get("reminder", False)
        try:
            r = requests.get(
                WIKI_API,
                params={"action": "parse", "page": PAGE, "format": "json", "prop": "text"},
                headers=WIKI_HEADERS,
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
            if winner:
                continue

            parsed = _parse_range(date_str, year)
            if not parsed:
                continue
            race_start, race_end = parsed

            if race_start >= end_date or race_end < start_date:
                continue

            # Try TNT Sports for per-stage events with actual times
            stage_events = _tnt_stage_events(race_name, year, start_date, end_date,
                                             has_reminder)
            if stage_events:
                events += stage_events
                continue

            # Fallback: single summary event from Wikipedia
            duration = (race_end - race_start).days + 1
            suffix = f" ({duration} ימים)" if duration > 1 else ""
            if race_start < start_date:
                suffix = f" (נמשכת עד {race_end.strftime('%d/%m')})"
            title = f"רכיבה: {race_name}{suffix}"

            dt = datetime(race_start.year, race_start.month, race_start.day,
                          8, 0, 0, tzinfo=timezone.utc)
            eid = f"cycling-{race_start.strftime('%Y%m%d')}-{race_name[:20].replace(' ', '-').lower()}"
            events.append(Event(
                id=eid, sport="cycling", title=title,
                time_utc=dt, has_reminder=False,
            ))

        return events
