"""
NavMan Telegram Bot — Navigation Drill Coordinator

Long-polling bot that guides a navigation coordinator through:
  1. Uploading and parsing the navigation points DB
  2. Uploading and filtering by map boundary (optional)
  3. Setting special points (start, intermediate, finish)
  4. Generating balanced navigation assignments
  5. Uploading participants and creating pairs
  6. Exporting XLS results

All commands have short aliases. Text is Hebrew.
"""
import json
import os
import sys
import time
from pathlib import Path

import requests as http

# Load .env from the skill root (navman/) so the bot can be started directly
# with `python scripts/bot_handler.py` without manually sourcing .env.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SKILL_DIR = Path(__file__).parent.parent
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
FILE_BASE = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
LONG_POLL_TIMEOUT = 30

ALLOWED_CHAT_IDS = set(
    int(x.strip()) for x in os.environ.get("ALLOWED_CHAT_IDS", "").split(",") if x.strip()
)

# Model priority list: free models first, paid fallbacks after.
# Override via VISION_MODELS (comma-separated) or VISION_MODEL (single).
_DEFAULT_MODELS = [
    "qwen/qwen3.5-flash-02-23",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "meta-llama/llama-3.2-11b-vision-instruct",
]
_env_models = os.environ.get("VISION_MODELS", "")
_env_model = os.environ.get("VISION_MODEL", "")
_models = (
    [m.strip() for m in _env_models.split(",") if m.strip()]
    or ([_env_model] if _env_model else _DEFAULT_MODELS)
)

VISION_CFG = {
    "url": os.environ.get("VISION_API_URL", "https://openrouter.ai/api/v1/chat/completions"),
    "key": os.environ.get("VISION_API_KEY", ""),
    "model": _models[0],
    "models": _models,
}

LOG_FILE = SKILL_DIR / "logs" / "bot_handler.log"
UPLOAD_DIR = SKILL_DIR / "data" / "uploads"
EXPORT_DIR = SKILL_DIR / "data" / "exports"

for _d in (SKILL_DIR / "logs", UPLOAD_DIR, EXPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Imports from skill modules
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).parent))
import session as sess
import ingestion
import map_parser
import nav_algorithm
import participants as part_mod
import export as export_mod

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True, file=sys.stderr)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

_session = http.Session()


def tg(method: str, **kwargs) -> dict:
    url = f"{API_BASE}/{method}"
    try:
        resp = _session.post(url, json=kwargs, timeout=35)
        return resp.json()
    except Exception as e:
        log(f"tg error {method}: {e}")
        return {}


def send(chat_id: int, text: str, **kw) -> None:
    tg("sendMessage", chat_id=chat_id, text=text, **kw)


def send_doc(chat_id: int, file_path: str, caption: str = "") -> None:
    url = f"{API_BASE}/sendDocument"
    with open(file_path, "rb") as f:
        http.post(
            url,
            data={"chat_id": chat_id, "caption": caption},
            files={"document": (Path(file_path).name, f, "application/octet-stream")},
            timeout=60,
        )


def download_file(file_id: str, dest: Path, ext: str = "") -> Path:
    """Download a Telegram file by file_id to dest directory."""
    info = tg("getFile", file_id=file_id)
    file_path = info.get("result", {}).get("file_path", "")
    if not file_path:
        raise RuntimeError("לא הצלחתי להוריד את הקובץ מ-Telegram")
    url = f"{FILE_BASE}/{file_path}"
    if not ext:
        ext = Path(file_path).suffix or ".bin"
    local = dest / f"{file_id}{ext}"
    resp = _session.get(url, timeout=60)
    resp.raise_for_status()
    local.write_bytes(resp.content)
    return local

# ---------------------------------------------------------------------------
# Command aliases
# ---------------------------------------------------------------------------

ALIASES = {
    "s": "session",
    "r": "session",
    "reset": "session",
    "st": "status",
    "h": "help",
    "up": "upload_points",
    "um": "upload_map",
    "upa": "upload_participants",
    "d": "done",
    "sm": "skip_map",
    "cm": "confirm_map",
    "em": "edit_map",
    "sp": "special",
    "ups": "upload_special",
    "gen": "generate",
    "a": "assign",
    "ex": "export",
    "cc": "clear_cache",
    "rp": "remove_point",
    "ap": "add_point",
    "y": "yes",
    "reparse": "reparse",
}

# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _state_label(state: dict) -> str:
    labels = {
        "init": "ממתין להתחלת סשן (/session)",
        "awaiting_points_upload": "ממתין להעלאת טבלת נקודות (/up, אז /d)",
        "points_uploaded": "נקודות נטענו — העלה מפה (/um) או דלג (/sm)",
        "awaiting_map_upload": "ממתין להעלאת תמונת מפה (/um, אז /d)",
        "map_pending_confirm": "מחכה לאישור נקודות המפה (/cm) או עריכה (/em)",
        "awaiting_special": "הגדר נקודות מיוחדות: /sp <id> <id> <id> או העלה תמונה (/ups)",
        "awaiting_special_upload": "ממתין לתמונת נקודות מיוחדות (/ups, אז /d)",
        "ready_for_generate": "מוכן ליצירת משימות: /gen <נקודות> <ממוצע> <מינ> <מקס> <משתתפים>",
        "assignments_generated": "משימות נוצרו — העלה משתתפים (/upa, אז /d)",
        "awaiting_participants_upload": "ממתין להעלאת טבלת משתתפים (/upa, אז /d)",
        "participants_uploaded": "משתתפים נטענו — צור שיבוץ (/a)",
        "fully_assigned": "שיבוץ מוכן — יצא תוצאות (/ex)",
    }
    return labels.get(state.get("state", "init"), state.get("state", "?"))

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def handle_help(chat_id: int, state: dict) -> None:
    text = (
        "NavMan — מערכת הכנת משימות ניווט\n\n"
        "פקודות:\n"
        "/session (/s) — סשן חדש\n"
        "/status (/st) — מצב נוכחי\n"
        "/upload_points (/up) — העלאת טבלת נקודות\n"
        "/upload_map (/um) — העלאת תמונת מפה (אופציונלי)\n"
        "/skip_map (/sm) — דילוג על סינון מפה\n"
        "/confirm_map (/cm) — אישור נקודות מפה\n"
        "/edit_map (/em) <ids> — עריכה ידנית של נקודות מפה\n"
        "/special (/sp) <start_id> <mid_id> <finish_id> — נקודות נה/נב/נס ידנית\n"
        "/upload_special (/ups) — העלאת תמונת נקודות מיוחדות (כל פורמט)\n"
        "/generate (/gen) <pts> <avg_km> <min_km> <max_km> <participants> — יצירת משימות\n"
        "/upload_participants (/upa) — העלאת טבלת משתתפים\n"
        "/assign (/a) — יצירת שיבוץ זוגות\n"
        "/export (/ex) — יצוא XLS\n"
        "/done (/d) — סיום העלאות (מפעיל עיבוד)\n"
        "/clear_cache (/cc) — מחיקת מטמון פרסור (לפרסור מחדש)\n"
        "/remove_point (/rp) <id> — הסרת נקודה מהבסיס\n"
        "/add_point (/ap) <id> <X> <Y> [תיאור] — הוספה/עדכון נקודה ידנית\n\n"
        f"מצב נוכחי: {_state_label(state)}"
    )
    send(chat_id, text)


