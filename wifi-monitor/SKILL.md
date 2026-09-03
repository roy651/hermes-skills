---
name: wifi-monitor
description: Background systemd service that monitors WiFi quality on the mini-PC — dual-interface ping loop (WiFi vs wired), Telegram alerts on degradation/recovery, CSV log for analysis.
version: 1.1.0
metadata:
  hermes:
    tags: [monitoring, wifi, network, systemd, background]
---

# WiFi Monitor

A kind-B background service (no hermes job — runs as a systemd unit) that pings the gateway (`192.168.1.1`) every 5 seconds on both `wlp1s0` (WiFi → AP → pfSense) and `eno1` (wired → pfSense direct).

Comparing both interfaces gives conclusive fault attribution:
- WiFi slow, wired OK → AP/WiFi fault (scheduled reboot, interference, association issue)
- Both slow → pfSense / upstream problem, not the AP

## Network notes — same-subnet multihoming (important)

Since the mini-PC went wired-primary, **both NICs are on one subnet**: `eno1` .16 (primary,
route metric 100) and `wlp1s0` .17 (fallback, metric 600), both `192.168.1.0/24` → gateway `.1`.
Two consequences the monitor is built around:

1. **Pings bind by DEVICE (`-I <iface>`), paired with ARP hardening (#2).** `ping -I <iface>`
   uses `SO_BINDTODEVICE`, which pins *both* egress and the receive interface, so each NIC's
   path is measured symmetrically. `monitor.py` guards with `iface_ipv4` (a NIC with no address
   is link-down → real LOSS) then pings bound to the iface name. **Do NOT bind by source IP**
   — that was tried (2026-07-10) to dodge ARP flux and backfired: source-IP bind does *not*
   pin egress (the route lookup picks the lowest-metric NIC, eno1, regardless of source), so a
   `.17`-sourced "WiFi" ping leaked out eno1 and returned over wlp1s0 — an asymmetric path that
   measured ~67ms of garbage and fired **dozens of false "AP fault" alerts** while the real
   WiFi was ~5ms. Reverted to device-bind once #2 was in place.
2. **ARP hardening (host sysctl, one-time, needs root) — this is what actually fixes the flux:**
   `/etc/sysctl.d/20-wifi-monitor-arp.conf` sets `arp_ignore=1` + `arp_announce=2` (all+default)
   so each NIC only answers/announces ARP for its own IP → the gateway's reply returns on the
   NIC that sent it instead of ARP-fluxing to the other. This cured the historic "wired 76% loss
   / 0ms RTT" artifact directly (verified: device-bind eno1 = 0% loss / 0.3ms once applied), and
   is the prerequisite for device-bind in #1. `rp_filter=2` (loose) is set in
   `10-network-security.conf`. Both files live in `/etc` (not git); re-create after a reinstall.

## Triaging an alert burst (real vs artifact vs AP) — see `references/fault-triage.md`

Before recommending any fix when "a few dozen drops" arrive, work three questions in order:
1. **Artifact of a recent monitor change?** Correlate the DEGRADED-per-hour histogram against
   `git log` + service restart time. A burst that lines up with an experiment-then-revert window
   (e.g. the 2026-07-10 source-IP-bind, 164 false faults 09–11 UTC) is the code, not the network.
2. **Real spike — radio or network?** Through a *real* WiFi spike, `modem_ms` (~0.7 ms) and
   `wan_ms` (~10 ms) stay solid → pfSense/modem/ISP cleared, it's the radio hop. A low median
   (~4 ms) with hour-clustered bursts = intermittent problem, **not** power-save (BPS raises the
   floor uniformly — don't chase `power_scheme` on a 4 ms median).
3. **Card or AP?** A second independent client (Roy's phone) failing at the *same wall-clock
   moment* localizes it to the AP and exonerates the 8265 card — skip the power-save test.
   Then the 2.4-vs-5-GHz phone test picks the AP fix. Full AP remediation list is in the
   reference file.

## What It Does

- Pings `192.168.1.1` every 5s on both interfaces simultaneously
- After 2 consecutive bad WiFi samples (RTT > 150ms or packet loss) → sends Telegram alert with:
  - WiFi vs wired RTT at event time
  - `iw dev wlp1s0 station dump` (signal level, bitrate, BSSID, connected time)
  - Last 5 kernel log lines mentioning `wlp1s0`
- On recovery → sends Telegram with event duration and peak RTT
- Appends every sample to `logs/wifi_monitor.csv` for offline analysis
- Logs events to `logs/wifi_events.log`

## Root-cause attribution — read the daily report correctly

The four probes are **nested path segments**, so one fault lights up several rows and the raw
per-link totals double-count it:

```
WiFi   ping -I wlp1s0 → 192.168.1.1    you → AP → pfSense
Wired  ping -I eno1   → 192.168.1.1    you → cable → pfSense
Modem  ping           → 192.168.3.1    ...→ pfSense → modem      (unbound: uses the default route)
WAN    ping           → 8.8.8.8        ...→ modem → ISP
```

The digest therefore assigns each failing sample to **exactly one** bucket, testing most-upstream
first (an upstream fault darkens everything below it, so it must be ruled out before blaming the
radio). Buckets carry seconds **and episode counts** — 10s in `1×` is one outage, in `2×` it is two.

| Test order | Condition | Bucket |
|---|---|---|
| 0 | modem ✗ **and** alt ✓ | 🏠 pfSense — **proven** |
| 1 | wifi ✗ **and** wired ✗ **and** modem ✗ | 🏠 pfSense / host (inferred) |
| 2 | modem ✗ | 🔌 Modem link |
| 3 | wan ✗ (modem ✓) | 🌐 Provider |
| 4 | wifi ✗, wired ✓ | 📡 WiFi hop |
| 5 | wired ✗, wifi ✓ | 🧵 Wired hop |

**The bypass probe (`alt`)** is a fifth path: a USB dongle joined to the *modem's own SSID*
(`sandy-wanda-backup`), pinging the same `192.168.3.1` as the Modem row but **without crossing
pfSense**. That converts rule 1 from an inference into a measurement:

- modem dark via pfSense but alive on the bypass → **pfSense is the fault, proven**
- *everything* dark including a probe on a separate radio **and** separate subnet → **this host**
- LAN fine, modem dark on **both** paths → the modem itself

It holds **no default route and no DNS** (`/etc/netplan/60-wifi-bypass.yaml`, `use-routes: false`),
so it can never carry real traffic or hijack the default path — it exists only to be pinged *from*.
Remove the dongle and `ALT_IFACE` resolves empty, the probe disables itself, and attribution falls
back to the inference. CSV rows before 2026-08-06 have 5 columns; a missing 6th is read as *not
measured*, never as a loss.

**⚠️ Retired 2026-09-03.** The dongle had sat `NO-CARRIER` (unassociated — the known USB-current
problem) for weeks, and a bug in `append_csv` wrote `LOSS` on every sample while it did, because it
keyed on "does a `wlx*` interface exist" instead of "was the bypass actually pinged". Fixed: a
down dongle now writes *not measured*. `60-wifi-bypass.yaml` is moved to `/etc/netplan/disabled/`
(netplan does not recurse); the dongle stays plugged in, unconfigured and inert. **What is lost without it:** only the two
bypass-informed verdicts — `proven_pfsense_runs` can no longer *prove* pfSense (it stays an
inference bucket), and a WAN-down alert can no longer split "pfSense WAN side" from "modem/ISP"
or "pfSense dead" from "this host dead". The WiFi / Wired / Modem / WAN rows never depended on it.
To bring it back: a ≥0.9 A USB port, restore the yaml, `sudo netplan apply` — nothing else.

**A provider outage does not cascade downward here** — WiFi/Wired/Modem probes all terminate at or
before the modem, so only the WAN row can see the ISP. A *modem* failure is usually the local link
or the box itself, not the provider; the true provider signature is WAN failing while the modem
still answers.

Coverage self-labels (≥98% normal drift · 90–98% elevated · <90% degraded): the loop sleeps 5s
*after* doing its work, so a healthy day drifts ~1%, and a timed-out ping costs up to 2s — which
makes low coverage a symptom rather than bookkeeping.

## Hermes Tool: Root-Cause Report (on demand)

`root_cause_report.py` charts the same buckets over time — one line per bucket, one chart for
seconds lost and one for episodes — as a **self-contained HTML file** (inline SVG, no libraries).
Runs monthly on cron; use these for anything on demand:

> **ALWAYS pass `--send`.** The charts ARE the deliverable. Without it the script only writes an HTML
> file to disk that the user never sees, and a text table in chat is **not** the report — quoting the
> file path is not delivery. Omit `--send` only when reading numbers to answer a narrow question the
> user asked, where no report was requested.

**Use `.venv/bin/python`, not `python3`** — the chart image needs matplotlib, which lives in the
skill venv. (Under bare `python3` it still works but silently degrades to HTML-only.)

```bash
cd ~/.hermes/skills/wifi-monitor

.venv/bin/python scripts/root_cause_report.py --days 7  --send                 # last week
.venv/bin/python scripts/root_cause_report.py --days 30 --send                 # rolling month
.venv/bin/python scripts/root_cause_report.py --month 2026-07 --send           # a calendar month
.venv/bin/python scripts/root_cause_report.py --since 2026-07-06 --until 2026-07-10 --send
```

`--send` posts **two** things: the charts as a **PNG** (`sendPhoto`, renders inline — this is what
the user actually looks at) and the HTML as a document (hover + table, for detail). A path on disk
is not a deliverable; the user cannot open it from Telegram. Add a short written summary alongside —
dominant bucket, notable episodes, what changed versus the previous period.

Examples → what to run:
- "network report for last week" / "דוח רשת לשבוע האחרון" → `--days 7 --send`
- "what broke in July?" → `--month 2026-07 --send`
- "why was the internet bad on the 7th?" → `--since 2026-07-06 --until 2026-07-08 --send`, then read
  the table and name the dominant bucket

## Deploy

This is a **kind-B** skill — git is the source, but the service runs from `~/.hermes/skills/wifi-monitor/`.

```bash
# On the mini-PC, after git pull:
mkdir -p ~/.hermes/skills/wifi-monitor/scripts ~/.hermes/skills/wifi-monitor/logs
cp ~/hermes-skills/wifi-monitor/scripts/monitor.py ~/.hermes/skills/wifi-monitor/scripts/

# Copy systemd unit and enable
cp ~/hermes-skills/wifi-monitor/wifi-monitor.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now wifi-monitor.service
```

### .env

Create `~/.hermes/skills/wifi-monitor/.env`:
```
TELEGRAM_BOT_TOKEN=<your-bot-token>
TELEGRAM_CHAT_ID=<your-chat-id>
```

### Config (optional overrides in .env)

| Variable      | Default       | Notes                                  |
|---------------|---------------|----------------------------------------|
| `WIFI_IFACE`  | `wlp1s0`      | WiFi interface (through the house AP)  |
| `WIRED_IFACE` | `eno1`        | Wired interface (direct to pfSense)    |
| `TARGET`      | `192.168.1.1` | Gateway to ping                        |
| `INTERVAL`    | `5`           | Seconds between samples                |
| `BAD_MS`      | `150`         | RTT threshold for a "bad" sample       |
| `BAD_CONFIRM` | `2`           | Consecutive bad samples before alert   |
| `MAINTENANCE_WINDOWS` | `01:05-01:20,13:05-13:20` | **UTC** windows re-labelled 🔧 Scheduled |
| `ALT_IFACE`   | *(first `wlx*`)* | Bypass-probe interface; empty = probe disabled |
| `ALT_TARGET`  | = `MODEM_TARGET` | What the bypass pings (the modem, reached directly) |

### The dongle (UGREEN AX900 / CM762, AIC8800D80)

Hard-won, so it is written down. **The label says `5.0V ⎓ 0.9A Max` — 900 mA. A USB 2.0 port supplies
only 500 mA.** On an underpowered port the adapter enumerates perfectly (descriptors are low-draw)
and then stalls on the first vendor command with `cmd timed-out` / `rd fail: -32` / `chip_id=0`,
identically across replugs, reboots and two different driver families. **It only works in a
high-current port.** Data rate is a red herring: it is `bcdUSB 2.00`, so USB 3.0 buys no speed —
only current.

Driver: `shenmintao/aic8800d80` branch **`legacy-mcu1`**, installed via DKMS (rebuilds across kernel
updates — survived 6.8.0-124 → 6.8.0-137). Requires **Secure Boot disabled**; an unsigned
out-of-tree module will not load otherwise. On success the device re-enumerates `a69c:8d80` →
`a69c:8d81` and a `wlx*` interface appears.

**Lesson worth generalising: on a USB peripheral, check the current rating before the data rate.**

⚠️ `MAINTENANCE_WINDOWS` is **UTC**, but the household thinks in local time (Asia/Jerusalem = UTC+3
in summer). Subtract 3 hours when adding one — a 16:11 *local* event is `13:05-13:20`, and entering
`16:10-16:15` would silently suppress 19:10 local instead.

**It is empty by default since the 2026-09-03 AP swap** (Tenda `sandy_wanda_6` → Asus
`sandy_wanda_7`). The two former defaults described the Tenda's own habits and are kept here only
as a worked example of the format:

- `01:05-01:20` UTC = **04:05–04:20 local** — the Tenda's nightly restart (~60–90s, one episode)
- `13:05-13:20` UTC = **16:05–16:20 local** — the mesh steered the client to another node exactly
  12h later (~11–15s; a roam, not a restart — the journal showed a BSSID change and an AP-initiated
  disassociation with reason 1)

Re-populate only from a *measured* recurring Asus event. A guessed window silently re-labels real
outages as "Scheduled".

Loss inside a window is **re-labelled, never dropped**, and the daily verdict line reports unplanned
loss separately (`15s unplanned · +85s scheduled`). So a restart that grows from 80s to 200s — as
2026-08-03 did — is still plainly visible.

## Systemd Unit

See `wifi-monitor.service` in this directory.

## Logs

All logs written to `logs/` in the deployed skill directory:
- `wifi_monitor.csv` — all samples (`timestamp_utc, wifi_ms, wired_ms`)
- `wifi_events.log` — event start/end lines
- `monitor.log` — process log

## Update

```bash
git pull && \
cp ~/hermes-skills/wifi-monitor/scripts/monitor.py ~/.hermes/skills/wifi-monitor/scripts/ && \
systemctl --user restart wifi-monitor
```
