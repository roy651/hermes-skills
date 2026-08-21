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

## Actually performing the merge + deploy

The check above is read-only. This is the deploy, learned the hard way on 2026-08-21
(2,918 commits behind; the merge itself was clean, the *deploy* was where it went wrong).

### The venv is uv-managed and has NO pip

`venv/bin/pip` does not exist — `python -m pip` reports *No module named pip*. The
project ships a `uv.lock` and a `[tool.uv] override-dependencies` block that plain
pip would ignore anyway. `hermes_cli/update_lock.py` describes the real update as
**"git pull + uv sync + desktop rebuild"**.

### `uv sync` alone is NOT enough — extras are the trap

A bare `uv sync` installs only the base dependency group. This install has **every
extra** present (`messaging`, `mcp`, `voice`, `google`, `slack`, `matrix`, …), and
`python-telegram-bot` and `mcp` live in extras. A bare sync silently upgraded
`cryptography` while leaving `mcp` at 1.28.1 when 0.20.5 requires 2.0.0.

```bash
cd ~/.hermes/hermes-agent
export VIRTUAL_ENV="$PWD/venv"
uv sync --frozen --inexact --all-extras --active
```

`--frozen` = install exactly what the lock pins (the set upstream tested).
`--inexact` = do not prune packages absent from the lock.

### Order of operations

```bash
# 1. safety nets FIRST -- the schema migration is not reversible by git alone
git tag pre-upstream-merge-$(date +%Y%m%d-%H%M%S) HEAD
cp -al venv venv.bak-$(date +%Y%m%d-%H%M%S)          # hardlink: instant, 1.1G costs nothing
mkdir -p ~/hermes-db-backup-$(date +%Y%m%d-%H%M%S)
sqlite3 ~/.hermes/state.db ".backup '<dir>/state.db'"

# 2. merge in a SCRATCH WORKTREE, never the live checkout
git worktree add -b merge/upstream-<date> ~/hermes-merge local/improvements
cd ~/hermes-merge && git merge origin/main

# 3. smoke-test with an ISOLATED HERMES_HOME -- otherwise the new binary
#    migrates the live state.db before you have committed to the upgrade
HERMES_HOME=/tmp/smoke .venv-test/bin/hermes --version

# 4. only then: stop the watchdog TIMER (it fires every 5m and will fight you),
#    stop the gateway, fast-forward, uv sync --all-extras, then
#    proxy -> wait for health -> gateway
```

### Verify afterwards

Run the `minipc-audit` skill. A 2,918-commit jump can re-enable a service on a
wildcard address or change a bind; the audit catches exactly that, independently.

### Pitfalls that cost real time

- **Never `$?` after a pipeline.** `uv sync ... | tail | sed; RC=$?` captures `sed`,
  not the install — a failed dependency step reported success and the gateway was
  restarted on stale libraries. Capture the exit code directly.
- **`git merge-tree` "changed in both" is not a conflict.** Counting those lines
  predicted conflicts on a merge that applied cleanly. Use a real dry-run merge.
- **The schema migration runs at gateway start.** After it, rollback = restore the
  DB backup *and* reset the code, and any messages since the migration are lost.
- Keep the safety nets for a few days before deleting them.

### Do not let it drift this far again

2,918 commits merged cleanly, and would have at any point along the way. The drift
happened because the job only *reported*; nobody acted. Merge monthly into a
worktree automatically, report "clean, N commits, say go", and keep the **deploy**
manual — it restarts everything on the box.

### Restarting claude-proxy without patching upstream

`hermes update` restarts the **gateway** only — it knows nothing about
`claude-proxy`. Patching upstream to teach it would create a merge conflict
every week, which is exactly what we are trying to avoid.

There is no update/restart hook: the add-on hook system (`gateway/hooks.py`)
dispatches only `gateway:startup`, `agent:start|step|end|main`, and the
shell-hook system covers agent-level events (tool/LLM/session), not deploys.

**The answer is a systemd drop-in**, at
`~/.config/systemd/user/hermes-gateway.service.d/proxy-first.conf`:

```ini
[Service]
ExecStartPre=-/usr/bin/systemctl --user restart --no-block claude-proxy.service
ExecStartPre=-/bin/bash -c 'for i in $(seq 1 30); do curl -sf -m 2 http://localhost:8765/health >/dev/null && exit 0; sleep 1; done; exit 0'
```

Why this and not a hook:
- It lives **outside the git checkout**, so it survives `hermes update` and can
  never conflict with upstream.
- It covers **every** restart path — `hermes update`, the watchdog, manual
  `systemctl`, and boot. A `gateway:startup` hook only covers restarts that
  reach the gateway's Python startup, and fires *after* the gateway is already
  up, which is the wrong order.
- `--no-block` avoids a systemd transaction deadlock (we are inside the
  gateway's own start job); the curl loop is the real gate.
- Both `ExecStartPre` lines are prefixed `-` so a proxy problem can never block
  the gateway from starting.

Verified 2026-08-21: `systemctl --user restart hermes-gateway` restarted the
proxy first (lower PID), returned in 2s, both healthy.