def handle_status(chat_id: int, state: dict) -> None:
    lines = [f"מצב: {_state_label(state)}"]
    if state["points_db"]:
        lines.append(f"נקודות בבסיס: {len(state['points_db'])}")
    if state["filtered_point_ids"]:
        lines.append(f"נקודות מסוננות: {len(state['filtered_point_ids'])}")
    sp = state.get("special", {})
    if any(sp.values()):
        lines.append(f"נקודות מיוחדות: נה={sp.get('start_id')} נב={sp.get('mid_id')} נס={sp.get('finish_id')}")
    if state["assignments"]:
        lines.append(f"משימות: {len(state['assignments'])}")
    if state["participants"]:
        lines.append(f"משתתפים: {len(state['participants'])}")
    if state["pairings"]:
        lines.append(f"זוגות: {len(state['pairings'])}")
    send(chat_id, "\n".join(lines))


def handle_remove_point(chat_id: int, state: dict, args: str) -> dict:
    if not state.get("points_db"):
        send(chat_id, "❌ אין נקודות בבסיס הנתונים כעת.")
        return state
    try:
        pid = int(args.strip())
    except ValueError:
        send(chat_id, "שימוש: /remove_point (/rp) <מזהה>\nדוגמה: /rp 54")
        return state
    if not any(p["id"] == pid for p in state["points_db"]):
        send(chat_id, f"❌ נקודה {pid} לא נמצאה בבסיס.")
        return state
    state["points_db"] = [p for p in state["points_db"] if p["id"] != pid]
    state["filtered_point_ids"] = [i for i in state["filtered_point_ids"] if i != pid]
    sess.save(chat_id, state)
    send(chat_id, f"✅ נקודה {pid} הוסרה. סה\"כ {len(state['points_db'])} נקודות בבסיס.")
    return state


def handle_add_point(chat_id: int, state: dict, args: str) -> dict:
    if not state.get("points_db") and state["state"] == "init":
        send(chat_id, "❌ התחל סשן תחילה (/s).")
        return state
    parts = args.split()
    if len(parts) < 3:
        send(chat_id, "שימוש: /add_point (/ap) <מזהה> <X> <Y> [תיאור]\nדוגמה: /ap 54 665000 3408000")
        return state
    try:
        pid = int(parts[0])
        x = float(parts[1].replace(",", "."))
        y = float(parts[2].replace(",", "."))
    except ValueError:
        send(chat_id, "❌ ערכים לא תקינים. דוגמה: /ap 54 665000 3408000")
        return state
    if not (0 < pid < 10_000):
        send(chat_id, "❌ מזהה נקודה חייב להיות בין 1 ל-9999.")
        return state
    if not (620_000 <= x <= 900_000) or not (3_300_000 <= y <= 3_700_000):
        send(chat_id, "❌ קואורדינטות מחוץ לטווח ITM תקין.")
        return state
    description = " ".join(parts[3:]) if len(parts) > 3 else ""
    pt = {"id": pid, "x": x, "y": y, "description": description}
    db = {p["id"]: p for p in state.get("points_db", [])}
    action = "עודכנה" if pid in db else "נוספה"
    db[pid] = pt
    state["points_db"] = sorted(db.values(), key=lambda p: p["id"])
    if pid not in state.get("filtered_point_ids", []):
        state["filtered_point_ids"].append(pid)
        state["filtered_point_ids"].sort()
    sess.save(chat_id, state)
    send(chat_id, f"✅ נקודה {pid} {action}: ({x:.0f}, {y:.0f}){' — ' + description if description else ''}. סה\"כ {len(state['points_db'])} נקודות.")
    return state


def handle_clear_cache(chat_id: int) -> None:
    cache_file = SKILL_DIR / "data" / "parse_cache.json"
    if cache_file.exists():
        cache_file.unlink()
        send(chat_id, "✅ מטמון הפרסור נמחק — הפרסור הבא יבצע קריאה חדשה ל-LLM.")
    else:
        send(chat_id, "אין מטמון פרסור לניקוי.")


def handle_session(chat_id: int) -> dict:
    ingestion.release_models()
    state = sess.reset(chat_id)
    state["state"] = "awaiting_points_upload"
    sess.save(chat_id, state)
    send(chat_id, "סשן חדש נפתח!\nהעלה טבלת נקודות ניווט:\n/upload_points (/up) — קובץ CSV/XLS או תמונות")
    return _offer_prev(chat_id, state, "points")


def handle_upload_points(chat_id: int, state: dict) -> dict:
    if state["state"] == "init":
        send(chat_id, "❌ התחל סשן חדש תחילה: /session")
        return state
    state["state"] = "awaiting_points_upload"
    state["pending_uploads"] = []
    sess.save(chat_id, state)
    send(chat_id, "שלח את קובץ הנקודות (CSV/XLS) או תמונות של הטבלה.\nכשסיימת: /done (/d)")
    return state


def handle_upload_map(chat_id: int, state: dict) -> dict:
    if not state.get("points_db"):
        send(chat_id, "❌ אין נקודות בבסיס — העלה נקודות תחילה (/up)")
        return state
    state["state"] = "awaiting_map_upload"
    state["pending_uploads"] = []
    sess.save(chat_id, state)
    send(chat_id, "שלח תמונת מפה עם גבול מצויר. כשסיימת: /done (/d)")
    return _offer_prev(chat_id, state, "map")


def handle_upload_participants(chat_id: int, state: dict) -> dict:
    if state["state"] not in ("assignments_generated", "participants_uploaded", "fully_assigned"):
        send(chat_id, f"❌ צור משימות תחילה (/gen). מצב: {_state_label(state)}")
        return state
    # Re-uploading clears previous participants and pairings
    if state.get("pairings"):
        state["pairings"] = []
    state["state"] = "awaiting_participants_upload"
    state["pending_uploads"] = []
    sess.save(chat_id, state)
    send(chat_id, "שלח קובץ משתתפים (CSV/XLS) או תמונה. כשסיימת: /done (/d)")
    return _offer_prev(chat_id, state, "participants")


