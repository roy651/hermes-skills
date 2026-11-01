#!/usr/bin/env bash
# Called by job-failed@.service when a unit fails: names the job, its last log lines and the fix.
#
# Usage: notify-failed.sh <unit-name>

source "$(dirname "$0")/telegram.sh"

unit=$1

# A failed HTTP call can print its URL, which holds the bot token. iconv drops the partial
# character tail -c can leave, which Telegram may reject and lose the alert.
last_lines=$(journalctl --user -u "$unit.service" -n 15 --no-pager -o cat \
    | sed -E 's/bot[0-9]+:[A-Za-z0-9_-]+/bot<redacted>/g' \
    | tail -c 2500 \
    | iconv -c -f UTF-8 -t UTF-8)

if [ "$unit" = "claude-rc" ]; then
    fix="Remote Control is down. It retries every 6 hours.
Check: ssh -t roy650@100.78.84.7 'claude auth status'
If logged out: ssh -t roy650@100.78.84.7 'claude auth login'
Then: systemctl --user reset-failed claude-rc && systemctl --user restart claude-rc"
else
    fix="Full log: journalctl --user -u $unit -n 50
Re-run: systemctl --user start $unit"
fi

send_telegram "🚨 Job failed: $unit

$last_lines

$fix"
