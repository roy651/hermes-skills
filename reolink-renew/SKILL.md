---
name: reolink-renew
description: Check and renew the free Reolink Cloud subscription (Basic Plan — 1GB/7-day/1-cam). Runs a direct API flow against apis.reolink.com. The account now enforces email MFA, so renewal is a human-in-the-loop flow: it pings you when renewal is due, waits (nudging every 2h) until you say "ready", logs in (which emails an 8-digit code), asks you to paste the code, then renews. A ~30-day "trusted token" is cached so most months renew silently with no code at all. Maintains a rolling buffer of 3 forward monthly reminders so a missed run self-heals.
version: 4.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [reolink, cloud, subscription, renewal, automation, monthly, mfa, human-in-the-loop]
    related_skills: [reminders]
---

# Reolink Cloud Subscription Renewal

Automates renewal of the free Reolink Cloud Basic Plan (1GB storage, 7-day retention, 1 camera, $0). The plan expires monthly and must be re-activated.

**The account enforces email MFA (8-digit code).** A fully-headless login is impossible, so renewal is a **human-in-the-loop state machine**: the skill pings you when renewal is due, waits until you're ready, triggers the code email, and completes login with the code you paste back. A **~30-day trusted token** is cached after each MFA login, so in most months the next run authenticates silently and **no code is needed at all**.

## Triggers

Run this skill when:
- A scheduled `reolink-renew-*` reminder fires (the monthly buffer — see Scheduling).
- The user replies **"ready"**, **"stop"**, or **pastes an 8-digit code** while a renewal is mid-flow.
- The user says "renew reolink", "check reolink subscription", "is my reolink cloud active".

## How to invoke the script

Runs from the git checkout (no `~/.hermes/skills/reolink-renew` copy; `run.sh` is relocatable):

```bash
bash ~/hermes-skills/reolink-renew/scripts/run.sh <mode>
```

| Mode | What it does | Emails a code? |
|------|--------------|----------------|
| `--check-only` | Try the cached trusted token; report status. Never renews. | **No** |
| `--login-init` (or no flag) | Try trusted token → renew if due. If MFA is needed, **email the code** and wait. | Only if trust expired |
| `--login-complete --code 12345678` | Submit the code, then renew if due. | No |
| `--status` | Print the persisted flow state (no network). | No |
| `--force` | (with init/complete) renew even if >2 days remain. | — |

**Output** is machine-parseable `STATUS:` lines — parse these exactly:

```
STATUS: active | renewed | expired | code_sent | mfa_required | error
EXPIRY: YYYY-MM-DD      (on active/renewed/expired)
STEP:   <step>          (on error)
MESSAGE: <text>
```

## Renewal decision (built into the script)

The script only places an order when the plan is **expired or within 2 days of expiry**; otherwise it reports `active` and does nothing. So every buffered reminder is idempotent — an early one is a harmless no-op, only the one near expiry actually renews. You never need to gate this yourself (but `--force` overrides it).

---

## The interactive flow (state machine)

State is persisted in `data/flow.json` (`--status` prints it). Drive it as follows.

### A) A monthly reminder fires (renewal is due)

1. Run `run.sh --check-only`.
2. Branch on `STATUS`:
   - **`active`** with EXPIRY > ~2 days away → not actually due yet (early buffer slot). Report briefly, then **top up the buffer** (below) and stop.
   - **`active`/`expired` and the trusted token worked** but you *are* within the window → run `run.sh` (login-init). It renews **silently** (no code). Go to **Success**.
   - **`mfa_required`** → the trusted token has expired; a code is needed. **Do not send it yet.** Ping the user and wait:

     > 🔔 Time to renew Reolink Cloud (expires {EXPIRY}). Ready to grab the email code?
     > Reply **ready** and I'll trigger it, or **stop** to skip this cycle.

     Set flow to *awaiting-ready*, **arm the recurring 2-hour nudge** (see below — this one nudge persists across the whole exchange), and stop. **Do not proceed until the user replies.**

### B) User replies "ready"

