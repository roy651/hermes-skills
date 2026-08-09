---
name: hermes-upstream-sync
description: Weekly cron job that checks if the local hermes-agent fork is behind NousResearch upstream and reports conflicts.
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [cron, maintenance, git, upstream, sync]
---

# Hermes Upstream Sync Check

Weekly cron job (every Sunday 09:00) that fetches upstream, counts commits behind, checks for rebase conflicts, and reports to Telegram. Silent if already up to date.

## Output Language & Formatting

**Language:** Always write sync/check reports in English.
**Tables:** Wrap all tabular output in triple-backtick code blocks so they render correctly in Telegram.

## Install

Add the cron job to Hermes via the Hermes CLI or by inserting `job.json` into `~/.hermes/cron/jobs.json`.

The job runs on the machine where hermes-agent is installed and requires:
- `~/.hermes/hermes-agent/` — the local fork (remote `origin` = NousResearch upstream)
- A Telegram delivery origin configured in the job

## What It Does

1. `git fetch origin` — fetches upstream without checking out
2. Counts commits behind `origin/main`
3. If 0 → `[SILENT]`, nothing sent
4. If behind → lists notable `feat`/`fix` commits and runs a dry-run rebase to detect conflicts
5. Sends a concise Telegram report: commits behind, notable changes, clean/conflict verdict

## After a `hermes upgrade` or gateway restart

After pulling upstream and restarting `hermes-gateway.service`, **also restart `claude-proxy.service`**:

**Restart them in order — proxy first, gateway second — waiting for the proxy to serve in between:**

```bash
systemctl --user restart claude-proxy
until curl -sf -m 3 http://localhost:8765/health >/dev/null; do sleep 3; done
systemctl --user restart hermes-gateway
```

Restarting both in one command (`restart claude-proxy hermes-gateway`) kills the backend out from under whatever call the gateway has in flight, which surfaces as `httpx.RemoteProtocolError: Server disconnected without sending a response` and a retry storm in the gateway log. It recovers on its own, but it makes every restart look like a fault — that noise cost real debugging time on 2026-08-09. Ordering the restarts removes it entirely.

Why restart the proxy at all: it caches conversation session IDs in memory (for `claude --resume`). If the proxy runs for days without restart, its cached session IDs expire. When the gateway restarts and sends a fresh request, the proxy tries to resume a stale session → `claude exited 1` on every call → the gateway reports "model provider failed after retries" to the user.

Symptom: gateway is up, claude CLI works directly, but every API call returns `503 claude unavailable: claude exited 1`.
Fix: `systemctl --user restart claude-proxy` — clears the in-memory session cache; next call opens a fresh session.

## Updating the Job Prompt

Edit `job.json` in this repo, then update the live job on the mini-PC:

```bash
python3 - << 'EOF'
import json

with open('/home/roy650/.hermes/cron/jobs.json') as f:
    data = json.load(f)

jobs = data if isinstance(data, list) else data.get('jobs', [])
with open('job.json') as f:
    new_job = json.load(f)

for i, job in enumerate(jobs):
    if job.get('id') == 'hermes_upstream_sync_check':
        # Preserve runtime fields
        new_job = {**job, 'prompt': new_job['prompt'], 'schedule': new_job['schedule']}
        jobs[i] = new_job
        break

with open('/home/roy650/.hermes/cron/jobs.json', 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
EOF
```
