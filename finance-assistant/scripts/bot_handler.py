"""Finance Assistant Telegram Bot — personal finance queries via ActualBudget."""
import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import requests as http

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).parent))
import actual_client as ac
import llm_query
import report as report_mod

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SKILL_DIR = Path(__file__).parent.parent
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
LONG_POLL_TIMEOUT = 30

ALLOWED_CHAT_IDS = set(
    int(x.strip()) for x in os.environ.get("ALLOWED_CHAT_IDS", "").split(",") if x.strip()
)

IMPORTER_DIR = SKILL_DIR / "importer"
LOG_FILE = SKILL_DIR / "logs" / "bot.log"
SCHEDULE_FILE = SKILL_DIR / "schedule.json"

# Per-chat state for multi-step flows (in-memory only — not persisted)
_chat_state: dict[int, dict] = {}

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
    try:
        resp = _session.post(f"{API_BASE}/{method}", json=kwargs, timeout=35)
        return resp.json()
    except Exception as e:
        log(f"tg error {method}: {e}")
        return {}


def send(chat_id: int, text: str, **kw) -> None:
    tg("sendMessage", chat_id=chat_id, text=text, parse_mode="Markdown", **kw)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _ils(amount: float) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}₪{abs(amount):,.0f}"


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def handle_balance(chat_id: int) -> None:
    send(chat_id, "⏳ שולף יתרות...")
    try:
        accounts = ac.get_balances()
    except Exception as e:
        send(chat_id, f"❌ שגיאה: {e}")
        return
    if not accounts:
        send(chat_id, "לא נמצאו חשבונות.")
        return
    lines = ["*💰 יתרות חשבונות*\n"]
    for acc in accounts:
        icon = "💳" if acc["type"] == "credit" else "🏦"
        lines.append(f"{icon} {acc['name']}: {_ils(acc['balance'])}")
    send(chat_id, "\n".join(lines))


def handle_budget(chat_id: int) -> None:
    send(chat_id, "⏳ שולף נתוני תקציב...")
    try:
        rows = ac.get_monthly_budget()
        summary = ac.get_income_vs_expense()
    except Exception as e:
        send(chat_id, f"❌ שגיאה: {e}")
        return
    today = date.today()
    lines = [f"*📋 תקציב — {today.strftime('%B %Y')}*\n"]
    lines.append(f"הכנסות: {_ils(summary['income'])}  |  הוצאות: {_ils(summary['expenses'])}  |  נטו: {_ils(summary['net'])}\n")
    current_group = None
    for row in rows:
        if row["group"] != current_group:
            current_group = row["group"]
            lines.append(f"\n_{current_group}_")
        remaining = row["remaining"]
        status = "✅" if remaining >= 0 else "🔴"
        lines.append(f"  {status} {row['category']}: {_ils(abs(row['actual']))} / {_ils(row['budgeted'])}")
    send(chat_id, "\n".join(lines))


def handle_report(chat_id: int, report_type: str) -> None:
    send(chat_id, f"⏳ מכין דוח {report_type}...")
    if report_type == "weekly":
        text = report_mod.build_weekly_report()
    else:
        text = report_mod.build_monthly_report()
    send(chat_id, text)


