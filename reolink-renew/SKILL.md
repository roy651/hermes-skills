---
name: reolink-renew
description: Check and renew the free Reolink Cloud subscription (Basic Plan — 1GB/7-day/1-cam). Runs a direct API flow against apis.reolink.com — no browser required. After each run, schedules a reminder one day before the subscription expires so renewal is never missed. Sends the user a Telegram notification on every run with full status details and next expiry date.
version: 2.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [reolink, cloud, subscription, renewal, automation, monthly]
    related_skills: []
---

# Reolink Cloud Subscription Renewal

Automates renewal of the free Reolink Cloud Basic Plan (1GB storage, 7-day retention, 1 camera, $0). The plan expires monthly and requires manual re-activation — this skill handles that automatically.

## Triggers

Run this skill when:
- User says "renew reolink cloud", "check reolink subscription", "is my reolink cloud active"
- A scheduled reminder fires (set up automatically after each run — see Scheduling below)

## How to Invoke

```bash
bash ~/.hermes/skills/reolink-renew/scripts/run.sh
```

Flags:
- `--check-only` — report status without renewing
- `--verbose`    — print API debug info to stderr

## Output Format

The script prints structured lines to stdout. Parse these exactly:

```
STATUS: active|renewed|expired|error
EXPIRY: YYYY-MM-DD          (present on active/renewed/expired)
STEP:   <step-name>          (present on error only)
MESSAGE: <human description>
```

## Notification (Mandatory After Every Run)

**You MUST send the user a message** after every execution of this skill, whether the run succeeds or fails. Include all the information below.

### On successful renewal (STATUS: renewed)

```
Reolink Cloud Renewed [SUCCESS]

Plan: Basic Plan (Monthly) — $0.00
Storage: 1GB, Retention: 7 days, Cameras: 1
New expiry: {EXPIRY}
Country: Israel
Device: will auto-associate if unlinked

Next reminder: {EXPIRY minus 1 day} at 09:00

This was a free-tier renewal — no payment was charged.
```

### On already-active (STATUS: active)

```
Reolink Cloud Status [ACTIVE]

Your subscription is active until {EXPIRY}.
No renewal needed right now.

I will remind you the day before it expires.
```

### On expired, check-only mode (STATUS: expired)

```
Reolink Cloud [EXPIRED]

Your last subscription expired on {EXPIRY}.
Run without --check-only to renew immediately.
```

### On error (STATUS: error)

```
Reolink Cloud [ERROR]

Renewal failed at step: {STEP}
Details: {MESSAGE}

Troubleshooting:
- Check that credentials in the .env file are correct
- Make sure 2FA is disabled on the account
- If login fails, the server-side API may be blocked by Cloudflare
```

## Scheduling: Automatic Next-Run Reminder

**This is mandatory.** After the script exits successfully (STATUS is `active` or `renewed`), always schedule a one-time cron reminder for one day before the expiry date.

### How to schedule

Use the `cronjob` tool with action `create`:

- **prompt:** `Run the reolink-renew skill: bash ~/.hermes/skills/reolink-renew/scripts/run.sh`
- **schedule:** cron expression for `09:00` on `EXPIRY - 1 day`. E.g. if EXPIRY is 2026-06-04, schedule is `0 9 3 6 2026`
- **name:** `reolink-renew`
- **deliver:** `telegram` (so the notification reaches the user via Telegram)

### One-time jobs, not recurring

Create a **one-time** cron job (not recurring). Each renewal creates a new expiry date, and a new one-time reminder is scheduled. This avoids drift if the renewal date shifts.

Before creating, use `cronjob action=list` to check if a `reolink-renew` job already exists. If it does, remove it first with `cronjob action=remove` to avoid duplicate reminders.

## Credentials

Required in environment or `~/.hermes/skills/reolink-renew/.env`:
```
REOLINK_EMAIL=your@email.com
REOLINK_PASSWORD=your_password_here
```

## Technical Notes

- API base: `https://apis.reolink.com` (not cloud.reolink.com)
- Auth: OAuth2 password grant — token valid 30 minutes, no browser/Cloudflare challenge needed
- Plan is genuinely $0.00 — no payment flow, no payment method required
- Cameras are in Israel, cloud storage is in Italy (`reolink_cloud_it` region)
- Device re-association is handled automatically if the camera becomes unlinked on renewal
- The `.venv` is created automatically on first run via `run.sh`
