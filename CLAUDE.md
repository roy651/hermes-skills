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

## Known state (2026-07-19)
- **All 5 kind-B skills now run from the git checkout** — the wyckoff pilot was extended to `sports-alerts`, `navman`, `personal-assistant`, `finance-assistant`. Each: `~/.hermes/skills/<skill>` is a **symlink** → `~/hermes-skills/<skill>`, registered via `external_dirs` (now 14 = 9 kind-A + these 5). `rglob` doesn't follow the symlink → no ambiguity; the symlink keeps every `cd ~/.hermes/skills/<skill>` (Hermes cron jobs, SKILL.md commands, systemd `ExecStart`/`WorkingDirectory`) resolving. venv/`data`/`logs`/`.env`/`config` are gitignored in the checkout. **Net: Hermes' self-edits land in git for all of them.** Backups: `~/<skill>-premigration-bak-*`, `~/.hermes/config.yaml.bak-*mig-*`.
- Per-skill: **navman** already ran from the checkout (service pointed there) → only registration fixed, bot never restarted (same PID). **personal-assistant** = stateful bot (SQLAlchemy `data/pa.db`) → migrated stop→snapshot→symlink→start, watchdog paused during swap. **sports-alerts** = watchdog timer + weekly cron (no restart). **finance-assistant** = agent-invoked (no service/cron); **moneyman** vendored as a **git submodule** pinned `v2026.04.06.1` — source only; its 375M `node_modules`/`dst` are gitignored in moneyman and copied in as runtime, and the scanner prunes `node_modules` via `agent/skill_utils.py:EXCLUDED_SKILL_DIRS` so there's no SKILL.md pollution. fa's SKILL.md has no `name:` → registers by dir name. On a fresh Mac clone run `git submodule update --init`.
- **Migration survived** ~6 days + heavy Hermes activity (symlinks/external_dirs/single-registration all held; Hermes pushed 10 wyckoff commits straight to git — the drift fix working in the wild).
- **To migrate a new kind-B skill:** rebuild its venv in the checkout, copy runtime (data/logs/.env/config — gitignored), `mv ~/.hermes/skills/<skill> ~/<skill>-bak && ln -s ~/hermes-skills/<skill> ~/.hermes/skills/<skill>`, add the checkout dir to `external_dirs`, restart its service (if any) + gateway. Verify registration by **walking with `EXCLUDED_SKILL_DIRS` + dir-name fallback** (naive `rglob` misreports: it follows into `node_modules` and misses no-frontmatter skills).

## Known state (2026-08-05)
- **New kind-A skill `portfolio-brainstorm`** (git-only; `external_dirs` now **15**). Manually triggered deep portfolio review — reads the latest digests, prior review records and conversation history, then argues asset-by-asset. Its judgement lives in the new **`wyckoff/docs/portfolio-review-method.md`** (tracked, deliberately PII-free); per-session records go to **`wyckoff/data/reviews/YYYY-MM-DD.md`** (gitignored — they hold positions). Do **not** copy this skill into `~/.hermes/skills/`.
- **Digests are now archived.** `wyckoff/scripts/notifier.py:send()` writes every outbound message to `wyckoff/data/reports/` (gitignored) — previously a digest existed *only* inside Telegram, so nothing could read it back. `--dry-run` never reaches the notifier, so dry-runs are not archived. New `wyckoff/scripts/review_context.py` gathers the whole evidence chain (digests → prior reviews → transcripts → job health → holdings) with no LLM.
- **Three live bugs fixed.** (1) `entry.py` **crashed on the 2026-08-01 weekly run and sent nothing** — the model returned `recommendation` as a dict and `rec in ENTRY_RECS` raised `unhashable type: dict`, killing all 26 analyses; now flattened in `analysis.analyze()`. (2) **claude-proxy mislabelled the backend model**: it picked by `inputTokens`, which *excludes* cache reads, so on a warm cache the real model reported `inputTokens=2 / cacheRead=32761` and lost to a tiny haiku housekeeping call — analyses were never degraded, but the ⚠️ DEGRADED banner was blind. Now sums input + cacheRead + cacheCreation. (3) `openpyxl` was missing from `wyckoff/requirements.txt`, so `import_holdings.py` died on a rebuilt venv.
- **Claude tier is now `claude-opus-5`** (`~/.claude/settings.json`; backup `settings.json.bak-opus48`), and `switch-model/switch.sh` maps the `opus` tier to it so it stops reverting to 4-8.
- **`wifi-monitor` is still on the OLD two-tree model** — `~/.hermes/skills/wifi-monitor` is a **real directory, not a symlink**, and both systemd units run from there. Edit in git, then copy `scripts/monitor.py` into `~/.hermes/skills/wifi-monitor/scripts/`. Candidate for the symlink migration. Its daily report now shows **total time lost per link** (`lost_samples × INTERVAL`), which surfaces losses that the 0.0%-rounded percentage hides.
- ⚠️ A **pre-existing `git stash` entry** sits in the mini-PC checkout (`WIP on main: 033b705 wyckoff: fix SLARL.TA ticker, restore holdings and watchlist`) from an earlier session. Left untouched — inspect before dropping.

