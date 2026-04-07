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

VISION_CFG = {
    "url": os.environ.get("VISION_API_URL", "https://openrouter.ai/api/v1/chat/completions"),
    "key": os.environ.get("VISION_API_KEY", ""),
    "model": os.environ.get("VISION_MODEL", "anthropic/claude-3-5-sonnet"),
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
    "gen": "generate",
    "a": "assign",
    "ex": "export",
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
        "awaiting_special": "הגדר נקודות מיוחדות: /sp <התחלה> <אמצע> <סיום>",
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
        "פקודות (עם קיצורים):\n"
        "/session /s — סשן חדש\n"
        "/status /st — מצב נוכחי\n"
        "/upload_points /up — העלאת טבלת נקודות\n"
        "/upload_map /um — העלאת תמונת מפה (אופציונלי)\n"
        "/skip_map /sm — דילוג על סינון מפה\n"
        "/confirm_map /cm — אישור נקודות מפה\n"
        "/edit_map /em <ids> — עריכה ידנית של נקודות מפה\n"
        "/special /sp <ס> <נקה> <ס> — הגדרת נקודות מיוחדות\n"
        "/generate /gen <נק> <ממוצע> <מינ> <מקס> <משתתפים> — יצירת משימות\n"
        "/upload_participants /upa — העלאת טבלת משתתפים\n"
        "/assign /a — יצירת שיבוץ זוגות\n"
        "/export /ex — יצוא XLS\n"
        "/done /d — סיום העלאות (מפעיל עיבוד)\n\n"
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
        lines.append(f"נקודות מיוחדות: ס={sp.get('start_id')} נקה={sp.get('mid_id')} ס={sp.get('finish_id')}")
    if state["assignments"]:
        lines.append(f"משימות: {len(state['assignments'])}")
    if state["participants"]:
        lines.append(f"משתתפים: {len(state['participants'])}")
    if state["pairings"]:
        lines.append(f"זוגות: {len(state['pairings'])}")
    send(chat_id, "\n".join(lines))


def handle_session(chat_id: int) -> dict:
    state = sess.reset(chat_id)
    send(chat_id, "סשן חדש נפתח!\nהעלה טבלת נקודות ניווט:\n/upload_points (קובץ CSV/XLS או תמונות)")
    return state


def handle_upload_points(chat_id: int, state: dict) -> dict:
    if state["state"] == "init":
        send(chat_id, "❌ התחל סשן חדש תחילה: /session")
        return state
    state["state"] = "awaiting_points_upload"
    state["pending_uploads"] = []
    sess.save(chat_id, state)
    send(chat_id, "שלח את קובץ הנקודות (CSV/XLS) או תמונות של הטבלה.\nכשסיימת: /done")
    return state


def handle_upload_map(chat_id: int, state: dict) -> dict:
    if state["state"] not in ("points_uploaded",):
        send(chat_id, f"❌ לא ניתן כעת. מצב: {_state_label(state)}")
        return state
    state["state"] = "awaiting_map_upload"
    state["pending_uploads"] = []
    sess.save(chat_id, state)
    send(chat_id, "שלח תמונת מפה עם גבול מצויר. כשסיימת: /done")
    return state


def handle_upload_participants(chat_id: int, state: dict) -> dict:
    if state["state"] not in ("assignments_generated",):
        send(chat_id, f"❌ צור משימות תחילה (/gen). מצב: {_state_label(state)}")
        return state
    state["state"] = "awaiting_participants_upload"
    state["pending_uploads"] = []
    sess.save(chat_id, state)
    send(chat_id, "שלח קובץ משתתפים (CSV/XLS) או תמונה. כשסיימת: /done")
    return state


def handle_skip_map(chat_id: int, state: dict) -> dict:
    if state["state"] not in ("points_uploaded", "awaiting_map_upload"):
        send(chat_id, f"❌ לא ניתן כעת. מצב: {_state_label(state)}")
        return state
    state["filtered_point_ids"] = [p["id"] for p in state["points_db"]]
    state["state"] = "awaiting_special"
    sess.save(chat_id, state)
    n = len(state["filtered_point_ids"])
    send(chat_id, f"דולג על סינון מפה — כל {n} נקודות בשימוש.\nהגדר נקודות מיוחדות:\n/special <ס> <נקה> <סיום>")
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
    send(chat_id, f"אושר — {n} נקודות בסינון.\nהגדר נקודות מיוחדות:\n/special <ס> <נקה> <סיום>")
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
    send(chat_id, f"עודכן — {len(ids)} נקודות בסינון.\nהגדר נקודות מיוחדות:\n/special <ס> <נקה> <סיום>")
    return state


def handle_special(chat_id: int, state: dict, args: str) -> dict:
    if state["state"] not in ("awaiting_special", "ready_for_generate"):
        send(chat_id, f"❌ לא ניתן כעת. מצב: {_state_label(state)}")
        return state
    parts = args.split()
    if len(parts) != 3:
        send(chat_id, "שימוש: /special <ס> <נקה> <סיום>\nדוגמה: /sp 240 265 261")
        return state
    try:
        start_id, mid_id, finish_id = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        send(chat_id, "❌ מספרים לא תקינים. שלוש מספרי נקודות נדרשים.")
        return state

    db_ids = {p["id"] for p in state["points_db"]}
    for label, pid in [("ס", start_id), ("נקה", mid_id), ("סיום", finish_id)]:
        if pid not in db_ids:
            send(chat_id, f"❌ נקודה {label} (ID={pid}) לא נמצאה בבסיס הנתונים")
            return state

    state["special"] = {"start_id": start_id, "mid_id": mid_id, "finish_id": finish_id}
    state["state"] = "ready_for_generate"
    sess.save(chat_id, state)
    send(
        chat_id,
        f"נקודות מיוחדות: ס={start_id}, נקה={mid_id}, סיום={finish_id}\n"
        "צור משימות:\n/generate <נקודות> <ממוצע-ק\"מ> <מינ-ק\"מ> <מקס-ק\"מ> <משתתפים>\n"
        "דוגמה: /gen 3 8 6 10 16"
    )
    return state


def handle_generate(chat_id: int, state: dict, args: str) -> dict:
    if state["state"] != "ready_for_generate":
        send(chat_id, f"❌ לא ניתן כעת. מצב: {_state_label(state)}")
        return state

    parts = args.split()
    if len(parts) != 5:
        send(
            chat_id,
            "שימוש: /generate <נקודות> <ממוצע-ק\"מ> <מינ> <מקס> <משתתפים>\n"
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

    state["assignments"] = assignments
    state["state"] = "assignments_generated"
    sess.save(chat_id, state)

    preview = nav_algorithm.format_assignments_preview(assignments, state["points_db"])
    send(chat_id, preview)
    send(chat_id, "משימות נוצרו!\nהעלה טבלת משתתפים:\n/upload_participants (/upa)")
    return state


def handle_assign(chat_id: int, state: dict) -> dict:
    if state["state"] not in ("participants_uploaded",):
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

    if sent_any:
        send(chat_id, "✅ הקבצים נשלחו!")
    return state


# ---------------------------------------------------------------------------
# File upload handler (called when user sends file/photo in upload mode)
# ---------------------------------------------------------------------------

def handle_incoming_file(chat_id: int, state: dict, file_id: str, mime: str, name: str) -> dict:
    """Queue an uploaded file for processing after /done."""
    upload_state = state.get("state", "")
    if upload_state not in ("awaiting_points_upload", "awaiting_map_upload", "awaiting_participants_upload"):
        send(chat_id, "לא ממתין לקבצים כעת. השתמש ב-/up, /um או /upa תחילה.")
        return state

    pending = state.get("pending_uploads", [])
    pending.append({"file_id": file_id, "mime": mime, "name": name})
    state["pending_uploads"] = pending
    sess.save(chat_id, state)
    send(chat_id, f"קובץ התקבל ({name or 'תמונה'}). שלח עוד או: /done")
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

    send(chat_id, "מעבד קבצי נקודות... ⏳")
    downloads = _download_pending(chat_id, pending)
    if not downloads:
        send(chat_id, "❌ הורדת הקבצים נכשלה")
        return state

    images = [(p, m) for p, m in downloads if _is_image(m, p)]
    files = [(p, m) for p, m in downloads if not _is_image(m, p)]

    all_points = []
    try:
        for path, _ in files:
            pts = ingestion.parse_nav_file(str(path))
            all_points.extend(pts)

        if images:
            vision_cfg = VISION_CFG if VISION_CFG.get("key") else {}
            pts = ingestion.parse_nav_images([str(p) for p, _ in images], vision_cfg)
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
    state["pending_uploads"] = []
    state["state"] = "points_uploaded"
    sess.save(chat_id, state)

    preview = ingestion.format_nav_preview(all_points)
    send(chat_id, preview)
    send(chat_id, "נקודות נטענו!\nהעלה מפה לסינון (/upload_map) או דלג (/skip_map)")
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
    state["pending_uploads"] = []
    state["state"] = "participants_uploaded"
    sess.save(chat_id, state)

    preview = ingestion.format_participant_preview(all_parts)
    send(chat_id, preview)

    n_part = len(all_parts)
    n_tasks = len(state["assignments"])
    if n_part % 2 != 0:
        send(chat_id, f"⚠️ מספר משתתפים אי-זוגי ({n_part}) — אחד יהיה יחיד")
    if n_part > n_tasks:
        send(chat_id, f"⚠️ {n_part} משתתפים אך רק {n_tasks} משימות — ייתכן שיבוץ חוזר")

    send(chat_id, "משתתפים נטענו! צור שיבוץ: /assign (/a)")
    return state


# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------

def dispatch(chat_id: int, text: str) -> None:
    state = sess.load(chat_id)

    if not text.startswith("/"):
        # Non-command text — give a gentle nudge
        if state["state"] != "init":
            send(chat_id, f"מצב: {_state_label(state)}\nשלח /help לרשימת פקודות")
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
    elif cmd == "generate":
        state = handle_generate(chat_id, state, args)
    elif cmd == "assign":
        state = handle_assign(chat_id, state)
    elif cmd == "export":
        state = handle_export(chat_id, state)
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
    if upload_state not in ("awaiting_points_upload", "awaiting_map_upload", "awaiting_participants_upload"):
        send(chat_id, "לא ממתין לקבצים. השתמש ב-/up, /um או /upa תחילה.")
        return

    doc = message.get("document")
    photo = message.get("photo")

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

    handle_incoming_file(chat_id, state, file_id, mime, name)


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