1. Run `run.sh --login-init`. This emails an 8-digit code and returns `STATUS: code_sent` (or renews silently → **Success** if the trust token happens to still work).
2. Set flow to *awaiting-code*. **Leave the nudge running** (do not cancel it — a code can expire before the user acts; the nudge is what re-sends a fresh one every 2h). Ping the user and wait:

   > 📧 I've triggered the login — Reolink just emailed you an 8-digit code (valid ~15 min).
   > Paste it here and I'll finish the renewal. (Say **stop** to abort.)

### C) User pastes an 8-digit code

1. Run `run.sh --login-complete --code <the 8 digits>`.
2. Branch:
   - **`renewed`/`active`** → **now** remove the nudge. Go to **Success**.
   - **`error`** with "rejected (wrong or expired)" → the code lapsed. Run `run.sh --login-init` to send a **fresh** code, tell the user, and keep waiting (nudge stays armed). Stay in *awaiting-code*.

### D) User says "stop"

Remove the `reolink-renew-nudge` job, set flow to *idle*, leave the monthly buffer intact, confirm: "Okay — I'll stop nudging and leave Reolink for now; I'll check again at the next monthly reminder." (A skipped cycle self-heals: the plan lapses until the next buffer reminder, then renews from expired.)

### Success (STATUS: renewed or active after acting)

1. **Remove the `reolink-renew-nudge` job** if one exists (renewal is done — nudging must stop).
2. Send the user the appropriate message (below).
3. **Re-anchor + top up the buffer** off the fresh EXPIRY (below).
4. Reset flow (the script already sets it to `idle`).

---

## Notifications (mandatory after every run)

**On silent/successful renewal (`renewed`):**
```
Reolink Cloud Renewed ✅

Plan: Basic Plan (Monthly) — $0.00
New expiry: {EXPIRY}
{"Renewed silently via the trusted token — no code needed." OR "Completed with your email code."}
A rolling 3-reminder buffer is queued; next check ~{EXPIRY minus 2 days}.
```

**On already-active (`active`, nothing to do):**
```
Reolink Cloud ✅ active until {EXPIRY}. No renewal needed. Buffer re-queued.
```

**On error (`error`) — loud, high-priority (prefix 🚨 REOLINK RENEWAL FAILED):**
```
🚨 REOLINK RENEWAL FAILED at step: {STEP}
Details: {MESSAGE}

Troubleshooting:
- If STEP is login/mfa: the trusted token likely expired and the code was wrong/stale — reply "ready" to retry with a fresh code.
- Check credentials in the .env file.
- If the API rejects everything, Reolink may have changed the MFA flow (see Technical Notes).
```
Never let an error shrink the buffer — still run the top-up so 3 reminders stay queued.

## The 2-hour nudge (persists until renewed or stopped)

**Invariant: while a renewal is pending (flow state is `awaiting_ready` or `awaiting_code`), a 2-hour nudge is ALWAYS queued. It is removed only when the renewal completes (`STATUS: renewed`) or the user says "stop".** A code expiring, a missed ping, or hours passing must never end the nudging — only success or an explicit stop does.

Arm it once, when you first enter a pending state (branch A or B). It is a **recurring** job that re-enters this skill every 2 hours:
```bash
hermes cron create "every 2h" \
  'Continue the pending reolink-renew flow: read data/flow.json state and re-prompt the user (see SKILL.md "On each nudge firing").' \
  --name reolink-renew-nudge --skill reolink-renew --deliver origin
```
- Omit `--repeat` so it **recurs** — it keeps firing every 2h on its own until removed. Create it only if no `reolink-renew-nudge` job already exists (idempotent).
- **Remove it** (`hermes cron list` → `hermes cron remove <id>`) the instant `STATUS: renewed` OR the user says "stop". Pasting a code does **not** remove it (the submit might fail/expire) — only a confirmed renewal does.

### On each nudge firing
Re-enter this skill and branch on `--status`:
- **`awaiting_ready`** → re-ping: "⏰ Still need to renew Reolink Cloud (expires {EXPIRY}). Reply **ready** when you can grab the email code, or **stop**."
- **`awaiting_code`** → the earlier code has expired (codes last ~15 min). Run `run.sh --login-init` to email a **fresh** code, then re-ping: "⏰ Reolink renewal still pending — I've sent a **fresh** 8-digit code (the previous one expired). Paste it, or say **stop**."
- **`idle`** → nothing pending (already renewed or stopped): remove this nudge job and do nothing.

