import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

TZ = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Jerusalem"))
WAKING_START = 6
WAKING_END = 22

log = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def send_reminder(chat_id: str, text: str, reminder_id: int, recurring: bool):
    """APScheduler job — module-level so APScheduler can pickle/unpickle it."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"⏰ תזכורת: {text}"},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Failed to send reminder {reminder_id}: {e}")

    if not recurring:
        import db
        db.deactivate_reminder(reminder_id)


def init(db_path: str):
    global _scheduler
    _scheduler = BackgroundScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{db_path}")},
        timezone=TZ,
        job_defaults={"misfire_grace_time": 3600},
    )
    _scheduler.start()
    log.info("Scheduler started")


def schedule(reminder_id: int, user_id: str, text: str, schedule_data: dict, recurring: bool):
    t = schedule_data["type"]
    if t == "once":
        run_date = datetime.fromisoformat(schedule_data["datetime"]).replace(tzinfo=TZ)
        trigger = DateTrigger(run_date=run_date)
    elif t == "interval":
        trigger = IntervalTrigger(seconds=schedule_data["seconds"])
    elif t == "cron":
        kwargs = {k: v for k, v in schedule_data.items() if k != "type"}
        trigger = CronTrigger(**kwargs)
    else:
        raise ValueError(f"Unknown trigger type: {t}")

    _scheduler.add_job(
        send_reminder,
        trigger=trigger,
        args=[user_id, text, reminder_id, recurring],
        id=f"reminder_{reminder_id}",
        replace_existing=True,
    )
    log.info(f"Scheduled reminder {reminder_id} for user {user_id} ({t})")


def cancel(reminder_id: int):
    job_id = f"reminder_{reminder_id}"
    if _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)
        log.info(f"Cancelled reminder {reminder_id}")


# ---------------------------------------------------------------------------
# Todo digest
# ---------------------------------------------------------------------------

def send_todo_digest(user_id: str):
    """APScheduler job — sends open todos as a digest message."""
    import db
    todos = db.list_todos(user_id)
    open_todos = [t for t in todos if not t["done"]]
    if not open_todos:
        return
    lines = ["📋 תזכורת משימות:"]
    for i, t in enumerate(open_todos, 1):
        lines.append(f"{i}. {t['text']}")
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": user_id, "text": "\n".join(lines)},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Failed to send todo digest for {user_id}: {e}")


def _digest_hours(schedule_data: dict) -> list[int]:
    t = schedule_data.get("type")
    if t == "specific_times":
        return sorted(h for h in schedule_data["hours"] if WAKING_START <= h <= WAKING_END)
    if t == "interval_waking":
        every = int(schedule_data.get("every_hours", 4))
        return [h for h in range(WAKING_START, WAKING_END + 1) if (h - WAKING_START) % every == 0]
    return [6, 10, 14, 18, 22]  # default every 4h


def schedule_todo_digest(user_id: str, schedule_data: dict):
    hours = _digest_hours(schedule_data)
    trigger = CronTrigger(hour=",".join(str(h) for h in hours), minute=0, timezone=TZ)
    _scheduler.add_job(
        send_todo_digest,
        trigger=trigger,
        args=[user_id],
        id=f"todo_digest_{user_id}",
        replace_existing=True,
    )
    log.info(f"Scheduled todo digest for {user_id} at hours {hours}")


def has_todo_digest(user_id: str) -> bool:
    return _scheduler.get_job(f"todo_digest_{user_id}") is not None


def pause_todo_digest_today(user_id: str):
    job_id = f"todo_digest_{user_id}"
    if _scheduler.get_job(job_id):
        tomorrow_6am = (datetime.now(TZ) + timedelta(days=1)).replace(
            hour=WAKING_START, minute=0, second=0, microsecond=0
        )
        _scheduler.modify_job(job_id, next_run_time=tomorrow_6am)
        log.info(f"Paused todo digest for {user_id} until {tomorrow_6am}")
