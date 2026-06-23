# hermes-skills — working & deploy guide

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

## SSH
`roy650@192.168.1.17` — reuse one connection: `-o ControlMaster=auto -o ControlPath=~/.ssh/cm-%r@%h:%p -o ControlPersist=600`.