## Scheduling: rolling 3-reminder buffer

Keep **3 future one-time reminders** queued, spaced ~1 month apart, each firing at **EXPIRY − 2 days** at 09:00 (2 days of lead for the human-in-the-loop exchange). If a run is missed, the next buffered reminder catches it.

### Naming (idempotent top-up)
Name each `reolink-renew-YYYY-MM-DD` after its fire date. A slot then exists by name or not — topping up never duplicates.

### Top-up algorithm (run on every Success, and after errors)
1. `hermes cron list` → future jobs named `reolink-renew-YYYY-MM-DD`. Sort by date; `N` = count, `L` = latest.
2. If `N == 0`: create the lead at **EXPIRY − 2 days**; that's `L`, `N = 1`.
3. While `N < 3`: create one at **`L` + 1 month** (09:00); it becomes `L`; `N += 1`.
4. **Only add missing later slots — never touch existing ones.**
5. Delete any `reolink-renew-*` slot whose date is already **past**.

### Re-anchor the lead after every renewal
The plan cycle is **stack-from-expiry** (confirmed 2026-08-04: renewing Aug-4 while expiry was Aug-5 produced Sep-5, i.e. expiry + 1 month, *not* reset-to-today). So expiry stays on a fixed day-of-month and the `+1 month` chain stays aligned. Still, after a renewal, recompute the lead = new EXPIRY − 2 days; if the earliest future slot differs by ≥1 day, delete **all** future `reolink-renew-*` and reseed from step 2.

### Cron command shape
For target `YYYY-MM-DD` at 09:00 use `0 9 <D> <M> *`:
```bash
hermes cron create '0 9 <D> <M> *' \
  'Run the reolink-renew skill (monthly renewal — see SKILL.md interactive flow).' \
  --name reolink-renew-YYYY-MM-DD --skill reolink-renew --deliver origin --repeat 1
```

## Credentials

In `~/.hermes/.env` or the skill `.env`:
```
REOLINK_EMAIL=your@email.com
REOLINK_PASSWORD=your_password_here
```

## Technical Notes

- **API base:** `https://apis.reolink.com`. Login is the my.reolink.com **account-center** OAuth2 password grant (`/v1.0/oauth2/token/`), token valid 30 min.
- **MFA wire-format (reverse-engineered from my.reolink.com, verified live 2026-08-04):**
  - Send code: `POST /v2/auth/mfa/codes` JSON `{clientId, scenario:"users.login_with_password", method:"email", data:{emailAddress}}` → `{id, expiringAt}` and emails an 8-digit code (~15 min TTL).
  - Submit: re-POST the token endpoint with headers `x-verify-scenario: users.login_with_password`, `x-verify-id: <id>`, `x-verify-code: <code>` → `{access_token, mfa_trust_token}`.
  - **Trusted token:** the `mfa_trust_token` is cached in `data/trusted.json` and replayed as the `mfa_trust_token` form field to **skip MFA for ~30 days**. The monthly cycle is ~31 days, so it often *just* lapses each cycle → expect an occasional real code, silent otherwise.
  - If Reolink changes any of this, the single patch-points are `MFA_SEND_URL`, `MFA_SCENARIO`, and `_post_login()` in `scripts/renew-reolink.py`.
- Plan is genuinely $0.00 — no payment flow. Camera re-association is automatic on renewal.
- **Renewal cycle** is calendar-monthly, fixed day-of-month, **stack-from-expiry** (see above).
- **Runtime state** (`data/`, gitignored): `flow.json` (state machine), `trusted.json` (MFA trust token — sensitive).
- **Deploy:** kind-A skill that **runs from the git checkout** — a `git commit` is the deploy; no file-copy/restart. The mini-PC is pull-only; a deliberate push needs `HERMES_ALLOW_PUSH=1 git push`.