## Known state (2026-08-06)
- **wifi-monitor gained root-cause attribution.** Each failing sample is assigned to exactly ONE bucket (most-upstream rule wins) with **episode counts**, so "10s in 1×" (one outage) is distinguishable from "10s in 2×". Known recurring events are **re-labelled 🔧 Scheduled, never dropped**, via `MAINTENANCE_WINDOWS` — ⚠️ **UTC**, while the house thinks in local time (UTC+3): `01:05-01:20` = the 04:10 local Tenda restart, `13:05-13:20` = the 16:11 local **mesh roam** (the AP steers the client to another node exactly 12h after its restart — confirmed by a BSSID change + AP-initiated disassoc in the journal). Effect on a sample day: WiFi-hop fell 85s → 10s once the known events were separated out.
- **A fifth "bypass" probe proves pfSense faults.** A UGREEN AX900 (CM762 / AIC8800D80) USB dongle joined to the *modem's* SSID pings `192.168.3.1` **without crossing pfSense**, so `modem ✗ + alt ✓` = pfSense at fault by measurement, not inference; all-dark-including-alt = this host. It deliberately holds **no default route/DNS** (`/etc/netplan/60-wifi-bypass.yaml`, `use-routes: false`) so it can never carry real traffic. CSV gained a 6th column on 2026-08-06; rows before that are read as *not measured*, never as loss, so history stays usable.
- **⚠️ The dongle needs a HIGH-CURRENT USB port** — label says `0.9A max`, USB 2.0 supplies 0.5A. Underpowered, it enumerates fine then stalls on the first vendor command (`cmd timed-out`, `rd fail: -32`, `chip_id=0`) identically across replugs, reboots and two driver families. Cost hours. **On USB peripherals check the current rating before the data rate** — `bcdUSB 2.00` made me wrongly rule out moving ports. Driver: `shenmintao/aic8800d80` branch `legacy-mcu1` via DKMS; needs **Secure Boot disabled**.
- **Monthly root-cause report** (`scripts/root_cause_report.py`, cron `07ee1009ba5a`, 1st 05:00 UTC) charts each bucket over time — duration + episodes — as an **inline PNG plus interactive HTML**. Must run under `.venv/bin/python` (matplotlib lives there; system python silently degrades to HTML-only) and **always with `--send`**, since a path on disk is not a deliverable. Windows: `--days N`, `--month YYYY-MM`, `--since/--until`.
- **wifi-monitor now has a venv** (`requirements.txt`: requests, matplotlib) built without sudo. The monitoring daemon still runs on system `python3` and needs no new dependency.
- Mac→mini-PC workflow correction: **commit and push FIRST, then `git pull` on the mini-PC.** Scp-ing ahead of committing left the checkout dirty three times and Hermes correctly flagged it.
- Note: `.skills_prompt_snapshot.json` builds its invalidation manifest from `~/.hermes/skills/` only, so edits to **external-dir** skills never invalidate it. It caches names/descriptions only (not SKILL.md bodies), so it rarely bites — but delete it to force a rebuild if a new external skill seems invisible.

