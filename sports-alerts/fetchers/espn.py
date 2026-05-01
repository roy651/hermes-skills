from __future__ import annotations
from datetime import datetime, timedelta
import requests
from .base import Event, Fetcher, TZ_IL

ESPN = "https://site.api.espn.com/apis/site/v2/sports"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def _get(url: str, params: dict | None = None) -> dict:
    r = requests.get(url, params=params, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _teams(comp: dict) -> list[str]:
    return [c.get("team", {}).get("abbreviation", "?") for c in comp.get("competitors", [])]


class ESPNFetcher(Fetcher):
    def fetch_week(self, start: datetime, end: datetime) -> list[Event]:
        events: list[Event] = []
        if self.cfg.get("f1", {}).get("enabled"):
            events += self._f1(start, end)
        if self.cfg.get("nfl", {}).get("enabled"):
            events += self._nfl(start, end)
        if self.cfg.get("nba_avdia", {}).get("enabled"):
            events += self._nba(start, end)
        if self.cfg.get("hapoel_soccer", {}).get("enabled"):
            events += self._soccer(start, end)
        return events

    # ── F1 ──────────────────────────────────────────────────────────────────

    def _f1(self, start: datetime, end: datetime) -> list[Event]:
        cfg = self.cfg["f1"]
        allowed_sessions = set(cfg.get("sessions", ["race", "qualifying", "sprint"]))
        abbr_to_session = {
            "Race": "race", "Qual": "qualifying",
            "SR": "sprint", "SS": "sprint_shootout",
            "FP1": "practice", "FP2": "practice", "FP3": "practice",
        }
        label = {"race": "מירוץ", "qualifying": "כישורים", "sprint": "ספרינט"}
        try:
            data = _get(f"{ESPN}/racing/f1/scoreboard")
        except Exception as e:
            print(f"[espn:f1] error: {e}")
            return []

        events = []
        for ev in data.get("events", []):
            name = ev.get("shortName") or ev.get("name", "")
            for comp in ev.get("competitions", []):
                dt_str = comp.get("date") or comp.get("startDate", "")
                if not dt_str:
                    continue
                dt = _parse_dt(dt_str)
                if not (start <= dt < end):
                    continue
                abbr = comp.get("type", {}).get("abbreviation", "")
                session = abbr_to_session.get(abbr)
                if session not in allowed_sessions:
                    continue
                title = f"F1 {label[session]} — {name}"
                eid = f"f1-{ev.get('id', 'x')}-{abbr.lower()}"
                events.append(Event(id=eid, sport="f1", title=title, time_utc=dt))
        return events

    # ── NFL ─────────────────────────────────────────────────────────────────

    def _nfl(self, start: datetime, end: datetime) -> list[Event]:
        cfg = self.cfg["nfl"]
        max_local_hour = cfg.get("max_local_hour", 24)
        try:
            data = _get(f"{ESPN}/football/nfl/scoreboard")
        except Exception as e:
            print(f"[espn:nfl] error: {e}")
            return []

        events = []
        for ev in data.get("events", []):
            dt = _parse_dt(ev.get("date", ""))
            if not (start <= dt < end):
                continue
            local = dt.astimezone(TZ_IL)
            # Hours < 8 are post-midnight; shift them past 24 for comparison
            effective_hour = local.hour if local.hour >= 8 else local.hour + 24
            if effective_hour >= max_local_hour:
                continue
            comps = ev.get("competitions", [{}])
            abbrs = _teams(comps[0])
            title = f"NFL: {' @ '.join(reversed(abbrs))}"
            eid = f"nfl-{ev.get('id', 'x')}"
            events.append(Event(id=eid, sport="nfl", title=title, time_utc=dt))
        return events

    # ── NBA (Deni Avdia / Portland Trail Blazers) ────────────────────────────

    def _nba(self, start: datetime, end: datetime) -> list[Event]:
        events: list[Event] = []
        day = start.date()
        seen: set[str] = set()
        while day < end.date():
            try:
                data = _get(f"{ESPN}/basketball/nba/scoreboard",
                            {"dates": day.strftime("%Y%m%d")})
                for ev in data.get("events", []):
                    eid_raw = str(ev.get("id", ""))
                    if eid_raw in seen:
                        continue
                    comps = ev.get("competitions", [{}])
                    teams = comps[0].get("competitors", []) if comps else []
                    abbrs = [t.get("team", {}).get("abbreviation", "") for t in teams]
                    if "POR" not in abbrs:
                        continue
                    seen.add(eid_raw)
                    dt = _parse_dt(ev.get("date", ""))
                    names = [t.get("team", {}).get("shortDisplayName", "") for t in teams]
                    title = f"NBA: {' @ '.join(reversed(names))} (דני אבדיה)"
                    events.append(Event(id=f"nba-{eid_raw}", sport="nba_avdia",
                                        title=title, time_utc=dt))
            except Exception as e:
                print(f"[espn:nba] {day} error: {e}")
            day += timedelta(days=1)
        return events

    # ── Hapoel Tel Aviv soccer (Israeli Premier League) ──────────────────────

    def _soccer(self, start: datetime, end: datetime) -> list[Event]:
        events: list[Event] = []
        seen: set[str] = set()
        day = start.date()
        while day < end.date():
            try:
                data = _get(f"{ESPN}/soccer/isr.1/scoreboard",
                            {"dates": day.strftime("%Y%m%d")})
                for ev in data.get("events", []):
                    eid_raw = str(ev.get("id", ""))
                    if eid_raw in seen:
                        continue
                    dt = _parse_dt(ev.get("date", ""))
                    if not (start <= dt < end):
                        continue
                    comps = ev.get("competitions", [{}])
                    teams = comps[0].get("competitors", []) if comps else []
                    names = [t.get("team", {}).get("displayName", "") for t in teams]
                    if not any("Hapoel" in n and "Tel Aviv" in n for n in names):
                        continue
                    seen.add(eid_raw)
                    shorts = [t.get("team", {}).get("shortDisplayName", "") or
                              t.get("team", {}).get("abbreviation", "?") for t in teams]
                    title = f"כדורגל: {' נגד '.join(shorts)}"
                    events.append(Event(id=f"soccer-{eid_raw}", sport="hapoel_soccer",
                                        title=title, time_utc=dt))
            except Exception as e:
                print(f"[espn:soccer] {day} error: {e}")
            day += timedelta(days=1)
        return events
