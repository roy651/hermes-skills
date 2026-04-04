---
name: reolink-renew
description: Check and renew the free Reolink Cloud subscription (Basic Plan — 1GB/7-day/1-cam). Runs a direct API flow against apis.reolink.com — no browser required. After each run, schedules a reminder one day before the subscription expires so renewal is never missed.
version: 1.0.0
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

## After Every Run: Schedule the Next Reminder

**This is mandatory.** After the script exits successfully (STATUS is `active` or `renewed`), always schedule a reminder for one day before the expiry date.

### How to schedule

Use the `schedule` skill or `CronCreate` tool to create a one-time trigger:

- **When:** `EXPIRY - 1 day` at 09:00 local time (Asia/Jerusalem)
- **What:** run this skill (same as above — `bash ~/.hermes/skills/reolink-renew/scripts/run.sh`)
- **Label:** `reolink-renew`

Before creating, check if a `reolink-renew` reminder already exists and delete the old one first to avoid duplicates.

### Example (if EXPIRY is 2026-05-04, remind on 2026-05-03 at 09:00):

```
/schedule reolink-renew: run reolink-renew skill on 2026-05-03 at 09:00
```

## What to Report to the User

| STATUS | Report |
|---|---|
| `active` | "Your Reolink Cloud subscription is active until {EXPIRY}. I've scheduled a reminder for {EXPIRY-1day}." |
| `renewed` | "Renewed your Reolink Cloud free plan. Active until {EXPIRY}. Reminder set for {EXPIRY-1day}." |
| `expired` + `--check-only` | "Your Reolink Cloud subscription expired on {EXPIRY}. Run again without --check-only to renew." |
| `error` | "Renewal failed at step '{STEP}': {MESSAGE}. Check credentials or run with --verbose." |

## Credentials

Required in environment or `~/.hermes/skills/reolink-renew/.env`:
```
REOLINK_EMAIL=...
REOLINK_PASSWORD=...
```

## Technical Notes

- API base: `https://apis.reolink.com` (not cloud.reolink.com)
- Auth: OAuth2 password grant — token valid 30 minutes, no browser/Cloudflare challenge needed
- Plan is genuinely $0.00 — no payment flow
- Cameras are in Israel, cloud storage is in Italy (`reolink_cloud_it` region)
- Device re-association is handled automatically if the camera becomes unlinked on renewal
- The `.venv` is created automatically on first run via `run.sh`
