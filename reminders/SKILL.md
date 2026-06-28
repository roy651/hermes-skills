---
name: reminders
description: Set, snooze, reschedule, list, and cancel reminders that ping the user on Telegram. Use whenever the user says "remind me…", "snooze", "remind me later", "what reminders do I have", or "cancel that reminder".
metadata:
  hermes:
    tags: [reminders, cron, telegram]
---

# Reminders

You manage reminders through the **`hermes cron`** CLI (run it via your terminal/bash tool). A reminder
is just a scheduled job whose output is the reminder text, delivered to the user. **Always actually run
the command — never just say "done"; acknowledging without running `hermes cron` does nothing.**

## Set a reminder
```bash
hermes cron create "<schedule>" "Your ENTIRE output is the reminder, verbatim, starting at the ⏰ line — no preamble: ⏰ <what to remind>" \
  --name "<short name>" --deliver telegram --repeat 1
```
- `<schedule>` accepts natural forms — `30m`, `2h`, `every 2h`, `tomorrow 9am`, or a cron expr `0 9 * * *`.
- `--repeat 1` = one-off (omit for recurring).
- Examples → command:
  - "remind me in 90 minutes to call the bank" → `hermes cron create "90m" "...⏰ Call the bank" --name "call bank" --deliver telegram --repeat 1`
  - "every weekday at 8am remind me to take meds" → `hermes cron create "0 8 * * 1-5" "...⏰ Take meds" --name "meds" --deliver telegram`

## Snooze / reschedule  (this is what was missing before)
When the user says "snooze", "remind me later", or "push that to tonight":
1. `hermes cron list` — find the job id of the reminder in question.
2. `hermes cron edit <id> --schedule "<new schedule>"` — e.g. `--schedule "2h"` for "snooze 2 hours".
   (If the original was a one-off that already fired, `hermes cron create` a fresh one instead.)
Then confirm to the user the new time.

## List / cancel
- List: `hermes cron list`
- Cancel: `hermes cron remove <id>`

Keep reminder text short and in the user's language. Confirm the scheduled time back to them.