def handle_sync(chat_id: int) -> None:
    send(chat_id, "⏳ מריץ ייבוא כרטיסי אשראי... (יכול לקחת כמה דקות)")
    script = IMPORTER_DIR / "run-import.sh"
    if not script.exists():
        send(chat_id, "❌ run-import.sh לא נמצא. האם MoneyMan הותקן?")
        return
    try:
        result = subprocess.run(
            ["bash", str(script)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            send(chat_id, "✅ הייבוא הסתיים בהצלחה.")
        else:
            tail = (result.stderr or result.stdout)[-500:]
            send(chat_id, f"⚠️ הייבוא הסתיים עם שגיאה:\n```\n{tail}\n```")
    except subprocess.TimeoutExpired:
        send(chat_id, "⏱ הייבוא לקח יותר מ-5 דקות — בדוק את הלוגים.")
    except Exception as e:
        send(chat_id, f"❌ שגיאה בהרצת הייבוא: {e}")


def handle_sync_bank_prompt(chat_id: int) -> None:
    _chat_state[chat_id] = {"awaiting_bank_password": True}
    send(
        chat_id,
        "🔐 *ייבוא בנק לאומי*\n\nשלח את הסיסמה שלך כהודעה הבאה.\n"
        "הסיסמה *לא תישמר* ותישאר בזיכרון בלבד.\n"
        "שלח /cancel לביטול.",
    )


def handle_bank_password(chat_id: int, password: str) -> None:
    _chat_state.pop(chat_id, None)
    send(chat_id, "⏳ מתחבר לבנק לאומי... (יכול לקחת מספר דקות)")
    script = IMPORTER_DIR / "run-bank-import.sh"
    if not script.exists():
        send(chat_id, "❌ run-bank-import.sh לא נמצא.")
        return
    try:
        env = {**os.environ, "LEUMI_PASSWORD": password}
        result = subprocess.run(
            ["bash", str(script)],
            capture_output=True, text=True, timeout=300, env=env,
        )
        # Clear the password from any lingering reference
        del password
        if result.returncode == 0:
            send(chat_id, "✅ ייבוא לאומי הסתיים בהצלחה.")
        else:
            tail = (result.stderr or result.stdout)[-400:]
            send(chat_id, f"⚠️ הייבוא הסתיים עם שגיאה:\n```\n{tail}\n```")
    except subprocess.TimeoutExpired:
        send(chat_id, "⏱ הייבוא לקח יותר מ-5 דקות.")
    except Exception as e:
        send(chat_id, f"❌ שגיאה: {e}")


def handle_ask(chat_id: int, question: str) -> None:
    if not question.strip():
        send(chat_id, "שאל משהו, למשל: /ask כמה הוצאתי החודש על מסעדות?")
        return
    send(chat_id, "⏳ חושב...")
    answer = llm_query.answer(question)
    send(chat_id, answer)


def handle_schedule(chat_id: int, args: str) -> None:
    sched = json.loads(SCHEDULE_FILE.read_text()) if SCHEDULE_FILE.exists() else {}

    if not args.strip():
        lines = ["*📆 לוח זמנים לדוחות*\n"]
        w = sched.get("weekly", {})
        m = sched.get("monthly", {})
        lines.append(f"שבועי:  {'✅' if w.get('enabled') else '❌'}  {w.get('day', '—')} {w.get('time', '—')}")
        lines.append(f"חודשי:  {'✅' if m.get('enabled') else '❌'}  יום {m.get('day', '—')} {m.get('time', '—')}")
        lines.append("\nלשינוי: `/schedule weekly monday 09:00`")
        lines.append("לכיבוי: `/schedule weekly off`")
        send(chat_id, "\n".join(lines))
        return

    parts = args.strip().split()
    if len(parts) < 2:
        send(chat_id, "שימוש: `/schedule weekly|monthly <day|day-number> <HH:MM>` או `off`")
        return

    report_type = parts[0].lower()
    if report_type not in ("weekly", "monthly"):
        send(chat_id, "סוג דוח לא מוכר. השתמש ב-`weekly` או `monthly`.")
        return

    if parts[1].lower() == "off":
        sched.setdefault(report_type, {})["enabled"] = False
        SCHEDULE_FILE.write_text(json.dumps(sched, ensure_ascii=False, indent=2))
        send(chat_id, f"✅ דוח {report_type} כובה.")
        return

    if len(parts) < 3:
        send(chat_id, "ציין גם שעה, למשל: `/schedule weekly monday 09:00`")
        return

    day_or_num = parts[1]
    time_str = parts[2]

    entry = sched.setdefault(report_type, {})
    entry["enabled"] = True
    entry["time"] = time_str
    if report_type == "weekly":
        entry["day"] = day_or_num.lower()
    else:
        try:
            entry["day"] = int(day_or_num)
        except ValueError:
            send(chat_id, "לדוח חודשי, ציין מספר יום (1–28).")
            return

    SCHEDULE_FILE.write_text(json.dumps(sched, ensure_ascii=False, indent=2))
    send(chat_id, f"✅ דוח {report_type} מתוזמן ל-{day_or_num} {time_str}.")


def handle_help(chat_id: int) -> None:
    send(
        chat_id,
        "*💼 Finance Assistant*\n\n"
        "/balance — יתרות חשבונות\n"
        "/budget — תקציב חודשי\n"
        "/report `[weekly|monthly]` — דוח (ברירת מחדל: חודשי)\n"
        "/sync — ייבוא כרטיסי אשראי\n"
        "/sync\\_bank — ייבוא בנק לאומי\n"
        "/ask `<שאלה>` — שאלה חופשית\n"
        "/schedule — הגדרות דוחות אוטומטיים\n"
        "/cancel — ביטול פעולה נוכחית",
    )


# ---------------------------------------------------------------------------
# Update dispatcher
# ---------------------------------------------------------------------------

def dispatch(update: dict) -> None:
    msg = update.get("message") or update.get("edited_message") or {}
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()

    if not chat_id or not text:
        return
    if ALLOWED_CHAT_IDS and chat_id not in ALLOWED_CHAT_IDS:
        return

    # Multi-step flow: awaiting bank password
    state = _chat_state.get(chat_id, {})
    if state.get("awaiting_bank_password"):
        if text.startswith("/cancel"):
            _chat_state.pop(chat_id, None)
            send(chat_id, "ביטול.")
        else:
            handle_bank_password(chat_id, text)
        return

    # Command routing
    cmd, _, rest = text.partition(" ")
    cmd = cmd.lower().lstrip("/").split("@")[0]

    if cmd in ("start", "help"):
        handle_help(chat_id)
    elif cmd == "balance":
        handle_balance(chat_id)
    elif cmd == "budget":
        handle_budget(chat_id)
    elif cmd == "report":
        report_type = rest.strip().lower() or "monthly"
        if report_type not in ("weekly", "monthly"):
            report_type = "monthly"
        handle_report(chat_id, report_type)
    elif cmd == "sync":
        handle_sync(chat_id)
    elif cmd == "sync_bank":
        handle_sync_bank_prompt(chat_id)
    elif cmd == "ask":
        handle_ask(chat_id, rest)
    elif cmd == "schedule":
        handle_schedule(chat_id, rest)
    elif cmd == "cancel":
        _chat_state.pop(chat_id, None)
        send(chat_id, "ביטול.")
    else:
        send(chat_id, f"פקודה לא מוכרת: `{cmd}`. שלח /help לרשימת פקודות.")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    log("Finance Assistant bot starting...")
    if not BOT_TOKEN:
        log("FATAL: TELEGRAM_BOT_TOKEN is not set")
        sys.exit(1)

    offset = None
    while True:
        try:
            result = tg("getUpdates", offset=offset, timeout=LONG_POLL_TIMEOUT)
            for update in result.get("result", []):
                try:
                    dispatch(update)
                except Exception as e:
                    log(f"dispatch error: {e}")
                offset = update["update_id"] + 1
        except KeyboardInterrupt:
            log("Shutting down.")
            break
        except Exception as e:
            log(f"poll error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
