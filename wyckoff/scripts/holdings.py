from __future__ import annotations
import json
from pathlib import Path

_FILE = Path(__file__).parent.parent / "data" / "holdings.json"

# Instruments whose price is driven by rates/duration/a formula — NOT a supply-demand
# trend — so a Wyckoff trailing stop mis-fires on them (it would sell a bond at the yield
# high on rate noise). Tag such a holding in holdings.json with "asset_class": "bond"
# (or explicit "no_trailing_stop": true) to exempt it from the mechanical stop. Their exit
# is a thesis/rate decision, not a trailing stop — see SKILL.md.
NO_TRAIL_CLASSES = {"bond", "treasury", "cash", "money_market"}


def no_trailing_stop(h: dict) -> bool:
    """True if this holding should be exempt from the mechanical trailing stop."""
    return bool(h.get("no_trailing_stop")) or h.get("asset_class") in NO_TRAIL_CLASSES


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
