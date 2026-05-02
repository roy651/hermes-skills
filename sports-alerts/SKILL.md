---
name: sports-alerts
description: Weekly sports digest and 5-minute event reminders via Telegram — F1, cycling, NFL, Hapoel Tel Aviv (soccer & basketball), NBA (Deni Avdia).
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [cron, sports, telegram, reminders]
---

# Sports Alerts

Weekly digest every Sunday 10:00 Israel time listing all upcoming sports events for the week. Sends a Telegram reminder 5 minutes before each event.

## Output Language & Formatting

All messages are sent in Hebrew. Tables are wrapped in triple-backtick code blocks.

## Sports Covered

| Sport | Source | Notes |
|---|---|---|
| Formula 1 | ESPN API | Race, Qualifying, Sprint Race (configurable) |
| UCI WorldTour cycling | TNT Sports / Wikipedia | Per-stage times + 5-min reminder (Wikipedia fallback) |
| NFL | ESPN API | Filtered by Israel midnight cutoff (configurable) |
| Hapoel Tel Aviv soccer | ESPN API (Israeli Premier League) | |
| Hapoel Tel Aviv basketball | Sofascore API | |
| NBA | ESPN API | Portland Trail Blazers games (Deni Avdia) |

## Hermes Tool: Run Digest On-Demand

When the user asks for this week's sports schedule or wants to refresh the digest, run:

```bash
cd ~/.hermes/skills/sports-alerts && .venv/bin/python weekly.py
```

This fetches fresh data, updates `reminders.json`, and sends the digest to Telegram immediately.

## Hermes Tool: Manage Reminders

When the user asks to skip, mute, enable, or check a specific sports event, run:

```bash
cd ~/.hermes/skills/sports-alerts

# List this week's events and their status
.venv/bin/python override.py list

# Disable a reminder (accepts partial name, Hebrew or English)
.venv/bin/python override.py disable "<query>"

# Re-enable a muted event
.venv/bin/python override.py enable "<query>"
```

Examples of what the user might say → what to run:
- "דלג על ה-F1 בסאו פאולו" → `override.py disable "sao paulo"`
- "אל תזכיר לי על המשחק של ה-NBA ביום שלישי" → `override.py disable "tuesday"` or `override.py list` first then disable by ID
- "הפעל מחדש את התזכורת לגמר" → `override.py enable "final"`
- "מה יש השבוע?" → `override.py list`

Run `override.py list` first when the query is ambiguous — it shows event IDs and titles to confirm before disabling.

## Configuration

Edit `~/.hermes/skills/sports-alerts/config.yaml`:

```yaml
sports:
  f1:
    enabled: true
    sessions: [race, qualifying, sprint]
    reminder: true
  nfl:
    max_local_hour: 24    # midnight Israel cutoff; set to 25 to allow 1 AM games
  cycling:
    reminder: true        # per-stage 5-min reminders (requires TNT Sports to be reachable)
```

## Install

```bash
# 1. Copy skill to Hermes
cp -r ~/hermes-skills/sports-alerts ~/.hermes/skills/

# 2. Create venv and install dependencies
cd ~/.hermes/skills/sports-alerts
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

# 3. Register weekly cron job in Hermes
python3 - << 'EOF'
import json
JOBS_FILE = "/home/roy650/.hermes/cron/jobs.json"
with open(JOBS_FILE) as f:
    data = json.load(f)
jobs = data if isinstance(data, list) else data.get("jobs", [])
with open("/home/roy650/.hermes/skills/sports-alerts/job.json") as f:
    new_job = json.load(f)
jobs = [j for j in jobs if j.get("id") != new_job["id"]]
jobs.append(new_job)
result = jobs if isinstance(data, list) else {**data, "jobs": jobs}
with open(JOBS_FILE, "w") as f:
    json.dump(result, f, indent=2)
print("job registered")
EOF

# 4. Install 5-minute watchdog systemd timer
cat > ~/.config/systemd/user/sports-watchdog.service << 'EOF'
[Unit]
Description=Sports Alerts Reminder Watchdog

[Service]
Type=oneshot
ExecStart=/home/roy650/.hermes/skills/sports-alerts/.venv/bin/python /home/roy650/.hermes/skills/sports-alerts/watchdog.py
StandardOutput=append:/home/roy650/.hermes/skills/sports-alerts/logs/watchdog.log
StandardError=append:/home/roy650/.hermes/skills/sports-alerts/logs/watchdog.log
EOF

cat > ~/.config/systemd/user/sports-watchdog.timer << 'EOF'
[Unit]
Description=Sports Alerts Reminder Watchdog (every 5 min)

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now sports-watchdog.timer
systemctl --user status sports-watchdog.timer

# 5. Run weekly digest immediately to test
cd ~/.hermes/skills/sports-alerts && .venv/bin/python weekly.py
```

## File Structure

```
sports-alerts/
├── SKILL.md
├── job.json              # Hermes weekly cron (Sunday 07:00 UTC = 10:00 Israel)
├── requirements.txt
├── config.yaml           # sport toggles and parameters
├── fetchers/
│   ├── base.py           # Event dataclass, Fetcher ABC
│   ├── espn.py           # F1, NFL, NBA, Hapoel soccer
│   ├── cycling.py        # UCI WorldTour via procyclingstats.com
│   └── sofascore.py      # Hapoel basketball
├── weekly.py             # fetch → save reminders.json → send digest
├── watchdog.py           # check reminders.json → fire due alerts
├── override.py           # Hermes tool: enable/disable events
├── notifier.py           # Telegram sender (uses ~/.hermes/.env token)
├── data/
│   └── reminders.json    # live queue: events + enabled/fired state
└── logs/
    ├── weekly.log
    └── watchdog.log
```

## Extending: Adding a New Sport

1. Create `fetchers/mysport.py` implementing the `Fetcher` ABC (`fetch_week` → `list[Event]`)
2. Add a config entry in `config.yaml` under `sports:`
3. Import and call in `weekly.py`'s `fetch_all()` — one line

No other changes needed.
