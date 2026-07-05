---
name: wifi-monitor
description: Background systemd service that monitors WiFi quality on the mini-PC — dual-interface ping loop (WiFi vs wired), Telegram alerts on degradation/recovery, CSV log for analysis.
version: 1.0.0
metadata:
  hermes:
    tags: [monitoring, wifi, network, systemd, background]
---

# WiFi Monitor

A kind-B background service (no hermes job — runs as a systemd unit) that pings the gateway (`192.168.1.1`) every 5 seconds on both `wlp1s0` (WiFi → Tenda → pfSense) and `eno1` (wired → pfSense direct).

Comparing both interfaces gives conclusive fault attribution:
- WiFi slow, wired OK → Tenda/WiFi fault (scheduled reboot, interference, association issue)
- Both slow → pfSense / upstream problem, not Tenda

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