def handle_skip_map(chat_id: int, state: dict) -> dict:
    if not state.get("points_db"):
        send(chat_id, f"❌ לא ניתן כעת. מצב: {_state_label(state)}")
        return state
    state["filtered_point_ids"] = [p["id"] for p in state["points_db"]]
    state["state"] = "awaiting_special"
    sess.save(chat_id, state)
    n = len(state["filtered_point_ids"])
    send(chat_id, f"דולג על סינון מפה — כל {n} נקודות בשימוש.\nהגדר נקודות מיוחדות:\n/special (/sp) <start_id> <mid_id> <finish_id>\nאו העלה תמונה: /upload_special (/ups)")
    return state


def handle_confirm_map(chat_id: int, state: dict) -> dict:
    if state["state"] != "map_pending_confirm":
        send(chat_id, f"❌ לא ניתן כעת. מצב: {_state_label(state)}")
        return state
    state["filtered_point_ids"] = state["pending_map_ids"][:]
    state["pending_map_ids"] = []
    state["state"] = "awaiting_special"
    sess.save(chat_id, state)
    n = len(state["filtered_point_ids"])
    send(chat_id, f"אושר — {n} נקודות בסינון.\nהגדר נקודות מיוחדות:\n/special (/sp) <start_id> <mid_id> <finish_id>\nאו העלה תמונה: /upload_special (/ups)")
    return state


def handle_edit_map(chat_id: int, state: dict, args: str) -> dict:
    if state["state"] not in ("map_pending_confirm", "awaiting_special", "ready_for_generate"):
        send(chat_id, f"❌ לא ניתן כעת. מצב: {_state_label(state)}")
        return state
    try:
        ids = [int(x.strip()) for x in args.replace(",", " ").split() if x.strip()]
        if not ids:
            raise ValueError
    except ValueError:
        send(chat_id, "שימוש: /edit_map <מספרים מופרדים בפסיק>\nדוגמה: /em 240,241,243,265")
        return state

    db_ids = {p["id"] for p in state["points_db"]}
    invalid = [i for i in ids if i not in db_ids]
    if invalid:
        send(chat_id, f"❌ נקודות לא קיימות בבסיס: {invalid}")
        return state

    state["filtered_point_ids"] = ids
    state["pending_map_ids"] = []
    if state["state"] == "map_pending_confirm":
        state["state"] = "awaiting_special"
    sess.save(chat_id, state)
    send(chat_id, f"עודכן — {len(ids)} נקודות בסינון.\nהגדר נקודות מיוחדות:\n/special (/sp) <start_id> <mid_id> <finish_id>\nאו העלה תמונה: /upload_special (/ups)")
    return state


def handle_special(chat_id: int, state: dict, args: str) -> dict:
    if state["state"] not in ("awaiting_special", "ready_for_generate"):
        send(chat_id, f"❌ לא ניתן כעת. מצב: {_state_label(state)}")
        return state
    parts = args.split()
    if len(parts) != 3:
        send(chat_id, "שימוש: /special (/sp) <start_id> <mid_id> <finish_id>\nדוגמה: /sp 240 265 261")
        return state
    try:
        start_id, mid_id, finish_id = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        send(chat_id, "❌ מספרים לא תקינים. שלושה מספרי נקודות נדרשים.")
        return state

    db_ids = {p["id"] for p in state["points_db"]}
    for label, pid in [("נה (start)", start_id), ("נב (mid)", mid_id), ("נס (finish)", finish_id)]:
        if pid not in db_ids:
            send(chat_id, f"❌ נקודה {label} (ID={pid}) לא נמצאה בבסיס הנתונים")
            return state

    state["special"] = {"start_id": start_id, "mid_id": mid_id, "finish_id": finish_id}
    state["state"] = "ready_for_generate"
    sess.save(chat_id, state)
    send(
        chat_id,
        f"נקודות מיוחדות: נה={start_id}, נב={mid_id}, נס={finish_id}\n"
        "צור משימות:\n/generate (/gen) <pts> <avg_km> <min_km> <max_km> <participants>\n"
        "דוגמה: /gen 3 8 6 10 16"
    )
    return state


def handle_generate(chat_id: int, state: dict, args: str) -> dict:
    if state["state"] not in ("ready_for_generate", "assignments_generated", "awaiting_participants_upload", "participants_uploaded", "fully_assigned"):
        send(chat_id, f"❌ לא ניתן כעת. מצב: {_state_label(state)}")
        return state

    parts = args.split()
    if len(parts) != 5:
        send(
            chat_id,
            "שימוש: /generate (/gen) <pts> <avg_km> <min_km> <max_km> <participants>\n"
            "דוגמה: /gen 3 8 6 10 16",
        )
        return state

    try:
        n_pts = int(parts[0])
        avg_km = float(parts[1])
        min_km = float(parts[2])
        max_km = float(parts[3])
        n_part = int(parts[4])
    except ValueError:
        send(chat_id, "❌ ערכים לא תקינים. דוגמה: /gen 3 8 6 10 16")
        return state

    if min_km >= max_km:
        send(chat_id, "❌ מרחק מינימום חייב להיות קטן ממקסימום")
        return state
    if n_part < 2:
        send(chat_id, "❌ מספר משתתפים חייב להיות לפחות 2")
        return state

    send(chat_id, "מחשב משימות ניווט... ⏳")

    try:
        assignments = nav_algorithm.generate_assignments(
            points_db=state["points_db"],
            filtered_point_ids=state["filtered_point_ids"],
            special=state["special"],
            n_per_nav=n_pts,
            avg_km=avg_km,
            min_km=min_km,
            max_km=max_km,
            n_participants=n_part,
        )
    except ValueError as e:
        send(chat_id, f"❌ שגיאה: {e}")
        return state

    # Re-generating clears forward progress
    if state.get("pairings") or state.get("participants"):
        state["pairings"] = []
        state["participants"] = []

    state["assignments"] = assignments
    state["state"] = "assignments_generated"
    sess.save(chat_id, state)

    preview = nav_algorithm.format_assignments_preview(assignments, state["points_db"])
    send(chat_id, preview)

    # Coverage report
    used_ids = {pid for a in assignments for pid in a["points"]}
    available = len(state["filtered_point_ids"])
    by_section: dict[str, list[float]] = {}
    for a in assignments:
        by_section.setdefault(a["section"], []).append(a["length_km"])
    section_lines = "  ".join(
        f"{sec}: {len(dists)} משימות, ממוצע {sum(dists)/len(dists):.1f}ק\"מ"
        for sec, dists in sorted(by_section.items())
    )
    send(chat_id,
         f"📊 כיסוי: {len(used_ids)} נקודות בשימוש מתוך {available} זמינות\n{section_lines}")

    send(chat_id, "משימות נוצרו!\nהעלה טבלת משתתפים:\n/upload_participants (/upa)\nאחר כך: /done (/d)")
    return state


