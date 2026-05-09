from __future__ import annotations
import json
from pathlib import Path

_FILE = Path(__file__).parent.parent / "data" / "holdings.json"


def load() -> dict:
    if not _FILE.exists():
        return {}
    return json.loads(_FILE.read_text())


def save(holdings: dict) -> None:
    _FILE.write_text(json.dumps(holdings, indent=2))


def add(ticker: str, qty: float, avg_cost: float) -> None:
    h = load()
    h[ticker.upper()] = {"qty": round(qty, 4), "avg_cost": round(avg_cost, 4)}
    save(h)


def remove(ticker: str) -> bool:
    h = load()
    key = ticker.upper()
    if key not in h:
        return False
    del h[key]
    save(h)
    return True
