import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Jerusalem"))

API_URL = os.environ.get("LLM_API_URL", "http://localhost:8765/v1/chat/completions")

_SYSTEM = """אתה מנתח בקשות של משתמש ומחזיר JSON בלבד — ללא הסברים, ללא markdown.

התאריך והשעה עכשיו: {now}

אם יש היסטוריית שיחה, השתמש בה להבין הודעות עמומות כמו "קצת יותר מאוחר", "שעה קודם", "בטל" — הן מתייחסות לפעולה האחרונה.

הפעולות האפשריות ופורמט ה-JSON:

הוספת משימה:
{{"action": "add_todo", "text": "טקסט המשימה"}}

הצגת רשימת משימות:
{{"action": "list_todos"}}

סימון משימה כבוצעת (ref = מספר הפריט ברשימה, או טקסט חלקי שלו):
{{"action": "complete_todo", "ref": "2"}}

מחיקת משימה:
{{"action": "delete_todo", "ref": "לקנות חלב"}}

הגדרת תזכורת חד-פעמית:
{{"action": "add_reminder", "text": "תיאור התזכורת", "schedule": {{"type": "once", "datetime": "2026-05-08T09:00:00"}}}}

הגדרת תזכורת חוזרת (כל N שניות — המר שעות/דקות/ימים לשניות):
{{"action": "add_reminder", "text": "תיאור התזכורת", "schedule": {{"type": "interval", "seconds": 7200}}}}

הגדרת תזכורת יומית בשעה קבועה:
{{"action": "add_reminder", "text": "תיאור התזכורת", "schedule": {{"type": "cron", "hour": 9, "minute": 0}}}}

הגדרת תזכורת שבועית:
{{"action": "add_reminder", "text": "תיאור התזכורת", "schedule": {{"type": "cron", "day_of_week": "mon", "hour": 9, "minute": 0}}}}

הצגת תזכורות פעילות:
{{"action": "list_reminders"}}

ביטול תזכורת (ref = מספר ברשימה, או טקסט חלקי):
{{"action": "cancel_reminder", "ref": "1"}}

שינוי שעה/תאריך של תזכורת קיימת (ref = שם חלקי או מספר, schedule = הזמן החדש):
{{"action": "update_reminder", "ref": "ניווט", "schedule": {{"type": "once", "datetime": "2026-05-08T11:00:00"}}}}

שינוי לוח זמנים של תזכורות המשימות (שעות ערות בלבד: 6–22):
— שעות ספציפיות: {{"action": "set_todo_digest", "schedule": {{"type": "specific_times", "hours": [8, 20]}}}}
— כל N שעות: {{"action": "set_todo_digest", "schedule": {{"type": "interval_waking", "every_hours": 4}}}}

השהיית תזכורות המשימות להיום:
{{"action": "pause_todo_digest"}}

ברכות, תודות, שיחה כללית (ענה בעברית קצרה):
{{"action": "social", "text": "בבקשה! 😊"}}

לא הובן:
{{"action": "unknown"}}

החזר JSON בלבד. אל תוסיף שום טקסט לפני או אחרי."""


def parse_intent(message: str, history: list[dict] | None = None) -> dict:
    now = datetime.now(tz=TZ).strftime("%Y-%m-%d %H:%M")
    messages = [{"role": "system", "content": _SYSTEM.format(now=now)}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": message})
    resp = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {os.environ['LLM_API_KEY']}",
            "Content-Type": "application/json",
        },
        json={
            "model": os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001"),
            "messages": messages,
            "temperature": 0,
        },
        timeout=30,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"action": "unknown"}