def handle_assign(chat_id: int, state: dict) -> dict:
    if state["state"] not in ("participants_uploaded", "fully_assigned"):
        send(chat_id, f"❌ העלה משתתפים תחילה (/upa). מצב: {_state_label(state)}")
        return state

    if not state["assignments"]:
        send(chat_id, "❌ אין משימות — הפעל /generate תחילה")
        return state

    try:
        sorted_parts = part_mod.sort_participants(state["participants"])
        pairs = part_mod.pair_participants(sorted_parts)
        pairings = part_mod.assign_tasks(pairs, state["assignments"])
    except Exception as e:
        send(chat_id, f"❌ שגיאה בשיבוץ: {e}")
        return state

    has_solo = any(pr["p2_name"] == "" for pr in pairings)
    state["pairings"] = pairings
    state["state"] = "fully_assigned"
    sess.save(chat_id, state)

    preview = part_mod.format_pairings_preview(pairings)
    send(chat_id, preview)
    if has_solo:
        send(chat_id, "⚠️ מספר משתתפים אי-זוגי — משתתף אחד ללא שותף")
    send(chat_id, "שיבוץ הושלם! יצא תוצאות: /export (/ex)")
    return state


def handle_export(chat_id: int, state: dict) -> dict:
    if not state["assignments"] and not state["pairings"]:
        send(chat_id, "❌ אין נתונים לייצוא")
        return state

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_dir = EXPORT_DIR / str(chat_id)
    out_dir.mkdir(exist_ok=True)

    sent_any = False
    if state["assignments"]:
        try:
            path = export_mod.export_assignments(state["assignments"], state["points_db"], str(out_dir))
            send_doc(chat_id, path, caption="משימות ניווט")
            sent_any = True
        except Exception as e:
            send(chat_id, f"❌ שגיאה בייצוא משימות: {e}")

    if state["pairings"]:
        try:
            path = export_mod.export_pairings(state["pairings"], str(out_dir))
            send_doc(chat_id, path, caption="שיבוץ זוגות")
            sent_any = True
        except Exception as e:
            send(chat_id, f"❌ שגיאה בייצוא זוגות: {e}")

    if sent_any and state["assignments"] and state["pairings"]:
        try:
            path = export_mod.export_combined(state["pairings"], state["points_db"], str(out_dir))
            send_doc(chat_id, path, caption="שיבוץ משולב")
        except Exception as e:
            send(chat_id, f"❌ שגיאה בייצוא משולב: {e}")
    if sent_any:
        send(chat_id, "✅ הקבצים נשלחו!")
        ingestion.release_models()
    return state


# ---------------------------------------------------------------------------
# Prev-session reuse helpers
# ---------------------------------------------------------------------------

def _offer_prev(chat_id: int, state: dict, key: str) -> dict:
    """If prev session has data for 'key', send an offer message and set _reuse_offer."""
    prev = sess.load_prev(chat_id)
    if not prev:
        return state

    if key == "points" and prev.get("points_db"):
        n = len(prev["points_db"])
        state["_reuse_offer"] = key
        sess.save(chat_id, state)
        send(chat_id,
             f"💾 נמצא סשן קודם עם {n} נקודות.\n"
             "/yes — השתמש בנקודות הקודמות\n"
             "/reparse — פרסר מחדש תמונות קיימות\n"
             "או המשך להעלאה חדשה")
    elif key == "map" and prev.get("filtered_point_ids"):
        n = len(prev["filtered_point_ids"])
        state["_reuse_offer"] = key
        sess.save(chat_id, state)
        send(chat_id,
             f"💾 נמצא סשן קודם עם {n} נקודות מסוננות.\n"
             "/yes — השתמש בסינון הקודם\n"
             "/reparse — פרסר מחדש תמונת מפה\n"
             "או המשך להעלאה חדשה")
    elif key == "special" and any(prev.get("special", {}).values()):
        sp = prev["special"]
        state["_reuse_offer"] = key
        sess.save(chat_id, state)
        send(chat_id,
             f"💾 נמצא סשן קודם: נה={sp.get('start_id')} נב={sp.get('mid_id')} נס={sp.get('finish_id')}.\n"
             "/yes — השתמש בנקודות המיוחדות הקודמות\n"
             "/reparse — פרסר מחדש תמונה\n"
             "או הגדר ידנית: /sp <start_id> <mid_id> <finish_id>")
    elif key == "participants" and prev.get("participants"):
        n = len(prev["participants"])
        state["_reuse_offer"] = key
        sess.save(chat_id, state)
        send(chat_id,
             f"💾 נמצא סשן קודם עם {n} משתתפים.\n"
             "/yes — השתמש במשתתפים הקודמים\n"
             "/reparse — פרסר מחדש קבצים קיימים\n"
             "או המשך להעלאה חדשה")
    return state


def handle_yes(chat_id: int, state: dict) -> dict:
    offer = state.get("_reuse_offer")
    if not offer:
        send(chat_id, "❌ אין הצעה לשימוש חוזר כעת.")
        return state

    prev = sess.load_prev(chat_id)
    if not prev:
        send(chat_id, "❌ לא נמצא סשן קודם.")
        state.pop("_reuse_offer", None)
        sess.save(chat_id, state)
        return state

    state.pop("_reuse_offer", None)
    prev_files = prev.get("source_files", {})

    if offer == "points":
        state["points_db"] = prev["points_db"]
        state["filtered_point_ids"] = prev.get("filtered_point_ids", [p["id"] for p in prev["points_db"]])
        state["source_files"]["points"] = prev_files.get("points", [])
        state["media_groups"].update(prev.get("media_groups", {}))
        state["pending_uploads"] = []
        state["state"] = "points_uploaded"
        sess.save(chat_id, state)
        n = len(state["points_db"])
        send(chat_id, f"✅ נטענו {n} נקודות מהסשן הקודם.\nהעלה מפה לסינון (/um) או דלג (/sm)")
        return _offer_prev(chat_id, state, "map")

    elif offer == "map":
        state["filtered_point_ids"] = prev.get("filtered_point_ids", [])
        state["source_files"]["map"] = prev_files.get("map", [])
        state["media_groups"].update(prev.get("media_groups", {}))
        state["state"] = "awaiting_special"
        sess.save(chat_id, state)
        n = len(state["filtered_point_ids"])
        send(chat_id,
             f"✅ נטענו {n} נקודות מסוננות מהסשן הקודם.\n"
             "הגדר נקודות מיוחדות:\n/special (/sp) <start_id> <mid_id> <finish_id>\nאו העלה תמונה: /upload_special (/ups)")
        return _offer_prev(chat_id, state, "special")

    elif offer == "special":
        state["special"] = prev.get("special", {})
        state["source_files"]["special"] = prev_files.get("special", [])
        state["media_groups"].update(prev.get("media_groups", {}))
        state["state"] = "ready_for_generate"
        sess.save(chat_id, state)
        sp = state["special"]
        send(chat_id,
             f"✅ נקודות מיוחדות: נה={sp.get('start_id')}, נב={sp.get('mid_id')}, נס={sp.get('finish_id')}\n"
             "צור משימות:\n/generate (/gen) <pts> <avg_km> <min_km> <max_km> <participants>")
        return state

    elif offer == "participants":
        state["participants"] = prev.get("participants", [])
        state["source_files"]["participants"] = prev_files.get("participants", [])
        state["media_groups"].update(prev.get("media_groups", {}))
        state["state"] = "participants_uploaded"
        sess.save(chat_id, state)
        n = len(state["participants"])
        send(chat_id, f"✅ נטענו {n} משתתפים מהסשן הקודם.\nצור שיבוץ: /assign (/a)")
        return state

    return state


