---
name: telegram-bot-skill-router
description: Pattern for creating dedicated Telegram bots that route commands to specific Hermes skills — isolating bot capabilities from the main Hermes agent.
version: 1.0.0
author: Hermes Agent
---

# Dedicated Telegram Bot per Skill

## When to Use

When you want to expose a specific Hermes skill to external users (contacts, friends, colleagues) without giving them access to the full Hermes agent. Each skill gets its own bot with limited, focused commands.

## Architecture

```
External Users ──► @DedicatedBot (Telegram Bot API)
                       │
                       ▼ (polls via bot_handler.py)
              Command Router (bot_handler.py)
                       │
                       ▼ (subprocess)
              Skill Script (run.sh → Python/browser)
                       │
                       ▼
              Telegram sendPhoto/sendMessage back to user
```

## Setup Steps

1. **Create the bot** via BotFather:
   - Send `/newbot` to @BotFather
   - Choose name and username
   - Save the token

2. **Create skill directory**: `~/.hermes/skills/<skill-name>/`

3. **Files to create**:
   - `SKILL.md` — skill documentation
   - `.env` — credentials (HAARETZ_EMAIL, HAARETZ_PASSWORD, TELEGRAM_BOT_TOKEN)
   - `scripts/run.sh` — entry point for the skill
   - `scripts/<skill>_browser.py` — browser automation or API calls
   - `scripts/bot_handler.py` — Telegram poll loop and command router
   - `scripts/puzzle_cache.py` — optional cache manager (see caching below)

4. **Store credentials** in `.env` (mode 600):
   ```
   SKILL_SPECIFIC_CREDENTIAL=value
   TELEGRAM_BOT_TOKEN=<botfather-token>
   ```

## Bot Handler Pattern (bot_handler.py)

```python
import json, os, time, subprocess
from urllib.request import Request, urlopen

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

def api_call(method, data=None):
    url = f"{API_BASE}/{method}"
    payload = json.dumps(data).encode() if data else None
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

def send_message(chat_id, text):
    return api_call("sendMessage", {"chat_id": chat_id, "text": text})

def send_photo(chat_id, photo_path, caption=None):
    # multipart/form-data POST to /bot<token>/sendPhoto
    ...

def poll_loop():
    offset = None
    while True:
        updates = api_call("getUpdates", {"offset": offset, "timeout": 10})
        for update in updates.get("result", []):
            msg = update.get("message", {})
            chat_id = msg.get("chat", {}).get("id")
            text = msg.get("text", "")
            if text.startswith("/puzzle"):
                # Run skill, send result
                result = subprocess.run(["bash", "scripts/run.sh"], ...)
                send_photo(chat_id, media_path, caption)
            offset = update["update_id"] + 1
        time.sleep(3)
```

## Caching Pattern (for paywalled content)

Skills that hit paywalled sites should cache results to minimize login attempts:
- Store cache state in `cache_state.json` (last_fetch_date, last_retry_at)
- Store downloaded content in `output/` directory
- On non-publish days: always serve cache, no login
- On publish day: attempt fetch, fallback to cache, schedule retry

## Pitfalls

1. **Playwright `has_text` is substring match** — `has_text="התחברות"` also matches `(להתחברות עם מייל אחר)`. Use `exact=True` or `get_by_role("button", name="exact text", exact=True)`.

2. **Login page JS transitions** — The URL may not change when the page content transitions (email → password screen). Always check for element visibility, not URL changes.

3. **Bot detection on headless browsers** — Add init scripts to hide webdriver flag: `Object.defineProperty(navigator, 'webdriver', {get: () => undefined})`

4. **Empty getUpdates on first run** — Normal until someone sends the bot a message.

5. **No need for webhook** — Long polling (`getUpdates` with timeout=10) is simpler and sufficient for low-traffic bots.

6. **Multipart photo upload** — Use http.client HTTPSConnection for multipart/form-data to avoid external dependencies (requests, aiohttp for uploads).
