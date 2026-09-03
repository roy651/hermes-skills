# WiFi-monitor fault triage — real spike vs measurement artifact vs AP fault

When an alert burst arrives ("a few dozen WiFi drops"), do NOT assume real link loss.
Work these three questions in order before recommending any fix.

## 1. Is it an artifact of a recent monitor change? (check FIRST)

A config experiment in `monitor.py` (bind mode, thresholds, iface logic) can fire dozens of
**false** faults. Correlate the DEGRADED-per-hour histogram against git + service restart times:

```bash
# hourly DEGRADED rate today
grep -E '2026-07-..\ .*DEGRADED' logs/wifi_events.log \
  | grep -oE '2026-07-.. [0-9]{2}' | sort | uniq -c
# when was the code last changed / service restarted?
git -C ~/hermes-skills log --format='%h %ci %s' -6 -- wifi-monitor/
systemctl --user show wifi-monitor.service -p ExecMainStartTimestamp
```

If a burst window lines up exactly with an experiment-then-revert window, those faults are the
experiment, not the network. Real example (2026-07-10): the source-IP-bind change was live
09:02–11:33 UTC and produced a 164-event burst (40–45 % of pings >100 ms); the moment it was
reverted + restarted (11:32), the afternoon went back to median 4 ms / <1 % spikes. Those 164
were artifacts. See SKILL.md "same-subnet multihoming" for why source-IP bind mismeasures.

## 2. Is a real spike the radio, or the network behind it?

**Data location:** the running service writes to the DEPLOYED copy, not the git checkout —
`~/.hermes/skills/wifi-monitor/logs/wifi_monitor.csv` (header `timestamp_utc,wifi_ms,wired_ms,modem_ms,wan_ms`).
The git-checkout `logs/monitor.log` is empty. `cd ~/.hermes/skills/wifi-monitor/logs` before running the
histograms below.

The monitor pings four targets per cycle: `wifi_ms` (wlp1s0→AP), `wired_ms` (eno1),
`modem_ms` (wired→modem ~0.7 ms), `wan_ms` (internet ~10 ms). Through a **real WiFi** spike the
wired/modem/WAN columns stay rock-solid — so pfSense, the modem and the ISP are cleared and it's
purely the mini-PC ↔ AP radio hop. Confirm the shape from the CSV:

```bash
python3 - <<'EOF'
import csv, statistics
from collections import defaultdict
today='2026-07-10'
byhr=defaultdict(list)
with open('wifi_monitor.csv') as f:
    for x in csv.DictReader(f):
        if x['timestamp_utc'].startswith(today):  # NB: run from the deployed logs dir
            byhr[x['timestamp_utc'][11:13]].append(float(x['wifi_ms']))
print("hour  n   medWiFi  p90   >100ms%")
for h in sorted(byhr):
    v=sorted(byhr[h])
    print("%s %4d %7.0f %6.0f %6.0f"%(h,len(v),statistics.median(v),
        v[int(len(v)*.9)],100*sum(1 for y in v if y>=100)/len(v)))
EOF
```

Tells: **low median (~4 ms) with episodic, hour-clustered bursts = a real intermittent problem,
NOT power-save.** Power-save (iwlmvm `power_scheme=2`/BPS) raises the *floor* uniformly — a 4 ms
median rules it out as the main cause, so don't waste the sudo change chasing it. A perfect
overnight baseline (00–08 at 0 % spikes) that bursts only at active hours points at
airtime/AP behaviour, not a constant radio defect.

## 3. Local card or the AP? — the free discriminator

**A second, independent client failing at the same wall-clock moment localizes the fault to the
AP.** If Roy's phone (different device, different radio) also drops streaming during the burst,
that exonerates the mini-PC's iwlwifi 8265 card better than any local test would — you can skip
disabling power-save entirely. If only the mini-PC is affected, suspect the card/driver.

Cheap 2-minute test that picks the fix: during the next burst window, connect the phone to the
**2.4 GHz and 5 GHz SSIDs separately**. Both stall → AP CPU/uplink/firmware. Only 5 GHz →
5 GHz airtime/channel-width. (The USB-dongle A/B is redundant once a second client corroborates
— it can't tell you more than the phone already did if the wired link is clean.)

## AP remediation (when it's the AP), ordered by ROI

The AP is an **Asus mesh, SSID `sandy_wanda_7`**, in AP/bridge mode (pfSense stays the sole
gateway and DHCP server). As seen from the mini-PC on 2026-09-03: 5 GHz ch 60 @ 80 MHz VHT,
BSSID `e8:9c:25:68:ed:e4`, -55 dBm, 866 Mbit/s. Its mgmt IP is NOT its BSSID and won't show in
ARP until you talk to it at L3 (arp-scan to find it). Since the mini-PC is wired-primary
(eno1 metric 100; WiFi is the metric-600 fallback the monitor probes), the AP's real job is
household WiFi, not the mini-PC's traffic.

⚠️ **The ROI list below was written against the previous AP** — a Tenda AC on `sandy_wanda_6`
(2.4 ch6 + 5 ch40) — and item 7 is the one that was eventually taken: it was replaced by the Asus
on 2026-09-03. Items 2, 4 and 5 are generic radio levers and still apply; items 1, 3 and 6 describe
Tenda firmware behaviour and should not be assumed of the Asus until measured.

1. **Firmware update.** Tenda AC firmware leaks/chokes under sustained evening load — classic
   "fine at noon, stalls at 21:00". First thing to check.
2. **5 GHz width 80 → 40 MHz.** 80 MHz on ch40 spans 5170–5250; any interferer anywhere in that
   width clobbers the whole link. 40 MHz halves exposure, plenty for streaming. Top lever if it's
   5 GHz-only.
3. **Disable "smart" features** — band-steering, auto-optimize/"AI", airtime scheduling, WPS,
   guest net. On Tendas these cause re-assoc storms and periodic background rescans (= whole-AP
   stalls).
4. **Fixed channels, not Auto** — auto-channel does periodic off-channel scans/hops that look
   exactly like these bursts. Pin 5 GHz to 36/40, 2.4 to 1/6/11.
5. **Split SSIDs (2.4 vs 5)** so band-steering can't drag clients and a 2.4 storm can't stall the
   shared SoC's 5 GHz side.
6. **Nightly scheduled reboot ~04:00 local** — band-aid for memory-leak-driven evening decay.
7. **If it persists:** the Tenda is underpowered — promote the modem's AP (`sandy-wanda-backup`)
   to primary for streaming devices, or replace the Tenda with a better AP.
   → **Taken 2026-09-03**: replaced with the Asus mesh (`sandy_wanda_7`).