def handle_reparse(chat_id: int, state: dict) -> dict:
    offer = state.get("_reuse_offer")
    if not offer:
        send(chat_id, "❌ אין הצעה לפרסור מחדש כעת.")
        return state

    prev = sess.load_prev(chat_id)
    src_files = (prev or {}).get("source_files", {}).get(offer, []) if prev else []
    state.pop("_reuse_offer", None)

    if not src_files:
        send(chat_id, "❌ אין קבצים שמורים לפרסור מחדש. העלה קבצים חדשים.")
        sess.save(chat_id, state)
        return state

    pending = [{"file_id": fid, "mime": "", "name": ""} for fid in src_files]
    state["pending_uploads"] = pending

    if offer == "points":
        state["state"] = "awaiting_points_upload"
        sess.save(chat_id, state)
        send(chat_id, f"מפרסר מחדש {len(src_files)} קבצים... ⏳")
        return _process_points_uploads(chat_id, state, pending)
    elif offer == "map":
        state["state"] = "awaiting_map_upload"
        sess.save(chat_id, state)
        send(chat_id, "מפרסר מחדש תמונת מפה... ⏳")
        return _process_map_upload(chat_id, state, pending)
    elif offer == "special":
        state["state"] = "awaiting_special_upload"
        sess.save(chat_id, state)
        send(chat_id, "מפרסר מחדש תמונת נקודות מיוחדות... ⏳")
        return _process_special_upload(chat_id, state, pending)
    elif offer == "participants":
        state["state"] = "awaiting_participants_upload"
        sess.save(chat_id, state)
        send(chat_id, "מפרסר מחדש קובץ משתתפים... ⏳")
        return _process_participants_uploads(chat_id, state, pending)

    return state


# ---------------------------------------------------------------------------
# File upload handler (called when user sends file/photo in upload mode)
# ---------------------------------------------------------------------------

def _extract_file_ids_from_message(msg: dict) -> list[tuple[str, str, str]]:
    """Extract (file_id, mime, name) from a Telegram message dict."""
    results = []
    doc = msg.get("document")
    photos = msg.get("photo")
    if doc:
        results.append((doc["file_id"], doc.get("mime_type", ""), doc.get("file_name", "")))
    if photos:
        largest = sorted(photos, key=lambda x: x.get("file_size", 0), reverse=True)
        results.append((largest[0]["file_id"], "image/jpeg", ""))
    return results


def _find_album_for_file(state: dict, chat_id: int, file_id: str) -> tuple[str | None, list]:
    """Return (media_group_id, all_file_ids) if file_id belongs to any known album."""
    for group_id, file_ids in state.get("media_groups", {}).items():
        if file_id in file_ids:
            return group_id, file_ids
    prev = sess.load_prev(chat_id)
    if prev:
        for group_id, file_ids in prev.get("media_groups", {}).items():
            if file_id in file_ids:
                return group_id, file_ids
    return None, []


def handle_incoming_file(chat_id: int, state: dict, file_id: str, mime: str, name: str, media_group_id: str = None) -> dict:
    """Queue an uploaded file for processing after /done."""
    upload_state = state.get("state", "")
    if upload_state not in ("awaiting_points_upload", "awaiting_map_upload", "awaiting_participants_upload", "awaiting_special_upload"):
        send(chat_id, "לא ממתין לקבצים כעת. השתמש ב-/up, /um או /upa תחילה.")
        return state

    # Track album membership
    if media_group_id:
        album = state.get("media_groups", {})
        album.setdefault(media_group_id, [])
        if file_id not in album[media_group_id]:
            album[media_group_id].append(file_id)
        state["media_groups"] = album

    pending = state.get("pending_uploads", [])
    # Avoid duplicates
    if any(p["file_id"] == file_id for p in pending):
        return state
    pending.append({"file_id": file_id, "mime": mime, "name": name})
    state["pending_uploads"] = pending
    sess.save(chat_id, state)
    n_pending = len(state.get("pending_uploads", []))
    send(chat_id, f"קובץ התקבל ({name or 'תמונה'}) — סה\"כ {n_pending}. שלח עוד או: /done (/d)")
    return state


# ---------------------------------------------------------------------------
# /done handler — process all queued uploads
# ---------------------------------------------------------------------------

def handle_done(chat_id: int, state: dict) -> dict:
    upload_state = state.get("state", "")
    pending = state.get("pending_uploads", [])

    if upload_state == "awaiting_points_upload":
        return _process_points_uploads(chat_id, state, pending)
    elif upload_state == "awaiting_map_upload":
        return _process_map_upload(chat_id, state, pending)
    elif upload_state == "awaiting_participants_upload":
        return _process_participants_uploads(chat_id, state, pending)
    elif upload_state == "awaiting_special_upload":
        return _process_special_upload(chat_id, state, pending)
    else:
        send(chat_id, f"❌ לא ממתין להעלאות כעת. מצב: {_state_label(state)}")
        return state


def _download_pending(chat_id: int, pending: list[dict]) -> list[tuple[Path, str]]:
    """Download all pending uploads. Returns list of (local_path, mime) tuples."""
    ul_dir = UPLOAD_DIR / str(chat_id)
    ul_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for item in pending:
        mime = item.get("mime", "")
        name = item.get("name", "")
        ext = Path(name).suffix if name else _ext_from_mime(mime)
        try:
            local = download_file(item["file_id"], ul_dir, ext)
            results.append((local, mime))
        except Exception as e:
            log(f"Download failed for {item['file_id']}: {e}")
    return results


def _ext_from_mime(mime: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "text/csv": ".csv",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.ms-excel": ".xls",
    }.get(mime, ".bin")


def _is_image(mime: str, path: Path) -> bool:
    return mime.startswith("image/") or path.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif")


