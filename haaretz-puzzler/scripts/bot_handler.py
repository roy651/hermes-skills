#!/usr/bin/env python3
"""Haaretz Puzzler — Telegram Bot Handler (using requests)

Long-polling Telegram Bot API for incoming messages.
Uses requests library for reliable HTTP with proper timeout handling.

Commands:
    /start      — Welcome message
    /puzzle     — Fetch latest puzzle
    /puzzle N   — Fetch the Nth puzzle image (1-10)
    /help       — Show available commands
"""

import json
import mimetypes
import os
import re
import sys
import time
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen

import requests

SKILL_DIR = Path(__file__).resolve().parent.parent

# Load .env
dotenv = SKILL_DIR / ".env"
if dotenv.exists():
    for line in dotenv.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not BOT_TOKEN:
    print("ERROR: TELEGRAM_BOT_TOKEN not set", file=sys.stderr)
    sys.exit(1)

ALLOWED_CHAT_IDS = set(
    int(x.strip()) for x in os.environ.get("ALLOWED_CHAT_IDS", "").split(",") if x.strip()
)
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
LONG_POLL_TIMEOUT = 30  # Telegram will hold connection for this many seconds
LOG_FILE = SKILL_DIR / "logs" / "bot_handler.log"

requests_session = requests.Session()


