# hermes-skills — working & deploy guide

## ⚠️ PRIVACY — THIS REPO IS PUBLIC
**Never commit portfolio PII.** No holdings/positions, quantities, average costs, the broker
security-number map, or the personal portfolio mapping — and **never put positions in commit
messages** (e.g. "add &lt;TICKER&gt; — &lt;N&gt; units @ &lt;price&gt;"). These live **runtime-only** and are gitignored
(`wyckoff/data/`): `holdings.json`, `secnum_map.json`. A tracked pre-commit/commit-msg hook
(`.githooks/`, enabled via `git config core.hooksPath .githooks`) blocks the obvious cases — but
the rule is on you (and any agent): **describe changes generically; keep the portfolio out of git.**
This applies to the Hermes agent (which must NOT auto-push holdings) and to Claude alike.

This repo (`github.com/roy651/hermes-skills`) is the **source of truth** for Roy's custom Hermes skills. It is checked out in two places and feeds a third:

1. **Mac local dev** — `~/Development/private/hermes-skills` (edit here)
2. **mini-PC git repo** — `roy650@192.168.1.17:~/hermes-skills` (= this repo; listed in Hermes `external_dirs`, so Hermes scans it **directly**)
3. **mini-PC runtime skills dir** — `~/.hermes/skills/` (Hermes' built-in skills dir; also scanned)

> Always edit in (1), push, then `git pull` in (2). **Never edit directly in `~/.hermes/skills/`** — that's a deployed copy; edits there get lost and cause drift.

## Two kinds of skill — they deploy differently

Hermes scans **both** (2) and (3). So whether a skill belongs in (3) depends on its kind:

### A) Pure-agent skills — git only
Only `SKILL.md` (+ `references/`); no `.venv`, scripts, service, or job. The LLM agent loads them **by name**. Example: `weather-lookup`.
- Live **only** in the git repo (2). **Do NOT copy into `~/.hermes/skills/`** — a same-named skill in both dirs makes Hermes log `Ambiguous skill name … Refusing to guess` and **skip the skill** (the job still runs off its prompt, so it fails quietly).
- **Deploy = `git pull` on the mini-PC.** Nothing else.

### B) Script / bot skills — git is source, ALSO deploy to `~/.hermes/skills/`
Have a `.venv`, scripts, `.env`, logs/data, and are executed by a **systemd service**, **system cron**, or a **Hermes bash-job** that does `cd ~/.hermes/skills/<skill>`. Examples: `personal-assistant`, `sports-alerts`, `wyckoff`, `finance-assistant`, `navman`.
- git (2) is the **source**; they must **also** be deployed into `~/.hermes/skills/<skill>/`, because that's where they execute (with their venv / `.env` / logs / data).
- **Deploy =** `git pull` → copy the changed source files into `~/.hermes/skills/<skill>/` (preserve its `.venv`, `.env`, `logs/`, `data/`) → reinstall deps if `requirements.txt` changed → `systemctl --user restart <service>` if it has one. (Hermes bash-jobs `cd` fresh each run, so they need no restart.)

**How to tell which kind:** a `.venv`/`requirements.txt`/scripts in the skill dir + something (systemd unit, `crontab -l`, or a `jobs.json` prompt) running it from `~/.hermes/skills/<skill>` ⇒ kind **B**. Just `SKILL.md`(+`references/`) ⇒ kind **A**.

## Standard workflow
1. Edit in `~/Development/private/hermes-skills/<skill>` (Mac).
2. `git commit && git push`.
3. mini-PC: `cd ~/hermes-skills && git pull --ff-only`.
4. **A** → done. **B** → copy changed files into `~/.hermes/skills/<skill>/`, reinstall deps if needed, restart its service.
5. Restart `hermes-gateway.service` only if a skill's *loaded* content (A-type SKILL.md) changed and you want the registry re-scanned.

## Known state (2026-06-21)
- `weather-lookup` deduped to git-only (kind A); stale `~/.hermes/skills/leisure/weather-lookup` moved to `~/skills-dedup-backup/`.
- The kind-B skills (`personal-assistant`, `sports-alerts`, `wyckoff`, `finance-assistant`, `navman`) correctly remain in `~/.hermes/skills/` — do **not** remove them.
- `switch-model` is a script skill (bash `switch.sh`, no venv) that sets the gateway model in `~/.hermes/config.yaml` + the Claude tier in `~/.claude/settings.json`. Its `SKILL.md` invokes `~/.hermes/skills/switch-model/switch.sh`, so **`switch.sh` must be deployed there** — deploy *only the script*, NOT a second `SKILL.md` (a duplicate SKILL.md collides with the git copy → "Ambiguous skill name"). `switch.sh claude` → local claude-proxy (provider `custom`, `http://localhost:8765/v1`); `switch.sh claude-model opus` → `claude-opus-4-8`. Deployed 2026-06-23 after a manual mis-switch to OpenRouter (no credits) hung the gateway. NB: the wyckoff scripts call the proxy directly (`localhost:8765`) and are independent of the gateway model.
- ⚠️ Pending: some kind-B source lives **only** in `~/.hermes/skills/` and is not yet in git (`sports-alerts/sport5.py`, `wyckoff/daily.py` & `data.py`, `finance-assistant/references/`). These must be committed back so git is the complete source. See `.claude/handoff-skill-sync.md`.

## Known state (2026-06-26)
- **Skill-name ambiguity fixed.** `skills.external_dirs` in `~/.hermes/config.yaml` no longer points at the repo root (which double-scanned the kind-B skills that also live in `~/.hermes/skills/` → "Ambiguous skill name"). It now lists the **8 git-only skill dirs explicitly** (`haaretz-puzzler, hermes-upstream-sync, israel-weather, memory-dreamer, reolink-renew, switch-model, telegram-bot-skill-router, weather-lookup`). The 5 kind-B skills resolve only from `~/.hermes/skills/`. **Add a new git-only (pure-agent) skill ⇒ add its dir to that list.** Backup: `config.yaml.bak-extdirs`.
- **claude-proxy moved to `_infra/claude-proxy/`** (commit `103b0f6`). It RUNS from the git checkout `~/hermes-skills/_infra/claude-proxy/` (systemd unit `claude-proxy.service`, `.venv`/`.env`/`logs` there) — so `git pull` deploys it; `systemctl --user restart claude-proxy` to apply. (The old `~/hermes-skills/claude-proxy/` path in the unit was a latent break that surfaced on reboot.)
- **mini-PC is on WiFi (`wlp1s0`) with a dead IPv6 route.** `requests` (no Happy-Eyeballs) hangs on Telegram's AAAA record, so `wyckoff/scripts/notifier.py` forces **IPv4-only** resolution. Real fix = restore ethernet / working IPv6. (Pending: BIOS "Restore on AC Power Loss → Power On".)
- **Claude degradation is now visible + mitigated.** The claude-proxy fell back to OpenRouter qwen whenever the `claude` CLI failed (expired OAuth token, or — the real culprit — `_call_claude` SIGKILLing concurrent subprocesses). Fixes: proxy **serialises** claude calls (`_claude_lock`) and returns the real backend in the **`X-Proxy-Backend`** header; wyckoff `entry.py`/`exit.py` **warm/refresh the token** before the batch and show a **⚠️ DEGRADED banner** if any call ran on a non-claude model. The OAuth token is 8h and must stay refreshed (`claude login` if it 401s).
- ⚠️ The **Hermes auto-improvement skill self-edits the *deployed* `~/.hermes/skills/` copies** (it inferred SKILL.md changes from chat on 2026-06-26 → git/runtime drift). TODO: add a guardrail so that when it edits a git-tracked file it also updates the git source.

## Known state (2026-06-29)
- **wyckoff validator now accepts JSON *or* prose.** Opus answers the exit-validator in prose; strict `json.loads` rejected it → `validate()` silently returned `None` (no `Validator:` line, no DEGRADED banner) — it only ever "worked" on qwen. Fixed in `analysis.py`: `_call_llm(raw=True)` + a tolerant `_verdict_from_text` (JSON → embedded JSON → `VERDICT:`/stance scoring). The news-lens stress-test works on Claude again.
- **wyckoff entry cohort is configurable** — `config.yaml` `entry.cohort_size` (default **20**, parameterizes the prescreener's old hardcoded `TOP_N`) caps how many prescreen survivors get the LLM read; `entry.py --cohort N` overrides on demand (agent maps "quick/light scan, ~N names" → it). Cuts per-run Claude load.
- **wyckoff analysis request timeout aligned** just above the proxy's claude-subprocess ceiling, so a slow-but-valid reply isn't dropped as a false read-timeout (claude errors exit fast → no added wait).
- **claude-proxy: qwen fallback DISABLED** — OpenRouter is out of credits (HTTP 402). A claude failure now returns a clean `503 claude_unavailable` instead of an empty paid-fallback body; Claude is the sole handler. Re-enable via the comment in `_infra/claude-proxy/proxy.py`. (The report "maxing" was fast `claude exited 1` throttling into the dead fallback, not 300s hangs.)
- **claude-proxy: `X-Proxy-Backend` reports the REAL model** (parsed from the CLI's `modelUsage`, e.g. `claude-opus-4-8`) instead of echoing the request label. Session still keyed on the request label so resume-matching is unaffected. We *are* on **Opus 4.8** (`~/.claude/settings.json`); wyckoff's `claude-opus-4-6` strings are just routing labels.
- **mini-PC is now wired-primary** — ethernet `eno1` static `192.168.1.16` (pfSense reservation), WiFi `.17` fallback, Tailscale `100.78.84.7`; BIOS "After Power Loss → On" set (auto-boots after a blip, verified).
- **reminders skill added** (git-only kind-A, in `external_dirs`) — set/snooze/list/cancel reminders via `hermes cron create/edit/remove`.

## Known state (2026-07-05)
- **claude-proxy stale-session pitfall.** After a gateway restart (e.g. `hermes upgrade`), always restart the proxy too: `systemctl --user restart claude-proxy hermes-gateway`. The proxy caches session IDs in memory; if left running for days, those IDs expire and every claude call gets `claude exited 1` (gateway reports "model provider failed after retries"). Documented in `hermes-upstream-sync/SKILL.md`.
- **Agent now allowed to push to hermes-skills.** Pre-push hostname block removed from `.githooks/pre-push` (it was redundant — the pre-commit + commit-msg hooks are the real PII guardrail). The agent can `git commit && git push` skill fixes directly. **After agent pushes, run `git pull` on the Mac before editing.** The public-repo PII rules still apply: no holdings/positions/quantities in commits or code.

## Known state (2026-07-13)
- **wyckoff migrated to run from the git checkout** (no more separate deployed copy to keep in sync). It lives+runs at `~/hermes-skills/wyckoff`; `~/.hermes/skills/wyckoff` is now a **symlink** to it. Registration is via `skills.external_dirs` (added `/home/roy650/hermes-skills/wyckoff`), which the scanner rglobs on the real path → the skill loads **once, no ambiguity** (the symlink is invisible to `rglob`, which doesn't follow symlinked dirs on py3.12). venv / `data/` (PII) / `logs/` / `config.yaml` live in the checkout (gitignored); secrets come from the global `~/.hermes/.env`. **Net effect: Hermes' self-edits to wyckoff now land in the git working tree directly — just `git commit`; no copy-to-`~/.hermes/skills` step.** The 7 wyckoff cron jobs still `cd ~/.hermes/skills/wyckoff` (resolves via the symlink) — unchanged. Backups: `~/wyckoff-premigration-bak-*`, `~/.hermes/config.yaml.bak-wyckoffmig-*`.
- This is the **pilot** for collapsing the kind-B two-tree split (which is why Hermes' improvements never reached git — `~/.hermes/skills/` isn't a git repo and its `skill_manager` treats it as source-of-truth). The other kind-B skills (`finance-assistant`, `personal-assistant`, `sports-alerts`, `navman`) still deploy the old way until migrated the same way.

## SSH
`roy650@<ip>` — **wired/primary `192.168.1.16`** (pfSense static for eno1, active after the wired lease renews/reboot), **WiFi/fallback `192.168.1.17`**, Tailscale `100.78.84.7`. Reuse one connection: `-o ControlMaster=auto -o ControlPath=~/.ssh/cm-%r@%h:%p -o ControlPersist=600` (drops to `ControlMaster=no` if the Mac's own network changes mid-session → "Broken pipe").
