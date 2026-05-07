---
name: personal-assistant
description: Personal assistant for family members — Hebrew todo list and reminders. Each user has isolated data. Standalone Telegram bot + Hermes HTTP API.
version: 1.0.0
---

# Personal Assistant

Hebrew-first personal assistant bot. Each family member has their own isolated todos and reminders. The bot runs as a standalone Telegram long-polling process and also exposes a local HTTP API on port 8766 for Hermes agent access.

## Users

| Name    | USER_ID env var    | Default value  |
|---------|--------------------|----------------|
| Roy     | USER_ID_ROY        | 391626535      |
| Michael | USER_ID_MICHAEL    | (set in .env)  |

User IDs map directly to Telegram chat IDs. All DB queries are scoped to `user_id` — users never see each other's data.

## Capabilities

- **Todos**: add, list, mark done, delete — via NL Hebrew
- **Reminders**: one-time, recurring interval (every N hours), daily/weekly cron — via NL Hebrew
- Reminders persist across reboots via APScheduler SQLAlchemy job store (same SQLite DB)

## Data

SQLite at `~/.hermes/skills/personal-assistant/data/pa.db`

Tables:
- `todos(id, user_id, text, done, created_at, done_at)`
- `reminders(id, user_id, text, trigger_type, trigger_data, active, created_at)`

APScheduler stores its job state in the same DB (table: `apscheduler_jobs`).

## Hermes Agent Access (Roy only)

The bot exposes a local HTTP API at `http://127.0.0.1:8766`. Roy can use it to read or modify his own data or Michael's.

The API requires an `Authorization: Bearer <key>` header. Before making any request, read the key from the skill's `.env`:

```bash
PA_KEY=$(grep '^API_KEY=' ~/.hermes/skills/personal-assistant/.env | cut -d= -f2)
MICHAEL_ID=$(grep '^USER_ID_MICHAEL=' ~/.hermes/skills/personal-assistant/.env | cut -d= -f2)
```

The API binds to `127.0.0.1` only — not reachable from outside the machine.

```bash
# List Roy's todos
curl -H "Authorization: Bearer $PA_KEY" http://127.0.0.1:8766/todos?user_id=391626535

# List Michael's todos
curl "http://127.0.0.1:8766/todos?user_id=$MICHAEL_ID"

# Add a todo for Michael
curl -s -X POST http://127.0.0.1:8766/todos \
  -H "Content-Type: application/json" \
  -d '{"user_id": "<michael_id>", "text": "להכין שיעורים"}'

# Mark todo #3 done
curl -X POST http://127.0.0.1:8766/todos/3/done

# Delete todo #5
curl -X DELETE http://127.0.0.1:8766/todos/5

# List Roy's reminders
curl -H "Authorization: Bearer $PA_KEY" http://127.0.0.1:8766/reminders?user_id=391626535

# Add a one-time reminder
curl -s -X POST http://127.0.0.1:8766/reminders \
  -H "Content-Type: application/json" \
  -d '{"user_id": "391626535", "text": "פגישה", "schedule": {"type": "once", "datetime": "2026-05-09T10:00:00"}}'

# Add a recurring reminder (every 2 hours = 7200 seconds)
curl -s -X POST http://127.0.0.1:8766/reminders \
  -H "Content-Type: application/json" \
  -d '{"user_id": "391626535", "text": "לשתות מים", "schedule": {"type": "interval", "seconds": 7200}}'

# Cancel reminder #2
curl -X DELETE http://127.0.0.1:8766/reminders/2
```

## Deploy

```bash
# From local machine
rsync -av personal-assistant/ roy650@192.168.1.17:~/.hermes/skills/personal-assistant/

# SSH in, create .env from .env.example, fill in real values
ssh roy650@192.168.1.17
cp ~/.hermes/skills/personal-assistant/.env.example ~/.hermes/skills/personal-assistant/.env
# edit .env: set TELEGRAM_BOT_TOKEN, USER_ID_MICHAEL, LLM_API_KEY

# Start
nohup bash ~/.hermes/skills/personal-assistant/scripts/run.sh \
  >> ~/.hermes/skills/personal-assistant/logs/bot.log 2>&1 \
  & echo $! > ~/.hermes/skills/personal-assistant/bot.pid
```

## Language & Formatting

The bot always replies in Hebrew. Tables are not used — lists are plain numbered text. No code blocks needed (no tabular output).
