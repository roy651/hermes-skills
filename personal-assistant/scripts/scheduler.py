import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

TZ = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Jerusalem"))

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
