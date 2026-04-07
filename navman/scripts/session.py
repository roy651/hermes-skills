"""Session state persistence — one JSON file per chat_id."""
import json
import os
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


def _path(chat_id: int) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    return DATA_DIR / f"{chat_id}.json"


def load(chat_id: int) -> dict:
    p = _path(chat_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return _empty()


def save(chat_id: int, state: dict) -> None:
    _path(chat_id).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def reset(chat_id: int) -> dict:
    state = _empty()
    save(chat_id, state)
    return state


def _empty() -> dict:
    return {
        "state": "init",
        "points_db": [],
        "filtered_point_ids": [],
        "pending_map_ids": [],
        "special": {"start_id": None, "mid_id": None, "finish_id": None},
        "assignments": [],
        "participants": [],
        "pairings": [],
        "pending_uploads": [],   # list of {type, file_id, mime_type, file_name}
    }


# Convenience: get a point dict by id from points_db
def get_point(state: dict, point_id: int) -> dict | None:
    for p in state["points_db"]:
        if p["id"] == point_id:
            return p
    return None
