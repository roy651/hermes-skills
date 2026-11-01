#!/usr/bin/env bash
# Run daily by reolink-renew.timer. Stays offline and silent until the plan is within two days
# of expiry. Then it renews through the trusted token if it can, or asks Roy to start the MFA
# flow, once a day until renewed. stdout is what Roy sees on Telegram.

set -u

run="$(dirname "$0")/run.sh"

expiry=$(bash "$run" --status | sed -n 's/^EXPIRY: //p')
if [ -n "$expiry" ] && [ "$(date -u +%F)" \< "$(date -u -d "$expiry -2 days" +%F)" ]; then
    exit 0
fi

if ! result=$(bash "$run" --login-init --no-email); then
    printf '%s\n' "$result"
    exit 1
fi

if [[ "$result" == *"STATUS: mfa_required"* ]]; then
    cat <<MESSAGE
🔔 Reolink Cloud expires ${expiry:-soon} and this renewal needs an email code.

In the Claude app, open homeabit and send:

Run bash reolink-renew/scripts/run.sh --login-init. If it prints STATUS: code_sent, find the newest Reolink code in Gmail and run bash reolink-renew/scripts/run.sh --login-complete --code <code>. Then run --check-only and show me the raw output.
MESSAGE
    exit 0
fi

new_expiry=$(printf '%s\n' "$result" | sed -n 's/^EXPIRY: //p')
case "$result" in
    *"STATUS: renewed"*)
        echo "✅ Reolink Cloud renewed. New expiry: $new_expiry" ;;
    *"STATUS: active"*)
        ;;  # not quite inside the two-day window yet: tomorrow's run renews
    *)
        printf 'Unexpected Reolink result:\n%s\n' "$result"
        exit 1 ;;
esac