def _process_points_uploads(chat_id: int, state: dict, pending: list[dict]) -> dict:
    if not pending:
        send(chat_id, "❌ לא הועלו קבצים. שלח קבצים ואז /done")
        return state

    n_files = len(pending)
    send(chat_id, f"מוריד {n_files} קבצים... ⏳")
    downloads = _download_pending(chat_id, pending)
    if not downloads:
        send(chat_id, "❌ הורדת הקבצים נכשלה")
        return state

    images = [(p, m) for p, m in downloads if _is_image(m, p)]
    files = [(p, m) for p, m in downloads if not _is_image(m, p)]

    all_points = []
    failed_images = []
    try:
        for path, _ in files:
            pts = ingestion.parse_nav_file(str(path))
            all_points.extend(pts)

        if images:
            send(chat_id, f"שולח {len(images)} תמונות לניתוח AI... ⏳ (עשוי לקחת כדקה לכל תמונה)")
            vision_cfg = VISION_CFG if VISION_CFG.get("key") else {}
            pts, failed_images = ingestion.parse_nav_images([str(p) for p, _ in images], vision_cfg)
            all_points.extend(pts)
    except Exception as e:
        send(chat_id, f"❌ שגיאה בעיבוד הקבצים: {e}")
        return state

    if not all_points:
        send(chat_id, "❌ לא נמצאו נקודות בקבצים שהועלו")
        return state

    # Deduplicate by ID (last wins)
    seen = {}
    for p in all_points:
        seen[p["id"]] = p
    all_points = sorted(seen.values(), key=lambda x: x["id"])

    state["points_db"] = all_points
    state["filtered_point_ids"] = [p["id"] for p in all_points]
    state["source_files"]["points"] = [item["file_id"] for item in pending]
    state["pending_uploads"] = []
    state["state"] = "points_uploaded"
    sess.save(chat_id, state)

    preview = ingestion.format_nav_preview(all_points)
    send(chat_id, preview)

    if failed_images:
        send(chat_id, f"⚠️ לא הצלחתי לנתח {len(failed_images)} תמונות: {', '.join(failed_images)}\nניתן להוסיף נקודות חסרות ידנית.")

    # Report gaps in ID sequence
    ids = sorted(p["id"] for p in all_points)
    gaps = [i for i in range(ids[0], ids[-1] + 1) if i not in set(ids)]
    if gaps:
        gap_str = ", ".join(str(g) for g in gaps[:20])
        if len(gaps) > 20:
            gap_str += f" ועוד {len(gaps) - 20}"
        send(chat_id,
             f"⚠️ חסרות {len(gaps)} נקודות ברצף: {gap_str}\n"
             "ניתן להוסיף אותן ידנית — פשוט כתוב למשל:\n"
             "נקודה 54: 665000, 3408000")

    # Export points as XLS so user can verify
    try:
        out_dir = EXPORT_DIR / str(chat_id)
        out_dir.mkdir(exist_ok=True)
        pts_path = export_mod.export_points(all_points, str(out_dir))
        send_doc(chat_id, pts_path, caption="טבלת נקודות — לאימות ותיקון")
    except Exception as e:
        log(f"points export failed: {e}")

    send(chat_id, "נקודות נטענו!\nהעלה מפה לסינון (/upload_map (/um)) או דלג (/skip_map (/sm))")
    return state


def _process_map_upload(chat_id: int, state: dict, pending: list[dict]) -> dict:
    if not pending:
        send(chat_id, "❌ לא הועלה תמונה. שלח תמונת מפה ואז /done")
        return state

    send(chat_id, "מנתח תמונת מפה עם AI... ⏳")
    downloads = _download_pending(chat_id, pending)
    if not downloads:
        send(chat_id, "❌ הורדת התמונה נכשלה")
        return state

    # Use first image (map is a single image)
    map_path, _ = downloads[0]
    all_ids = [p["id"] for p in state["points_db"]]

    try:
        filtered_ids = map_parser.parse_map_image(str(map_path), all_ids, VISION_CFG)
    except Exception as e:
        send(chat_id, f"❌ ניתוח מפה נכשל: {e}\nהשתמש ב-/edit_map <ids> להגדרה ידנית")
        state["state"] = "map_pending_confirm"
        state["pending_map_ids"] = all_ids  # fallback to all
        sess.save(chat_id, state)
        return state

    state["pending_map_ids"] = filtered_ids
    state["source_files"]["map"] = [item["file_id"] for item in pending]
    state["pending_uploads"] = []
    state["state"] = "map_pending_confirm"
    sess.save(chat_id, state)

    preview = map_parser.format_map_preview(filtered_ids, len(all_ids))
    send(chat_id, preview)
    return state


def _process_participants_uploads(chat_id: int, state: dict, pending: list[dict]) -> dict:
    if not pending:
        send(chat_id, "❌ לא הועלו קבצים. שלח קבצים ואז /done")
        return state

    send(chat_id, "מעבד קובץ משתתפים... ⏳")
    downloads = _download_pending(chat_id, pending)
    if not downloads:
        send(chat_id, "❌ הורדת הקבצים נכשלה")
        return state

    images = [(p, m) for p, m in downloads if _is_image(m, p)]
    files = [(p, m) for p, m in downloads if not _is_image(m, p)]

    all_parts = []
    try:
        for path, _ in files:
            ps = ingestion.parse_participant_file(str(path))
            all_parts.extend(ps)
        if images:
            vision_cfg = VISION_CFG if VISION_CFG.get("key") else {}
            ps = ingestion.parse_participant_images([str(p) for p, _ in images], vision_cfg)
            all_parts.extend(ps)
    except Exception as e:
        send(chat_id, f"❌ שגיאה בעיבוד משתתפים: {e}")
        return state

    if not all_parts:
        send(chat_id, "❌ לא נמצאו משתתפים בקבצים שהועלו")
        return state

    state["participants"] = all_parts
    state["source_files"]["participants"] = [item["file_id"] for item in pending]
    state["pending_uploads"] = []
    state["state"] = "participants_uploaded"
    sess.save(chat_id, state)

    preview = ingestion.format_participant_preview(all_parts)
    send(chat_id, preview)

    n_part = len(all_parts)
    n_tasks = len(state["assignments"])
    missing_scores = [p["name"] for p in all_parts if not str(p.get("score_raw", "")).strip() or str(p.get("score_raw", "")).strip() in ("0", "None", "")]
    if missing_scores:
        send(chat_id, f"⚠️ חסר ציון ל-{len(missing_scores)} משתתפים: {', '.join(missing_scores[:10])}\nהשיבוץ יסתמך על ציון 0 עבורם.")
    if n_part % 2 != 0:
        send(chat_id, f"⚠️ מספר משתתפים אי-זוגי ({n_part}) — אחד יהיה יחיד")
    if n_part > n_tasks:
        send(chat_id, f"⚠️ {n_part} משתתפים אך רק {n_tasks} משימות — ייתכן שיבוץ חוזר")

    send(chat_id, "משתתפים נטענו! צור שיבוץ: /assign (/a)")
    return state


# ---------------------------------------------------------------------------
# Special points upload
# ---------------------------------------------------------------------------

def handle_upload_special(chat_id: int, state: dict) -> dict:
    if state["state"] not in ("awaiting_special", "ready_for_generate"):
        send(chat_id, f"❌ לא ניתן כעת. מצב: {_state_label(state)}")
        return state
    state["state"] = "awaiting_special_upload"
    state["pending_uploads"] = []
    sess.save(chat_id, state)
    send(chat_id,
         "שלח תמונה של נקודות נה/נב/נס (כל פורמט קואורדינטות).\n"
         "סדר בתמונה: נה ראשון, נב שני, נס שלישי.\n"
         "כשסיימת: /done (/d)")
    return _offer_prev(chat_id, state, "special")