## SSH
`roy650@<ip>` — **wired/primary `192.168.1.16`** (pfSense static for eno1, active after the wired lease renews/reboot), **WiFi/fallback `192.168.1.17`**, Tailscale `100.78.84.7`. Reuse one connection: `-o ControlMaster=auto -o ControlPath=~/.ssh/cm-%r@%h:%p -o ControlPersist=600` (drops to `ControlMaster=no` if the Mac's own network changes mid-session → "Broken pipe").

## Known state (2026-08-21) — security hardening

Verified a Hermes security audit and hardened the mini-PC. Six of its seven findings held; one was
materially wrong, two real issues were missed. **New skill `minipc-audit`** re-checks all of this
monthly (kind-A-style script skill, cron `minipc_audit_monthly`, 1st at 06:00 UTC).

- **SSH is key-only.** `/etc/ssh/sshd_config.d/01-hardening.conf`. The `01-` prefix is load-bearing:
  sshd takes the **first** value it reads, `Include` is at line 12, and `50-cloud-init.conf` sets
  `PasswordAuthentication yes`. Grepping `sshd_config` gives the wrong answer — always use `sshd -T`.
- **ufw**: default deny; 22 scoped to the LAN + `tailscale0`. ⚠️ **The `tailscale0` rule is
  decorative** — see below.
- **Tailscale bypasses ufw entirely.** `-A INPUT -j ts-input` precedes every ufw chain and
  `ts-input` ends `-i tailscale0 -j ACCEPT`. ufw does **not** filter tailnet traffic. The controls
  that actually work are narrow **socket binds** and the **Tailscale ACL** (now port 22 only,
  verified from the enforced packet filter, not the console). Tailnet Lock deliberately **off**:
  Android cannot sign, so this host would be the sole signer.
- **Only sshd binds a wildcard.** Gateway webhook → `127.0.0.1` (`platforms.webhook.extra.host` in
  `~/.hermes/config.yaml`); daily-summary dashboard and actual-budget → the LAN address.
- **`docker-modem-guard.service`** re-inserts a `DOCKER-USER` DROP for the modem-side interface on
  every docker start (dockerd flushes its chains on restart). Needed because Docker's DNAT sits in
  `nat/PREROUTING`, ahead of every ufw chain — **ufw cannot police published container ports**.
- **`actual-budget.service`** waits for the LAN address, then starts the container (policy `no`).
  Binding to a specific IP creates a DHCP-timing dependency: docker starts ~3s early, and a failed
  bind leaves the container `Up` with **no port mapping**. `network-online.target` is useless here —
  `systemd-networkd-wait-online` is *skipped* via `ConditionPathIsSymbolicLink`.
- **Root-equivalence closed.** `roy650` removed from the `docker` group; blanket
  `NOPASSWD: /bin/systemctl, /usr/bin/docker` replaced by `/etc/sudoers.d/roy650-services` (scoped
  unit commands + `/usr/local/sbin/minipc-audit-root`). Both were root: `systemctl link` an arbitrary
  unit, or `docker run -v /:/host --privileged`. Password sudo retained via the `sudo` group.
  ⚠️ A `NOPASSWD` rule must never point into the git checkout — it is user-writable, i.e. a root shell.
- **Ubuntu Pro attached** (free personal): `esm-apps`, `esm-infra`, `livepatch` enabled.
- **WAN is clean** — zero open ports, confirmed 2026-08-21 from a phone hotspot. Cannot be checked
  from inside (NAT hairpin off ⇒ everything looks closed). Re-run quarterly. ⚠️ Mobile carriers
  intercept **port 53**, so a TCP connect reports it open for *any* address — control against an
  RFC 5737 TEST-NET address before believing it.
- ⚠️ Still open: modem Wi-Fi key rotation and **bridge mode** (the modem is upstream of pfSense and
  broadcasts its own SSID — the only route onto the untrusted segment).
- **Lesson:** a single vantage point is not a general property. "Firewalled from the LAN" is not
  "firewalled" (8644 was open via tailnet); "open from a carrier" is not "open" (port 53).

## Known state (2026-09-03) — house AP swapped: Tenda → Asus mesh

The household AP was replaced. **SSID `sandy_wanda_6` → `sandy_wanda_7`**, new password.

- **Transitional: `sandy_wanda_6` lives on as a *secondary* SSID on the Asus** (old password) so
  legacy IoT — robot-vac, Nest, Reolink cam — kept working without re-pairing. Verified from the
  mini-PC that it is **bridged onto the main `192.168.1.0/24`** (pfSense DHCP, LAN-reachable), not
  an isolated guest segment. Plan: migrate those devices to `_7` one by one, then delete the
  secondary SSID. The second mesh node is `192.168.1.222` (`e8:9c:25:9f:1a:60`); the primary is
  `.172`.

- **The mini-PC's only credential store is `/etc/netplan/50-cloud-init.yaml`** (`wifis: wlp1s0:`),
  applied with `sudo netplan apply`. NetworkManager is **inactive** (no nmcli profiles) and
  `/etc/wpa_supplicant/` holds only scripts — so there is exactly one place to edit.
  `/run/netplan/wpa-wlp1s0.conf` is **generated** from it on every apply; never edit that.
- Result on `wlp1s0`: BSSID `e8:9c:25:68:ed:e4`, **5 GHz ch 60 @ 80 MHz VHT**, -55 dBm,
  866 Mbit/s — up from the Tenda's 2.4 GHz ch 6 @ 40 MHz, -61 dBm, 240 Mbit/s.
- ⚠️ **The Asus shipped in router mode, not AP/bridge mode** — `wlp1s0` first came up on
  `192.168.50.34/24` via `192.168.50.1` (Asus factory LAN), i.e. **double-NAT behind pfSense**.
  Decision: put the Asus in **AP mode** so pfSense stays the sole gateway/DHCP. Symptom to watch
  for after any AP reset: a WiFi address outside `192.168.1.0/24`.
- **A DHCP reservation keys on the CLIENT's MAC**, here `wlp1s0` = `00:e1:8c:50:02:7b`. An AP in
  bridge mode forwards the client's own DHCP frames unchanged, so the AP's MAC never appears in the
  mapping — swapping "Tenda's MAC" for "Asus's MAC" in pfSense is not a thing. (In router mode the
  reservation is dead instead, because pfSense never sees the client MAC at all.)
- **`MAINTENANCE_WINDOWS` now defaults to empty.** The old `01:05-01:20,13:05-13:20` UTC pair
  described the *Tenda's* nightly restart and its 12h-later band-steer roam. Re-populate only from
  a **measured** Asus event — a guessed window silently re-labels real outages as 🔧 Scheduled.
- Code is now **vendor-neutral** ("AP", not "Tenda"), so the next swap needs no code edit; the
  hardware is named in the docs only. `references/fault-triage.md` keeps the Tenda ROI list, marked
  as historical: items 2/4/5 are generic radio levers, 1/3/6 were Tenda-firmware behaviour, and
  item 7 ("replace the Tenda") is what actually happened.
- **Bypass dongle retired.** `wlx6c1ff78c875a` had sat `NO-CARRIER` for weeks (the known
  USB-current problem) and `append_csv` wrote `LOSS` on every sample while it did — it keyed on
  "a `wlx*` interface exists", not "the bypass was pinged". Fixed (`alt_measured`); `60-wifi-bypass.yaml` →
  `/etc/netplan/disabled/`, dongle left plugged in but unconfigured (inert, `NO-CARRIER`). Cost: pfSense faults are inferred,
  not proven, and a WAN-down alert can't split pfSense-WAN from modem/ISP. Unrelated to the AP
  swap — the dongle targets the *modem's* SSID, which did not change.
- **`systemctl` scope trap:** `wifi-monitor.service` is a **`--user`** unit. Plain
  `systemctl status wifi-monitor` reports "could not be found" / "inactive" and looks like an
  outage. Always `systemctl --user`.

## Known state (2026-09-04)
- **Second Claude logout outage: 2026-08-28 03:00 → 09-03 09:50 UTC (6 days).** Same mechanism as
  2026-07-27: a failed OAuth refresh wipes the refresh token, `claude` exits 1 with **empty stderr**,
  every LLM-backed cron job 503s, no-agent jobs keep running. Recovered only by an interactive
  `/login`. **The proxy now names it**: on a non-zero exit it runs `claude auth status` and returns
  "Claude CLI is LOGGED OUT on the mini-PC … Fix: ssh -t … 'claude login'"; otherwise it includes
  the CLI's stdout (the CLI prints its reason there, not on stderr). Roy declined a periodic
  watchdog cron — the clearer Telegram failure text is the chosen control.
- **Claude tier is now `claude-fable-5-1`** (`~/.claude/settings.json`; backup
  `settings.json.bak-opus5`). `switch-model/switch.sh claude-model fable` maps it; the script copy
  in `~/.hermes/skills/switch-model/` must be refreshed on change. Verified via `X-Proxy-Backend`.
- **Re-running a spent one-shot:** `hermes cron run <id>` refuses a completed job until
  `hermes cron resume <id>`; it then runs synchronously in the CLI. If the agent removes its own
  job mid-run (reolink-renew does, by design) Hermes logs "fire claim ownership lost" and drops the
  reply — the work is done, only the Telegram summary is lost. Recurring jobs re-run and deliver
  normally with `hermes cron run`.
- **`minipc_audit.sh` exits 1 when any check fails** (e.g. pending security updates), so Hermes
  marks the run "failed" although the report ran. Script design, not an outage.
- **Daily Brief rebuilt (first draft sent 2026-09-04, under review for a few days).** Roy found
  it repetitive. `wyckoff/scripts/digest.py --daily` now adds a mechanical **market snapshot**
  (`scripts/market.py`: SPY/QQQ/IWM/TLT/HYG/GLD/DXY/VIX 1d/5d/20d + sector ETFs ranked by 5d),
  an **Engines** block parsed from the archive (exit review, entry funnel, MLM scan — one line
  each, stale-flagged), and a **delta gate**: the concentration table prints only when a weight
  moves ≥1 point or on Mondays (`data/brief_state.json`), and validated flags older than 7 days
  collapse to one carried line (`signals.build_section(fresh_days=)`). The read now **web-searches**
  inside the Claude Code call (market wrap, events/news on held names and sectors, one analyst
  piece from `brief.analysts` in `config.yaml`) — parts Market / Your book / Check, ≤1,800 chars.
  To fit the searches the **claude-proxy ceiling is 480s** (was 300; `analysis.py` waits 490).
  Dry run: 93s, 4.4k chars. `--dry-run` does not touch the state file.
