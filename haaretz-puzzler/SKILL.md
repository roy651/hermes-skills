---
name: haaretz-puzzler
description: Fetches crossword puzzle images from Haaretz (behind paywall) with intelligent caching to reduce login frequency. Responds to /puzzle commands from a dedicated Telegram bot.
version: 1.2.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [haaretz, crossword, puzzle, newspaper, browser-automation, telegram-bot, caching]
    related_skills: []
---

# Haaretz Puzzler — Crossword Fetcher

Fetches crossword puzzle images from Haaretz newspaper behind the paywall,
using browser automation with intelligent caching to minimize login attempts.

## Setup

Credentials are stored in `~/.hermes/skills/haaretz-puzzler/.env`:
```
HAARETZ_EMAIL=<email>
HAARETZ_PASSWORD=<password>
TELEGRAM_BOT_TOKEN=<bot token from BotFather>
ALLOWED_CHAT_IDS=<comma-separated user IDs>
```

Set `ALLOWED_CHAT_IDS` to restrict bot access. Only listed Telegram user/chat IDs can use commands. Example: `391626535` or `391626535,123456789`. Leave empty to allow all.

Optional cache config (defaults in parentheses):
```
PUZZLE_RETRY_HOURS=2        # Hours between retry attempts (2)
```

## Commands (for Telegram bot routing)

| Command | Action |
|---------|--------|
| `/puzzle` | Fetch the latest 3rd crossword image (default) |
| `/puzzle N` | Fetch the Nth crossword image (e.g., `/puzzle 1` for first puzzle) |

## How to Invoke

```bash
bash ~/.hermes/skills/haaretz-puzzler/scripts/run.sh [--index N]
```

Or set `PUZZLE_INDEX` env var.

## Caching Logic (Mandatory)

The cache manager (`puzzle_cache.py`) implements this reactive policy:

| Condition | Behavior |
|-----------|----------|
| **No cache exists** | Always fetch fresh from Haaretz |
| **Cached puzzle exists and same Friday date** | Serve cached. No login. |
| **Cached from older Friday AND >RETRY_HOURS since last check** | Attempt fetch |
| **Cooldown active** | Serve cached, no fetch |

Output includes `SOURCE:fresh` or `SOURCE:cached` so the caller can differentiate.

Cache state: `~/.hermes/skills/haaretz-puzzler/cache_state.json`
Cached images: `~/.hermes/skills/haaretz-puzzler/output/`

## Bot Handler

The Telegram bot handler (`bot_handler.py`) uses long-polling via `requests`:

- **First reply**: `מנסה לשלוף תשבץ אחרון` (immediate)
- **On cached**: Photo with caption `🧩 <title>\nתשבץ נשלף מהזיכרון הקיים`
- **On fresh**: Photo with caption `🧩 <title>\nתשבץ נשלף מהאתר`

Commands:
- `/start` — Welcome message
- `/puzzle [N]` — Fetch puzzle (Nth, default 3)
- `/help` — Show available commands

Run as a background daemon: `nohup .venv/bin/python scripts/bot_handler.py &`

## Browser Automation Flow

When a fresh fetch is needed:

1. Search `https://www.haaretz.co.il/ty-search?q=עושה+שכל` for latest puzzle
2. Extract first article URL from search results
3. Navigate to `https://login.haaretz.co.il/` and log in:
   - Enter email → click "המשך" → wait for password screen
   - Enter password → click "התחברות" (use `exact=True` — see pitfalls)
4. Navigate to the puzzle article URL (now authenticated)
5. Scroll down to trigger lazy-loaded images
6. Find images matching alt pattern: `<N>תשבץ <D.M.YY>`
7. Download the target image (default: 3rd puzzle)
8. Save to output/ directory

## Pitfalls

1. **Playwright `has_text` does substring matching** — `"התחברות"` matches `"(להתחברות עם מייל אחר)"` too! Always use `get_by_role("button", name="התחברות", exact=True)` for the login button.

2. **Bot command parsing** — Incoming text includes the leading `/`. Must strip it: `cmd = text.lower().lstrip("/").split("@")[0]`. Otherwise `/puzzle` never matches `puzzle`.

3. **Long-polling timeout** — If using urllib, the `urlopen(timeout=X)` must be **greater** than Telegram's `getUpdates` timeout parameter, otherwise the client cuts off the connection before Telegram responds. Better: use `requests` library which handles this cleanly.

4. **Two-step login flow** — Email first → "המשך" → password screen appears on same URL → password → "התחברות". Never submit both at once. After login, redirect goes to haaretz homepage — navigate to puzzle article from there.

5. **Date format in alt text** — Day has NO leading zero. `3תשבץ 3.4.26` not `3תשבץ 03.4.26`. Month may or may not have leading zero.

6. **Puzzle not yet published** — New puzzles appear Friday ~midnight-11:00. The page may show the previous week's puzzle. Cache manager handles this with cooldown.

7. **Stale cache** — If the cached image file is deleted, the cache manager detects it and forces a fresh fetch.

## File Structure

```
~/.hermes/skills/haaretz-puzzler/
├── SKILL.md                    # This file
├── .env                        # Credentials (mode 600)
├── cache_state.json            # Cache state (auto-generated)
├── output/                     # Downloaded puzzle images
├── logs/                       # Bot handler logs
└── scripts/
    ├── run.sh                  # Entry point (loads .env, calls cache manager)
    ├── puzzle_cache.py         # Cache manager (decides: fetch or serve cached)
    ├── haaretz_browser.py      # Playwright browser automation
    └── bot_handler.py          # Telegram bot handler (long-polling, requests)
```