def _process_special_upload(chat_id: int, state: dict, pending: list[dict]) -> dict:
    if not pending:
        send(chat_id, "❌ לא הועלתה תמונה. שלח תמונה ואז /done")
        return state

    send(chat_id, "מנתח נקודות מיוחדות... ⏳")
    downloads = _download_pending(chat_id, pending)
    if not downloads:
        send(chat_id, "❌ הורדת התמונה נכשלה")
        return state

    img_path, _ = downloads[0]
    try:
        coords = ingestion.parse_special_image(str(img_path), VISION_CFG)
    except Exception as e:
        send(chat_id, f"❌ ניתוח נכשל: {e}\nהשתמש ב-/sp ידנית.")
        state["state"] = "awaiting_special"
        sess.save(chat_id, state)
        return state

    if not coords:
        send(chat_id, "❌ לא נמצאו קואורדינטות. נסה /sp ידנית.")
        state["state"] = "awaiting_special"
        sess.save(chat_id, state)
        return state

    # Assign new IDs beyond current max
    max_id = max((p["id"] for p in state["points_db"]), default=0)
    labels = ["נה (התחלה)", "נב (אמצע)", "נס (סיום)"]
    role_keys = ["start_id", "mid_id", "finish_id"]
    added = []
    db = {p["id"]: p for p in state["points_db"]}

    for i, coord in enumerate(coords[:3]):
        new_id = max_id + i + 1
        pt = {"id": new_id, "x": coord["x"], "y": coord["y"],
              "description": coord.get("label", labels[i])}
        db[new_id] = pt
        state["special"][role_keys[i]] = new_id
        added.append(f"{labels[i]}: ID={new_id} ({coord['x']:.0f}, {coord['y']:.0f})"
                     + (f" — {coord['label']}" if coord.get("label") else ""))

    state["points_db"] = sorted(db.values(), key=lambda p: p["id"])
    # Keep filtered list in sync
    for pt in added:
        pass  # IDs already added to db; rebuild filtered if not filtered yet
    state["filtered_point_ids"] = [p["id"] for p in state["points_db"]
                                    if p["id"] in set(state["filtered_point_ids"])
                                    or p["id"] > max_id]

    state["source_files"]["special"] = [item["file_id"] for item in pending]
    state["pending_uploads"] = []
    state["state"] = "ready_for_generate"
    sess.save(chat_id, state)

    summary = "\n".join(added)
    sp = state["special"]
    send(chat_id,
         f"✅ נקודות מיוחדות נקלטו:\n{summary}\n\n"
         f"נה={sp['start_id']}, נב={sp['mid_id']}, נס={sp['finish_id']}\n"
         "לשינוי ידני: /sp <start_id> <mid_id> <finish_id>\n"
         "ליצירת משימות: /gen <pts> <avg_km> <min_km> <max_km> <participants>")
    return state


# ---------------------------------------------------------------------------
# Free-text / LLM oversight handler
# ---------------------------------------------------------------------------

