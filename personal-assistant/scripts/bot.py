#!/usr/bin/env python3
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import db
import llm
import scheduler
from flask import Flask, jsonify, request

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path(__file__).parent.parent / "logs" / "bot.log"),
    ],
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
API_PORT = int(os.environ.get("API_PORT", 8766))

ALLOWED_USER_IDS = set(filter(None, os.environ.get("ALLOWED_USER_IDS", "").split(",")))


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------

def send_message(chat_id: str, text: str, parse_mode: str = "HTML"):
    import requests
    try:
        requests.post(
            f"{BASE_URL}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"send_message failed: {e}")


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _describe_schedule(s: dict) -> str:
    t = s["type"]
    if t == "once":
        dt = datetime.fromisoformat(s["datetime"])
        return f"ב-{dt.strftime('%d/%m/%Y %H:%M')}"
    if t == "interval":
        secs = s["seconds"]
        if secs % 86400 == 0:
            n = secs // 86400
            return f"כל {n} {'יום' if n == 1 else 'ימים'}"
        if secs % 3600 == 0:
            n = secs // 3600
            return f"כל {n} {'שעה' if n == 1 else 'שעות'}"
        if secs % 60 == 0:
            n = secs // 60
            return f"כל {n} {'דקה' if n == 1 else 'דקות'}"
        return f"כל {secs} שניות"
    if t == "cron":
        hour = int(s.get("hour", 0))
        minute = int(s.get("minute", 0))
        dow = s.get("day_of_week")
        days_he = {"mon": "ב׳", "tue": "ג׳", "wed": "ד׳", "thu": "ה׳", "fri": "ו׳", "sat": "שבת", "sun": "א׳"}
        if dow:
            return f"כל {days_he.get(dow, dow)} ב-{hour:02d}:{minute:02d}"
        return f"כל יום ב-{hour:02d}:{minute:02d}"
    return ""


def format_todos(todos: list[dict]) -> str:
    if not todos:
        return "אין משימות ברשימה 📋"
    open_todos = [t for t in todos if not t["done"]]
    done_todos = [t for t in todos if t["done"]]
    lines = ["<b>📋 המשימות שלך:</b>\n"]
    i = 1
    for t in open_todos:
        lines.append(f"{i}. ⬜ {t['text']}")
        i += 1
    for t in done_todos:
        lines.append(f"{i}. ✅ <s>{t['text']}</s>")
        i += 1
    lines.append(f"\nפתוחות: {len(open_todos)}  |  הושלמו: {len(done_todos)}")
    return "\n".join(lines)


def format_reminders(reminders: list[dict]) -> str:
    if not reminders:
        return "אין תזכורות פעילות ⏰"
    lines = ["<b>⏰ התזכורות הפעילות שלך:</b>\n"]
    for i, r in enumerate(reminders, 1):
        s = json.loads(r["trigger_data"])
        lines.append(f"{i}. {r['text']} — {_describe_schedule(s)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Ref resolution
# ---------------------------------------------------------------------------

def _resolve_todo(user_id: str, ref: str) -> dict | None:
    todos = db.list_todos(user_id)
    try:
        idx = int(ref) - 1
        if 0 <= idx < len(todos):
            return todos[idx]
    except ValueError:
        pass
    ref_lower = ref.lower()
    return next((t for t in todos if ref_lower in t["text"].lower()), None)


def _resolve_reminder(user_id: str, ref: str) -> dict | None:
    reminders = db.list_reminders(user_id)
    try:
        idx = int(ref) - 1
        if 0 <= idx < len(reminders):
            return reminders[idx]
    except ValueError:
        pass
    ref_lower = ref.lower()
    return next((r for r in reminders if ref_lower in r["text"].lower()), None)


# ---------------------------------------------------------------------------
# Message handler
# ---------------------------------------------------------------------------

def handle_message(chat_id: str, text: str):
    if ALLOWED_USER_IDS and chat_id not in ALLOWED_USER_IDS:
        send_message(chat_id, "אין לך הרשאה להשתמש בבוט זה.")
        return

    try:
        intent = llm.parse_intent(text)
    except Exception as e:
        log.warning(f"LLM parse error: {e}")
        send_message(chat_id, "סליחה, לא הצלחתי לעבד את הבקשה. נסה שוב.")
        return

    action = intent.get("action", "unknown")
    log.info(f"chat={chat_id} action={action}")

    if action == "add_todo":
        todo_text = intent.get("text", "").strip()
        if not todo_text:
            send_message(chat_id, "לא הבנתי מה להוסיף לרשימה.")
            return
        db.add_todo(chat_id, todo_text)
        send_message(chat_id, f"✅ נוספה משימה: {todo_text}")

    elif action == "list_todos":
        todos = db.list_todos(chat_id)
        send_message(chat_id, format_todos(todos))

    elif action == "complete_todo":
        todo = _resolve_todo(chat_id, str(intent.get("ref", "")))
        if not todo:
            send_message(chat_id, "לא מצאתי את המשימה הזו.")
            return
        db.complete_todo(todo["id"])
        send_message(chat_id, f'✅ מצוין! סימנתי "{todo["text"]}" כבוצע.')

    elif action == "delete_todo":
        todo = _resolve_todo(chat_id, str(intent.get("ref", "")))
        if not todo:
            send_message(chat_id, "לא מצאתי את המשימה הזו.")
            return
        db.delete_todo(todo["id"])
        send_message(chat_id, f'🗑️ מחקתי: "{todo["text"]}"')

    elif action == "add_reminder":
        reminder_text = intent.get("text", "").strip()
        schedule_data = intent.get("schedule")
        if not reminder_text or not schedule_data:
            send_message(chat_id, "לא הבנתי את פרטי התזכורת.")
            return
        recurring = schedule_data["type"] in ("interval", "cron")
        reminder_id = db.add_reminder(
            chat_id, reminder_text,
            schedule_data["type"], json.dumps(schedule_data)
        )
        scheduler.schedule(reminder_id, chat_id, reminder_text, schedule_data, recurring)
        send_message(chat_id, f'⏰ תזכורת נקבעה: "{reminder_text}" — {_describe_schedule(schedule_data)}')

    elif action == "list_reminders":
        reminders = db.list_reminders(chat_id)
        send_message(chat_id, format_reminders(reminders))

    elif action == "cancel_reminder":
        reminder = _resolve_reminder(chat_id, str(intent.get("ref", "")))
        if not reminder:
            send_message(chat_id, "לא מצאתי את התזכורת הזו.")
            return
        db.cancel_reminder(reminder["id"])
        scheduler.cancel(reminder["id"])
        send_message(chat_id, f'🗑️ ביטלתי את התזכורת: "{reminder["text"]}"')

    else:
        send_message(chat_id, "לא הבנתי את הבקשה. אפשר לנסח מחדש?")


# ---------------------------------------------------------------------------
# Telegram long-polling
# ---------------------------------------------------------------------------

def poll():
    import requests as req
    offset = None
    log.info("Telegram polling started")
    while True:
        try:
            resp = req.get(
                f"{BASE_URL}/getUpdates",
                params={"timeout": 30, "offset": offset},
                timeout=35,
            )
            for update in resp.json().get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message") or update.get("edited_message")
                if not msg:
                    continue
                chat_id = str(msg["chat"]["id"])
                text = msg.get("text", "")
                if text:
                    handle_message(chat_id, text)
        except Exception as e:
            log.warning(f"Polling error: {e}")
            time.sleep(5)


# ---------------------------------------------------------------------------
# Local HTTP API (for Hermes agent access)
# ---------------------------------------------------------------------------

api = Flask(__name__)

@api.route("/health")
def health():
    return jsonify({"status": "ok"})


@api.route("/todos", methods=["GET"])
def api_list_todos():
    uid = request.args.get("user_id")
    if not uid:
        return jsonify({"error": "user_id required"}), 400
    return jsonify(db.list_todos(uid))


@api.route("/todos", methods=["POST"])
def api_add_todo():
    data = request.json or {}
    uid, text = data.get("user_id"), data.get("text", "").strip()
    if not uid or not text:
        return jsonify({"error": "user_id and text required"}), 400
    return jsonify({"id": db.add_todo(uid, text)}), 201


@api.route("/todos/<int:todo_id>/done", methods=["POST"])
def api_complete_todo(todo_id):
    db.complete_todo(todo_id)
    return jsonify({"ok": True})


@api.route("/todos/<int:todo_id>", methods=["DELETE"])
def api_delete_todo(todo_id):
    db.delete_todo(todo_id)
    return jsonify({"ok": True})


@api.route("/reminders", methods=["GET"])
def api_list_reminders():
    uid = request.args.get("user_id")
    if not uid:
        return jsonify({"error": "user_id required"}), 400
    return jsonify(db.list_reminders(uid))


@api.route("/reminders", methods=["POST"])
def api_add_reminder():
    data = request.json or {}
    uid = data.get("user_id")
    text = data.get("text", "").strip()
    schedule_data = data.get("schedule")
    if not all([uid, text, schedule_data]):
        return jsonify({"error": "user_id, text, schedule required"}), 400
    recurring = schedule_data["type"] in ("interval", "cron")
    rid = db.add_reminder(uid, text, schedule_data["type"], json.dumps(schedule_data))
    scheduler.schedule(rid, uid, text, schedule_data, recurring)
    return jsonify({"id": rid}), 201


@api.route("/reminders/<int:reminder_id>", methods=["DELETE"])
def api_cancel_reminder(reminder_id):
    db.cancel_reminder(reminder_id)
    scheduler.cancel(reminder_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    Path(Path(__file__).parent.parent / "logs").mkdir(exist_ok=True)
    db.init()
    scheduler.init(str(db.DB_PATH))

    api_thread = threading.Thread(
        target=lambda: api.run(host="127.0.0.1", port=API_PORT, use_reloader=False),
        daemon=True,
    )
    api_thread.start()
    log.info(f"API server started on port {API_PORT}")

    poll()
