---
name: wifi-monitor
description: Background systemd service that monitors WiFi quality on the mini-PC — dual-interface ping loop (WiFi vs wired), Telegram alerts on degradation/recovery, CSV log for analysis.
version: 1.1.0
metadata:
  hermes:
    tags: [monitoring, wifi, network, systemd, background]
---

# WiFi Monitor

A kind-B background service (no hermes job — runs as a systemd unit) that pings the gateway (`192.168.1.1`) every 5 seconds on both `wlp1s0` (WiFi → Tenda → pfSense) and `eno1` (wired → pfSense direct).

Comparing both interfaces gives conclusive fault attribution:
- WiFi slow, wired OK → Tenda/WiFi fault (scheduled reboot, interference, association issue)
- Both slow → pfSense / upstream problem, not Tenda

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
   measured ~67ms of garbage and fired **dozens of false "Tenda fault" alerts** while the real
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
   Then the 2.4-vs-5-GHz phone test picks the Tenda fix. Full Tenda remediation list is in the
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
| `WIFI_IFACE`  | `wlp1s0`      | WiFi interface (through Tenda AP)      |
| `WIRED_IFACE` | `eno1`        | Wired interface (direct to pfSense)    |
| `TARGET`      | `192.168.1.1` | Gateway to ping                        |
| `INTERVAL`    | `5`           | Seconds between samples                |
| `BAD_MS`      | `150`         | RTT threshold for a "bad" sample       |
| `BAD_CONFIRM` | `2`           | Consecutive bad samples before alert   |

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
