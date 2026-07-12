#!/usr/bin/env bash
# hermes-doctor — on-demand health check for the Hermes agent stack (runs on the mini-PC).
#
# The user's chosen alternative to an always-on watchdog: a Claude Code session runs this
# when asked ("check hermes"), reads the verdict, and fixes if needed. Invoke it either way:
#     ssh roy650@<host> 'bash -s' < _infra/hermes-doctor.sh      # from a dev machine
#     bash ~/hermes-skills/_infra/hermes-doctor.sh               # on the box
#
# It prints HEALTHY / DEGRADED / DOWN plus the evidence to act on. The synthetic proxy call
# is the important bit: it catches the "services up but every LLM call 503s" failure mode
# (E2BIG, expired OAuth token, stale sessions) that a process-liveness check misses.

set -u
PROXY_URL="http://localhost:8765/v1/chat/completions"
verdict="HEALTHY"
demote() { [ "$verdict" = "HEALTHY" ] && verdict="$1"; return 0; }   # only ever lowers HEALTHY

echo "=== hermes-doctor $(date '+%Y-%m-%d %H:%M %Z') ==="

# 1. Are the services up?
for svc in hermes-gateway claude-proxy; do
  state=$(systemctl --user is-active "$svc" 2>/dev/null || true)
  printf 'service %-16s %s\n' "$svc" "$state"
  [ "$state" = "active" ] || verdict="DOWN"
done

# 2. Did the gateway log recent upstream failures?
errs=$(journalctl --user -u hermes-gateway --since "10 min ago" 2>/dev/null \
        | grep -Ec "Argument list too long|claude_unavailable|API call failed after" || true)
echo "gateway upstream errors (last 10m): ${errs:-0}"
[ "${errs:-0}" -gt 0 ] && demote DEGRADED

# 3. Does a real one-token prompt make it through the proxy?
read -r code backend < <(python3 - "$PROXY_URL" <<'PY'
import json, sys, urllib.request
body = {"model": "claude-code",
        "messages": [{"role": "user", "content": "Reply with exactly: DOCTOR_OK"}]}
req = urllib.request.Request(sys.argv[1], data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
try:
    r = urllib.request.urlopen(req, timeout=60)
    print(r.status, r.headers.get("X-Proxy-Backend", "?"))
except Exception as e:
    print("ERR", str(e)[:60].replace(" ", "_"))
PY
)
echo "synthetic proxy call: HTTP $code  backend=$backend"
[ "$code" = "200" ] || verdict="DOWN"

echo "=== VERDICT: $verdict ==="