def handle_free_text(chat_id: int, state: dict, text: str) -> dict:
    """
    LLM-mediated handler for non-command input.
    Understands user intent and can modify state (add/correct points).
    Falls back to a simple nudge if LLM is unavailable.
    """
    if not VISION_CFG.get("key"):
        if state["state"] != "init":
            send(chat_id, f"מצב: {_state_label(state)}\nשלח /help לרשימת פקודות")
        return state

    import requests as _req

    sp = state.get("special", {})
    ctx = "\n".join(filter(None, [
        f"State: {state.get('state', 'init')}",
        f"Points in DB: {len(state.get('points_db', []))}",
        f"Filtered points: {len(state.get('filtered_point_ids', []))}",
        f"Assignments: {len(state.get('assignments', []))}",
        f"Participants: {len(state.get('participants', []))}",
        (f"Special: נה={sp.get('start_id')} נב={sp.get('mid_id')} נס={sp.get('finish_id')}" if any(sp.values()) else ""),
    ]))

    system = (
        "You are NavMan, a Hebrew-language assistant for a navigation drill coordinator bot.\n"
        "The bot manages: uploading a nav-points table, filtering by map, setting start/mid/finish points, "
        "generating assignments, uploading participants, and exporting results.\n\n"
        "Commands: /session(/s), /status(/st), /upload_points(/up), /done(/d), /skip_map(/sm), "
        "/upload_map(/um), /confirm_map(/cm), /edit_map(/em), /special(/sp) <start> <mid> <finish>, "
        "/generate(/gen) <pts> <avg_km> <min_km> <max_km> <participants>, "
        "/upload_participants(/upa), /assign(/a), /export(/ex).\n\n"
        f"Current context:\n{ctx}\n\n"
        "The user sent free text. Respond helpfully in Hebrew (1-3 sentences).\n"
        "If the user wants to ADD or CORRECT a navigation point, extract it and reply with a JSON block:\n"
        "```json\n{\"action\":\"add_point\",\"id\":54,\"x\":665000.0,\"y\":3408000.0,\"description\":\"\"}\n```\n"
        "Use action 'add_point' both for new points and corrections (overwrites by ID). "
        "If the user wants to remove a point: {\"action\":\"remove_point\",\"id\":54}.\n"
        "If the state is 'awaiting_participants_upload' and the user pastes a list of participants "
        "(names and scores), extract it and reply with:\n"
        "```json\n{\"action\":\"set_participants\",\"participants\":[{\"index\":1,\"name\":\"שם\",\"score_raw\":\"85\"}]}\n```\n"
        "Only include the JSON block when the user clearly provides data. "
        "Always also include a natural-language Hebrew message."
    )

    try:
        payload = {
            "model": VISION_CFG["model"],
            "max_tokens": 400,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
        }
        headers = {"Authorization": f"Bearer {VISION_CFG['key']}", "Content-Type": "application/json"}
        resp = _req.post(VISION_CFG["url"], headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if "choices" not in data:
            raise ValueError("no choices")
        reply = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log(f"handle_free_text LLM error: {e}")
        send(chat_id, f"מצב: {_state_label(state)}\nשלח /help לרשימת פקודות")
        return state

    # Extract and execute any structured action
    import re as _re, json as _json
    action_match = _re.search(r"```json\s*(\{.*?\})\s*```", reply, _re.DOTALL)
    if action_match:
        try:
            action = _json.loads(action_match.group(1))
            act = action.get("action")
            if act == "add_point":
                pid = int(action["id"])
                pt = {"id": pid, "x": float(action["x"]), "y": float(action["y"]),
                      "description": action.get("description", "")}
                db = {p["id"]: p for p in state.get("points_db", [])}
                db[pid] = pt
                state["points_db"] = sorted(db.values(), key=lambda p: p["id"])
                if pid not in state.get("filtered_point_ids", []):
                    state["filtered_point_ids"].append(pid)
                sess.save(chat_id, state)
                log(f"[{chat_id}] free_text added/corrected point {pid}")
            elif act == "remove_point":
                pid = int(action["id"])
                state["points_db"] = [p for p in state.get("points_db", []) if p["id"] != pid]
                state["filtered_point_ids"] = [i for i in state.get("filtered_point_ids", []) if i != pid]
                sess.save(chat_id, state)
                log(f"[{chat_id}] free_text removed point {pid}")
            elif act == "set_participants":
                participants = action.get("participants", [])
                if participants:
                    state["participants"] = participants
                    state["pairings"] = []
                    state["state"] = "participants_uploaded"
                    sess.save(chat_id, state)
                    log(f"[{chat_id}] free_text set {len(participants)} participants")
        except Exception as e:
            log(f"handle_free_text action parse error: {e}")

    # Send the natural-language part (strip the json block)
    message = _re.sub(r"```json.*?```", "", reply, flags=_re.DOTALL).strip()
    if message:
        send(chat_id, message)

    return state


# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------

def dispatch(chat_id: int, text: str) -> None:
    state = sess.load(chat_id)

    if not text.startswith("/"):
        handle_free_text(chat_id, state, text)
        return

    parts = text.split(None, 1)
    raw_cmd = parts[0].lstrip("/").split("@")[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""
    cmd = ALIASES.get(raw_cmd, raw_cmd)

    log(f"[{chat_id}] cmd={cmd} args={args!r} state={state['state']}")

    if cmd in ("session", "reset"):
        state = handle_session(chat_id)
    elif cmd == "help":
        handle_help(chat_id, state)
    elif cmd == "status":
        handle_status(chat_id, state)
    elif cmd == "upload_points":
        state = handle_upload_points(chat_id, state)
    elif cmd == "upload_map":
        state = handle_upload_map(chat_id, state)
    elif cmd == "upload_participants":
        state = handle_upload_participants(chat_id, state)
    elif cmd == "done":
        state = handle_done(chat_id, state)
    elif cmd == "skip_map":
        state = handle_skip_map(chat_id, state)
    elif cmd == "confirm_map":
        state = handle_confirm_map(chat_id, state)
    elif cmd == "edit_map":
        state = handle_edit_map(chat_id, state, args)
    elif cmd == "special":
        state = handle_special(chat_id, state, args)
    elif cmd == "upload_special":
        state = handle_upload_special(chat_id, state)
    elif cmd == "generate":
        state = handle_generate(chat_id, state, args)
    elif cmd == "assign":
        state = handle_assign(chat_id, state)
    elif cmd == "export":
        state = handle_export(chat_id, state)
    elif cmd == "clear_cache":
        handle_clear_cache(chat_id)
    elif cmd == "yes":
        state = handle_yes(chat_id, state)
    elif cmd == "reparse":
        state = handle_reparse(chat_id, state)
    elif cmd == "remove_point":
        state = handle_remove_point(chat_id, state, args)
    elif cmd == "add_point":
        state = handle_add_point(chat_id, state, args)
    elif cmd == "start":
        if state["state"] == "init":
            send(chat_id, "ברוך הבא ל-NavMan!\nהתחל סשן: /session")
        else:
            handle_status(chat_id, state)
    else:
        send(chat_id, f"פקודה לא מוכרת: /{raw_cmd}\nשלח /help לעזרה")


def handle_file_message(chat_id: int, message: dict) -> None:
    state = sess.load(chat_id)
    upload_state = state.get("state", "")
    doc = message.get("document")
    photo = message.get("photo")
    media_group_id = message.get("media_group_id")

    if upload_state not in ("awaiting_points_upload", "awaiting_map_upload", "awaiting_participants_upload", "awaiting_special_upload"):
        send(chat_id, "לא ממתין לקבצים. השתמש ב-/up, /um או /upa תחילה.")
        return

    if doc:
        file_id = doc["file_id"]
        mime = doc.get("mime_type", "application/octet-stream")
        name = doc.get("file_name", "")
    elif photo:
        # Telegram sends multiple sizes; use the largest
        photo_sizes = sorted(photo, key=lambda x: x.get("file_size", 0), reverse=True)
        file_id = photo_sizes[0]["file_id"]
        mime = "image/jpeg"
        name = ""
    else:
        return

    handle_incoming_file(chat_id, state, file_id, mime, name, media_group_id)


# ---------------------------------------------------------------------------
# Long-polling loop
# ---------------------------------------------------------------------------

def main() -> None:
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN is not set", file=sys.stderr)
        sys.exit(1)

    log("NavMan bot started")
    offset = None

    while True:
        try:
            params = {"timeout": LONG_POLL_TIMEOUT, "allowed_updates": ["message"]}
            if offset is not None:
                params["offset"] = offset

            result = tg("getUpdates", **params)
            updates = result.get("result", [])

            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                if not msg:
                    continue

                chat_id = msg.get("chat", {}).get("id")
                if not chat_id:
                    continue

                if ALLOWED_CHAT_IDS and chat_id not in ALLOWED_CHAT_IDS:
                    log(f"Ignored unauthorized user {chat_id}")
                    continue

                text = msg.get("text", "").strip()
                if text:
                    # If user replies to an old message with files while in an upload state,
                    # add those files to pending before processing the command.
                    reply_to = msg.get("reply_to_message")
                    if reply_to:
                        try:
                            cur_state = sess.load(chat_id)
                            if cur_state.get("state") in (
                                "awaiting_points_upload", "awaiting_map_upload",
                                "awaiting_participants_upload", "awaiting_special_upload",
                            ):
                                for fid, mime, name in _extract_file_ids_from_message(reply_to):
                                    group_id, album_fids = _find_album_for_file(cur_state, chat_id, fid)
                                    if group_id and len(album_fids) > 1:
                                        for album_fid in album_fids:
                                            cur_state = handle_incoming_file(chat_id, cur_state, album_fid, "image/jpeg", "")
                                    else:
                                        cur_state = handle_incoming_file(chat_id, cur_state, fid, mime, name)
                        except Exception as e:
                            log(f"reply_to file extraction error: {e}")
                    try:
                        dispatch(chat_id, text)
                    except Exception as e:
                        log(f"dispatch error: {e}")
                        send(chat_id, f"❌ שגיאה פנימית: {e}")
                elif msg.get("document") or msg.get("photo"):
                    try:
                        handle_file_message(chat_id, msg)
                    except Exception as e:
                        log(f"file handler error: {e}")
                        send(chat_id, f"❌ שגיאה בעיבוד הקובץ: {e}")

        except KeyboardInterrupt:
            log("Bot stopped")
            break
        except Exception as e:
            log(f"Poll error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
