#!/usr/bin/env bash
# Runs a scheduled job and posts its stdout to Telegram. Empty stdout sends nothing.
# Everything also goes to the journal. A non-zero exit passes through, so the unit
# fails and OnFailure= (job-failed@.service) sends the alert instead.
#
# Usage: run-job.sh <command> [args...]

source "$(dirname "$0")/telegram.sh"

output=$("$@")
status=$?

printf '%s\n' "$output"

if [ "$status" -ne 0 ]; then
    exit "$status"
fi

if [ -n "$output" ]; then
    send_telegram "$output"
fi
