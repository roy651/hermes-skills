---
name: reolink-renew
description: Check and renew the free Reolink Cloud subscription (Basic Plan — 1GB/7-day/1-cam). Runs a direct API flow against apis.reolink.com — no browser required. After each run, maintains a rolling buffer of 3 forward monthly reminders (topping the queue back up to 3 every run) so a single missed run self-heals instead of silently lapsing. Renews only when the plan is expired or within 2 days of expiry; buffered reminders that fire early are harmless no-ops. Sends a notification on every run, and a loud high-priority alert on any failure.
version: 3.1.0
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
- A scheduled reminder fires (one of the rolling 3-reminder buffer — see Scheduling below)

## How to Invoke

The skill runs from the git checkout (there is **no** `~/.hermes/skills/reolink-renew` copy on this box — `run.sh` is relocatable via `dirname "$0"`):

```bash
bash ~/hermes-skills/reolink-renew/scripts/run.sh
```

Flags:
- `--check-only` — report status without renewing (a true no-op — the **only** safe way to inspect)
- `--verbose`    — print API debug info to stderr

## Renewal Decision (check first, then renew only if needed)

⚠️ **Renew-mode (`run.sh` with no flag) ALWAYS places a renewal order — even when the plan is currently active.** So never blindly renew from a buffered reminder, or you'll stack redundant orders. Always decide first:

1. Run `run.sh --check-only` and read `STATUS` / `EXPIRY`.
2. **Renew** (run `run.sh` with no flag) only if:
   - `STATUS: expired`, **or**
   - `STATUS: active` **and** `EXPIRY` is **≤ 2 days** away.
3. Otherwise (active with more than 2 days left) → **do not renew**. This run is just a buffer heartbeat; go straight to topping up the schedule.

This makes every buffered reminder idempotent: only the one that fires near expiry actually renews; the earlier buffer reminders are harmless no-ops.

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

Next reminders: 3-deep buffer queued (~monthly), earliest {EXPIRY minus 1 day} at 09:00

This was a free-tier renewal — no payment was charged.
```

### On already-active (STATUS: active)

```
Reolink Cloud Status [ACTIVE]

Your subscription is active until {EXPIRY}.
No renewal needed right now.

A rolling 3-reminder buffer is queued (~monthly), so renewal can't be silently missed.
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

## Scheduling: Rolling 3-Reminder Buffer

**Mandatory on every run** — renewal, active heartbeat, **and even after an error** (see Failure Alerting). Instead of one fragile one-shot that dies silently if a single run is missed, there must always be **3 future one-time reminders queued**, spaced ~1 month apart. If any run is missed, the next buffered reminder catches it and self-heals; worst case is a bounded lapse, never an indefinite silent one.

### Reminder naming (makes top-up idempotent)
Name each reminder `reolink-renew-YYYY-MM-DD` after the date it fires. A slot then either exists by name or it doesn't — so topping up never creates duplicates and needs no "remove-first" dance.

### Top-up algorithm (run this every time)
1. `hermes cron list` → collect jobs whose name starts with `reolink-renew-` **and** whose next run is in the future. Sort by date. Let `N` = count, `L` = latest date.
2. If `N == 0` (first run / empty buffer): create the earliest slot at **EXPIRY − 1 day**; that becomes `L`, `N = 1`.
3. While `N < 3`: create one more one-time reminder at **`L` + 1 month** (09:00); that becomes the new `L`; `N += 1`. Repeat until `N == 3`.
4. **Only add the missing later slots — never touch reminders that already exist.** Re-running just tops the queue back to 3 and stops. That's the whole idempotency guarantee.
5. **Clean up stale slots:** delete any `reolink-renew-*` job whose date is already **in the past** (left over from an outage where reminders went past-due without firing). They're excluded from the future-count so they don't cause duplicates, but they clutter the list and can fire late on catch-up.

Each reminder is a **one-time** job (`--repeat 1`) that re-invokes this skill. When it fires it renews-if-needed (per the Renewal Decision) and re-runs this top-up, extending the buffer by one — so the 3-deep queue rolls forward on its own.

### Cron expression
For target `YYYY-MM-DD` at 09:00 use `0 9 <D> <M> *`. Full command shape:
```bash
hermes cron create '0 9 <D> <M> *' \
  'Run the reolink-renew skill: bash ~/hermes-skills/reolink-renew/scripts/run.sh' \
  --name reolink-renew-YYYY-MM-DD --skill reolink-renew --deliver origin --repeat 1
```
Example (EXPIRY 2026-08-05) → the 3 buffer slots are:
- `reolink-renew-2026-08-04` → `0 9 4 8 *`
- `reolink-renew-2026-09-04` → `0 9 4 9 *`
- `reolink-renew-2026-10-04` → `0 9 4 10 *`

### Re-anchor the lead slot after every renewal (margin fix — do NOT skip)
The `+1 month` chain keeps a fixed day-of-month, but the **lead** reminder erodes its 1-day safety margin if you trust the chain for the front of the queue: seed the lead at EXPIRY−1 (e.g. Aug-4 for expiry Aug-5), it fires and renews Aug-4 → new expiry Sep-4, but the next chained slot is Sep-4 = expiry *day*, not Sep-3 → the margin is gone and it converges to firing **on** expiry day. See `references/scheduling-semantics.md` for the full cycle-by-cycle trace.

So after **every renewal** (STATUS: renewed), re-derive the lead from reality rather than the chain:
- Recompute the target lead date = new `EXPIRY − 1 day`.
- If the earliest future `reolink-renew-*` slot differs from it by **≥ 1 day**, delete **all** future `reolink-renew-*` jobs and reseed from step 2 off the fresh EXPIRY (the delete-first reseed is why this never creates a duplicate same-month slot). If it already matches, leave the buffer alone.

This keeps the "renew a day early" margin every cycle. NB: whether the drift even occurs depends on Reolink's active-renewal semantics (stack-from-expiry vs reset-from-renewal-date) — an **open question**, see the reference. Re-anchoring is correct either way.

## Failure Alerting (loud — mandatory)

If STATUS is `error`, or the script cannot run at all:
- **Alert loudly, not quietly.** Send the user a high-priority message prefixed `🚨 REOLINK RENEWAL FAILED` via Telegram (and a push notification if available). Include the failing `STEP`, `MESSAGE`, and the troubleshooting list from the error notification above. This must not be a silent log line — the user has to notice.
- **Never let a failure shrink the buffer.** Still run the top-up so 3 future reminders stay queued — the next buffered reminder becomes the automatic retry. An error must never leave the queue empty.

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
- **Renewal cycle is calendar-monthly on a fixed day-of-month, NOT a rolling 30 days** — renew on the Nth → expires the Nth next month (evidence: Jul-5 → Aug-5; a 30-day cycle would give Aug-4). This is why the `+1 month` buffer chain stays aligned. Whether an *early* (still-active) renewal stacks onto the current expiry or resets from the renewal date is still unconfirmed — see `references/scheduling-semantics.md` and verify on the next active-state renewal.
- **Deploy:** this skill is kind-A but **runs from the git checkout** (`~/hermes-skills/reolink-renew/`, no `~/.hermes/skills/` copy), so a git commit is the deploy — no file-copy/restart. The mini-PC host is **pull-only**; a deliberate push from there needs `HERMES_ALLOW_PUSH=1 git push`.