def log(msg):
    msg = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(msg, flush=True, file=sys.stderr)
    os.makedirs(LOG_FILE.parent, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(msg + "\n")


def api(method, **kwargs):
    """Call Telegram Bot API via requests."""
    url = f"{API_BASE}/{method}"
    try:
        resp = requests_session.post(url, json=kwargs, timeout=35)
        return resp.json()
    except Exception as e:
        log(f"API error ({method}): {e}")
        return None


def send_msg(chat_id, text, reply_to=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    return api("sendMessage", **data)


def send_photo(chat_id, photo_path, caption=None, reply_to=None):
    """Send image as a document to preserve full quality (sendPhoto compresses)."""
    url = f"{API_BASE}/sendDocument"
    with open(photo_path, "rb") as f:
        mime = "image/webp" if photo_path.endswith(".webp") else "image/jpeg"
        files = {"document": (os.path.basename(photo_path), f, mime)}
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        if reply_to:
            data["reply_to_message_id"] = reply_to
        try:
            resp = requests_session.post(url, data=data, files=files, timeout=35)
            result = resp.json()
            if result.get("ok"):
                log(f"Photo sent to {chat_id}")
            else:
                log(f"Photo send failed: {result}")
            return result
        except Exception as e:
            log(f"sendDocument error: {e}")
            return None


def handle_puzzle(chat_id, message_id, args_text=""):
    """Handle /puzzle: reply 'checking...' immediately, then fetch or serve cache."""
    puzzle_index = 3
    m = re.search(r'(\d+)', args_text.strip())
    if m:
        puzzle_index = int(m.group(1))
        if puzzle_index < 1 or puzzle_index > 10:
            send_msg(chat_id, "❌ נסה שוב מאוחר יותר.")
            return

    # First reply
    send_msg(chat_id, f"מנסה לשלוף תשבץ אחרון", reply_to=message_id)

    env = os.environ.copy()
    env["PUZZLE_INDEX"] = str(puzzle_index)
    try:
        result = subprocess.run(
            ["bash", str(SKILL_DIR / "scripts" / "run.sh")],
            env=env, capture_output=True, text=True, timeout=180,
        )

        if result.returncode != 0:
            err_msg = "לא הצלחתי להביא את התשבץ. נסה שוב מאוחר יותר."
            for line in result.stderr.split("\n"):
                if "ERROR:" in line:
                    err_msg = f"❌ {line.split('ERROR:')[1].strip()}"
                    break
            send_msg(chat_id, err_msg)
            log(f"Fetch failed: {result.stderr[:300]}")
            return

        media_path = None
        alt_info = ""
        source = "fresh"
        for line in result.stdout.strip().split("\n"):
            if line.startswith("MEDIA:"):
                media_path = line[6:]
            elif line.startswith("ALT_INFO:"):
                alt_info = line[9:]
            elif line.startswith("SOURCE:"):
                source = line[7:]

        if media_path and os.path.exists(media_path):
            if source == "cached":
                caption = f"🧩 {alt_info}\nתשבץ נשלף מהזיכרון הקיים"
            else:
                caption = f"🧩 {alt_info}\nתשבץ נשלף מהאתר"
            send_photo(chat_id, media_path, caption=caption, reply_to=message_id)
        else:
            send_msg(chat_id, "❌ התשבץ לא הורד כראוי.")

    except subprocess.TimeoutExpired:
        send_msg(chat_id, "❌ לקח יותר מדי זמן. נסה שוב בעוד מספר דקות.")
    except Exception as e:
        send_msg(chat_id, "❌ שגיאה לא צפויה.")
        log(f"Unexpected error in handle_puzzle: {e}")


def handle_logic(chat_id, message_id):
    """Handle /logic: fetch the latest logic puzzle from Haaretz."""
    send_msg(chat_id, "מנסה לשלוף תשבץ היגיון אחרון", reply_to=message_id)

    env = os.environ.copy()
    env["PUZZLE_TYPE"] = "logic"
    env["PUZZLE_INDEX"] = "1"
    try:
        result = subprocess.run(
            ["bash", str(SKILL_DIR / "scripts" / "run.sh")],
            env=env, capture_output=True, text=True, timeout=180,
        )

        if result.returncode != 0:
            err_msg = "לא הצלחתי להביא את תשבץ ההיגיון. נסה שוב מאוחר יותר."
            for line in result.stderr.split("\n"):
                if "ERROR:" in line:
                    err_msg = f"❌ {line.split('ERROR:')[1].strip()}"
                    break
            send_msg(chat_id, err_msg)
            log(f"Logic fetch failed: {result.stderr[:300]}")
            return

        media_path = None
        alt_info = ""
        source = "fresh"
        for line in result.stdout.strip().split("\n"):
            if line.startswith("MEDIA:"):
                media_path = line[6:]
            elif line.startswith("ALT_INFO:"):
                alt_info = line[9:]
            elif line.startswith("SOURCE:"):
                source = line[7:]

        if media_path and os.path.exists(media_path):
            suffix = "נשלף מהזיכרון הקיים" if source == "cached" else "נשלף מהאתר"
            caption = f"🧠 {alt_info}\nתשבץ היגיון {suffix}"
            send_photo(chat_id, media_path, caption=caption, reply_to=message_id)
        else:
            send_msg(chat_id, "❌ תשבץ ההיגיון לא הורד כראוי.")

    except subprocess.TimeoutExpired:
        send_msg(chat_id, "❌ לקח יותר מדי זמן. נסה שוב בעוד מספר דקות.")
    except Exception as e:
        send_msg(chat_id, "❌ שגיאה לא צפויה.")
        log(f"Unexpected error in handle_logic: {e}")


def handle_start(chat_id):
    send_msg(chat_id, (
        "🧩 <b>ברוכים הבאים לתשבצי הארץ!</b>\n\n"
        "פקודות זמינות:\n"
        "/puzzle — תשבץ שבועי (מוסף סוף שבוע)\n"
        "/puzzle N — תשבץ מספר N (למשל: /puzzle 1)\n"
        "/logic — תשבץ היגיון (יוצא באמצע השבוע)\n"
        "/help — עזרה\n\n"
        "התשבצים מתעדכנים כל שבוע. 📅"
    ))


def handle_help(chat_id):
    send_msg(chat_id, (
        "📋 <b>פקודות זמינות:</b>\n\n"
        "/start — ברוכים הבאים\n"
        "/puzzle — התשבץ השלישי מהמוסף השבועי (ברירת מחדל)\n"
        "/puzzle N — תשבץ מספר N (למשל: /puzzle 1)\n"
        "/logic — תשבץ היגיון (יוצא לרוב ביום רביעי)\n"
        "/help — עזרה זו\n\n"
        "💡 הבוט שולף תשבץ חדש רק כששולחים פקודה.\n"
        "אם התשבץ עוד לא פורסם — נשלח התשבץ האחרון מהמטמון."
    ))


def main():
    offset = None
    log("Bot handler starting (requests-based)...")

    while True:
        try:
            result = api("getUpdates", offset=offset, timeout=LONG_POLL_TIMEOUT,
                         allowed_updates=["message"])

            if not result or not result.get("ok"):
                time.sleep(2)
                continue

            for update in result.get("result", []):
                msg = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "").strip()
                message_id = msg.get("message_id", 0)

                if not chat_id or not text:
                    continue

                # Restrict to allowed users
                if ALLOWED_CHAT_IDS and chat_id not in ALLOWED_CHAT_IDS:
                    log(f"Ignored command from unauthorized user {chat_id}")
                    continue

                if text.startswith("/"):
                    parts = text.split(None, 1)
                    cmd = parts[0].lower().lstrip("/").split("@")[0]
                    args = parts[1] if len(parts) > 1 else ""

                    log(f"Command: {cmd} from chat {chat_id}")
                    if cmd == "start":
                        handle_start(chat_id)
                    elif cmd == "puzzle":
                        handle_puzzle(chat_id, message_id, args)
                    elif cmd == "logic":
                        handle_logic(chat_id, message_id)
                    elif cmd == "help":
                        handle_help(chat_id)
                    else:
                        send_msg(chat_id, "❓ פקודה לא מזוהה. שלח /help.",
                                 reply_to=message_id)

                offset = update.get("update_id", 0) + 1
                time.sleep(0.3)

        except KeyboardInterrupt:
            log("Stopping (SIGINT)")
            break
        except Exception as e:
            log(f"Main loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
