---
name: personal-assistant
description: Personal assistant for family/team members — Hebrew todo list and reminders. Each user has isolated data. Standalone Telegram bot + local HTTP API for Hermes agent access.
version: 1.0.0
---

# Personal Assistant

Hebrew-first personal assistant bot. Each user has their own isolated todos and reminders. Runs as a standalone Telegram long-polling bot and exposes a local HTTP API on port 8766 for Hermes agent access.

## When to use this skill vs Hermes cron

**Use this skill** for personal reminders and todo management: "remind me to call John tomorrow at 9", "add milk to my list", "what's on my list". These are lightweight Telegram pushes — no LLM at fire time, low cost. Other bot users can interact with their own data directly through the bot.

**Use Hermes cron** for scheduled agent tasks that require reasoning or tool use: "every Sunday check if the server is up", "fetch the weekly report and summarize it". Those need a full agent session at fire time.

## Users

Each user is identified by their Telegram chat ID, set in `.env` as a comma-separated list:

```
ALLOWED_USER_IDS=<id1>,<id2>,...
AGENT_USER_ID=<hermes agent user's chat id>
```

All DB queries are scoped to the sender's `user_id` — users never see each other's data.

## Capabilities

- **Todos**: add, list, mark done, delete — via NL Hebrew
- **Reminders**: one-time, recurring interval, daily/weekly cron — via NL Hebrew
- Reminders persist across reboots via APScheduler SQLAlchemy job store (SQLite)

## Data

SQLite at `~/.hermes/skills/personal-assistant/data/pa.db`

Tables:
- `todos(id, user_id, text, done, created_at, done_at)`
- `reminders(id, user_id, text, trigger_type, trigger_data, active, created_at)`

APScheduler stores its job state in the same DB (table: `apscheduler_jobs`).

## Hermes Agent Access

The bot exposes a local HTTP API at `http://127.0.0.1:8766` (localhost only). Use it to read or modify any user's data. Get the agent's own user ID from `.env`:

```bash
AGENT_ID=$(grep '^AGENT_USER_ID=' ~/.hermes/skills/personal-assistant/.env | cut -d= -f2)

# List agent user's todos
curl "http://127.0.0.1:8766/todos?user_id=$AGENT_ID"

# Add a todo for the agent user
curl -s -X POST http://127.0.0.1:8766/todos \
  -H "Content-Type: application/json" \
  -d "{\"user_id\": \"$AGENT_ID\", \"text\": \"buy milk\"}"

# List todos for a specific bot user
curl "http://127.0.0.1:8766/todos?user_id=<their_chat_id>"

# Mark todo #3 done
curl -X POST http://127.0.0.1:8766/todos/3/done

# Delete todo #5
curl -X DELETE http://127.0.0.1:8766/todos/5

# List agent user's reminders
curl "http://127.0.0.1:8766/reminders?user_id=$AGENT_ID"

# Add a one-time reminder
curl -s -X POST http://127.0.0.1:8766/reminders \
  -H "Content-Type: application/json" \
  -d "{\"user_id\": \"$AGENT_ID\", \"text\": \"meeting\", \"schedule\": {\"type\": \"once\", \"datetime\": \"2026-05-09T10:00:00\"}}"

# Add a recurring reminder (every 2 hours = 7200 seconds)
curl -s -X POST http://127.0.0.1:8766/reminders \
  -H "Content-Type: application/json" \
  -d "{\"user_id\": \"$AGENT_ID\", \"text\": \"drink water\", \"schedule\": {\"type\": \"interval\", \"seconds\": 7200}}"

# Cancel reminder #2
curl -X DELETE http://127.0.0.1:8766/reminders/2
```

## Deploy

```bash
# From local dev machine
rsync -av personal-assistant/ <user>@<host>:~/.hermes/skills/personal-assistant/

# SSH in, create .env from .env.example
cp ~/.hermes/skills/personal-assistant/.env.example ~/.hermes/skills/personal-assistant/.env
# edit .env: set TELEGRAM_BOT_TOKEN, ALLOWED_USER_IDS, AGENT_USER_ID, LLM_API_KEY

# Enable systemd service (auto-start on boot, restart on crash)
systemctl --user enable --now personal-assistant.service personal-assistant-watchdog.timer
```

## Language & Formatting

The bot always replies in Hebrew. No tables — lists are plain numbered text.
