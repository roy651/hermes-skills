from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from abc import ABC, abstractmethod
from zoneinfo import ZoneInfo

TZ_IL = ZoneInfo("Asia/Jerusalem")

DAY_NAMES_HE = {0: "שני", 1: "שלישי", 2: "רביעי", 3: "חמישי", 4: "שישי", 5: "שבת", 6: "ראשון"}

SPORT_EMOJI = {
    "f1": "🏎️",
    "cycling": "🚴",
    "nfl": "🏈",
    "hapoel_soccer": "⚽",
    "hapoel_basketball": "🏀",
    "nba_avdia": "🏀",
}


@dataclass
class Event:
    id: str
    sport: str
    title: str
    time_utc: datetime
    has_reminder: bool = True
    enabled: bool = True
    fired: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "sport": self.sport,
            "title": self.title,
            "time_utc": self.time_utc.isoformat(),
            "has_reminder": self.has_reminder,
            "enabled": self.enabled,
            "fired": self.fired,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Event:
        return cls(
            id=d["id"],
            sport=d["sport"],
            title=d["title"],
            time_utc=datetime.fromisoformat(d["time_utc"]),
            has_reminder=d.get("has_reminder", True),
            enabled=d.get("enabled", True),
            fired=d.get("fired", False),
        )

    def local_str(self) -> str:
        local = self.time_utc.astimezone(TZ_IL)
        day = DAY_NAMES_HE[local.weekday()]
        return f"יום {day} {local.strftime('%d/%m')} {local.strftime('%H:%M')}"


class Fetcher(ABC):
    def __init__(self, cfg: dict):
        self.cfg = cfg

    @abstractmethod
    def fetch_week(self, start: datetime, end: datetime) -> list[Event]:
        ...
