#!/usr/bin/env python3
"""
WiFi quality monitor — dual-interface ping loop.

Pings TARGET on both WIFI_IFACE (wlp1s0, via the house AP → pfSense) and WIRED_IFACE
(eno1, direct to pfSense) every INTERVAL seconds.  Comparing both interfaces
distinguishes AP/WiFi faults from pfSense/upstream faults:

  wlp1s0  →  AP (bridge mode)  →  pfSense 192.168.1.1
  eno1                        →  pfSense 192.168.1.1  (direct, no AP in path)

On WiFi degradation:
  - Captures WiFi state snapshot + radio channel scan (cached, no root needed)
  - Sends Telegram alert with signal level, bitrate, BSSID, channel congestion
  - Polls mid-event every MID_POLL_INTERVAL seconds to track BSSID/signal drift
    (BSSID change = deauth/roam; stable BSSID + good signal = AP routing/CPU fault)
On recovery: sends Telegram with duration + peak RTT.
Appends every sample to CSV log for offline analysis.
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ── config ────────────────────────────────────────────────────────────────────

TARGET           = "192.168.1.1"    # LAN gateway  — tests the WiFi/AP hop
MODEM_TARGET     = os.environ.get("MODEM_TARGET", "192.168.3.1")  # WAN gateway — tests pfSense→modem; "" to disable
WAN_TARGET       = os.environ.get("WAN_TARGET",   "8.8.8.8")      # internet    — tests full ISP path
WAN_TARGET_ALT   = os.environ.get("WAN_TARGET_ALT", "1.1.1.1")    # second opinion before calling it loss
WIFI_IFACE       = os.environ.get("WIFI_IFACE",           "wlp1s0")
WIRED_IFACE      = os.environ.get("WIRED_IFACE",          "eno1")
INTERVAL         = int(os.environ.get("INTERVAL",         "5"))
BAD_MS           = float(os.environ.get("BAD_MS",         "150"))
BAD_CONFIRM      = int(os.environ.get("BAD_CONFIRM",      "2"))
WAN_CONFIRM      = int(os.environ.get("WAN_CONFIRM",      "3"))
PFSENSE_CONFIRM  = int(os.environ.get("PFSENSE_CONFIRM",  "3"))   # samples before the bypass convicts pfSense
MID_POLL_INTERVAL = int(os.environ.get("MID_POLL_INTERVAL", "30"))

# Bypass probe: a USB dongle joined to the MODEM's own SSID, so it reaches the modem WITHOUT crossing
# pfSense. That turns "pfSense/host" from an inference (three probes dark at once) into a direct
# measurement, and separates a dead pfSense from this host's networking dying. It deliberately holds
# no default route — see /etc/netplan/60-wifi-bypass.yaml — so it can never carry real traffic.
# Empty/absent interface = probe disabled, and everything behaves exactly as before.
def _autodetect_alt_iface() -> str:
    """First wlx* interface (USB WiFi adapters get MAC-derived names). Import must never fail: this
    module is also imported by the report tooling, which may run where /sys/class/net does not."""
    try:
        return next((n for n in sorted(os.listdir("/sys/class/net")) if n.startswith("wlx")), "")
    except OSError:
        return ""


ALT_IFACE  = os.environ.get("ALT_IFACE") or _autodetect_alt_iface()
ALT_TARGET = os.environ.get("ALT_TARGET", MODEM_TARGET)

_HERE = Path(__file__).resolve().parent
_LOGS = _HERE.parent / "logs"
_LOGS.mkdir(exist_ok=True)

LOG_CSV    = _LOGS / "wifi_monitor.csv"
_CSV_HEADER = "timestamp_utc,wifi_ms,wired_ms,modem_ms,wan_ms\n"
LOG_EVENTS = _LOGS / "wifi_events.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(_LOGS / "monitor.log"),
    ],
)
log = logging.getLogger(__name__)


# ── ping ──────────────────────────────────────────────────────────────────────

def iface_ipv4(iface: str) -> str | None:
    """Current IPv4 address of iface (e.g. '192.168.1.16'), or None if down/unaddressed.

    Re-read each loop so a link-down NIC (its address disappears) is detected as LOSS
    rather than silently falling back to the other interface's path.
    """
    try:
        r = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "dev", iface],
            capture_output=True, text=True, timeout=5,
        )
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", r.stdout)
        return m.group(1) if m else None
    except Exception:
        return None


def ping_one(bind: str | None, target: str = TARGET, timeout: int = 2) -> float | None:
    """Ping `target`, binding to interface device `bind` (e.g. "eno1" / "wlp1s0").

    Device-bind (`-I <iface>`, SO_BINDTODEVICE) pins BOTH egress and the receive
    interface, so each NIC's path is measured symmetrically.  This requires ARP
    hardening (`/etc/sysctl.d/20-wifi-monitor-arp.conf`: arp_ignore=1 arp_announce=2)
    so that, with both NICs on one subnet (eno1 .16 + wlp1s0 .17 → 192.168.1.0/24),
    the gateway's reply returns on the NIC that sent it instead of ARP-fluxing to the
    other — otherwise the bound socket misses it and reports false loss.

    NB: do NOT bind by source IP here.  Source-IP bind does *not* pin egress — the
    route lookup picks the lowest-metric NIC (eno1) regardless of source — so a
    .17-sourced ping leaks out eno1 and returns over wlp1s0, an asymmetric path that
    measures ~67ms of nonsense and trips the fault alarm (the 2026-07-10 false-faults
    regression).  bind=None → unbound (WAN check via the default route).
    """
    try:
        cmd = ["ping", "-c", "1", "-W", str(timeout), target]
        if bind:
            cmd = ["ping", "-I", bind, "-c", "1", "-W", str(timeout), target]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 1,
        )
        if r.returncode == 0:
            m = re.search(r"time=(\d+(?:\.\d+)?)", r.stdout)
            if m:
                return float(m.group(1))
        return None
    except Exception:
        return None


def ping_wan(timeout: int = 2) -> float | None:
    """Is the public internet reachable — not "did 8.8.8.8 answer".

    A single anycast target cannot separate a real WAN outage from that one operator
    dropping us, and a miss was previously recorded as loss either way.  So a miss is
    retried against a second operator before it counts.  The fallback only runs after
    a failure, so a healthy sample still costs exactly one ping.
    """
    for target in (WAN_TARGET, WAN_TARGET_ALT):
        if not target:
            continue
        ms = ping_one(None, target, timeout)
        if ms is not None:
            return ms
    return None


# ── WiFi state helpers — iw preferred, wpa_cli + /proc fallback ───────────────
#
# `iw` is the richest source but not installed on this machine.
# Fallback chain:
#   signal/noise  → /proc/net/wireless  (always present; iwlwifi noise = -256 = unavailable)
#   BSSID/freq    → wpa_cli status      (wpa_supplicant manages the interface)
#   speed         → wpa_cli signal_poll
#   AP scan       → wpa_cli scan_results (cached; no new scan triggered)

import shutil as _shutil
_IW = _shutil.which("iw")


def _proc_signal(iface: str) -> tuple[float | None, float | None]:
    """Read (signal_dBm, noise_dBm) from /proc/net/wireless. Noise is -256 on iwlwifi = n/a."""
    try:
        with open("/proc/net/wireless") as f:
            for line in f:
                if iface not in line:
                    continue
                parts = line.split()
                sig   = float(parts[3].rstrip("."))
                noise = float(parts[4].rstrip("."))
                if sig   > 0: sig   -= 256
                if noise > 0: noise -= 256
                return sig, (noise if noise > -200 else None)
    except Exception:
        pass
    return None, None


def _wpa_run(*args: str, iface: str) -> str:
    try:
        r = subprocess.run(
            ["wpa_cli", "-i", iface, *args],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _wpa_kv(iface: str, cmd: str) -> dict[str, str]:
    """Run a wpa_cli command and return key=value pairs as a dict."""
    out = _wpa_run(cmd, iface=iface)
    result = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def station_dump(iface: str) -> str:
    """Connection state: BSSID, signal, tx speed. Uses iw if available, wpa_cli otherwise."""
    if _IW:
        try:
            r = subprocess.run(
                ["iw", "dev", iface, "station", "dump"],
                capture_output=True, text=True, timeout=5,
            )
            wanted = ("signal:", "tx bitrate:", "rx bitrate:", "connected time:", "BSSID")
            lines = [l.strip() for l in r.stdout.splitlines() if any(k in l for k in wanted)]
            if lines:
                return "\n".join(lines)
        except Exception:
            pass

    # wpa_cli fallback
    lines = []
    status = _wpa_kv(iface, "status")
    if status.get("bssid"):
        lines.append(f"BSSID: {status['bssid']}")
    if status.get("ssid"):
        lines.append(f"SSID: {status['ssid']}")
    if status.get("freq"):
        lines.append(f"freq: {status['freq']} MHz")
    poll = _wpa_kv(iface, "signal_poll")
    if poll.get("RSSI"):
        lines.append(f"signal: {poll['RSSI']} dBm")
    if poll.get("LINKSPEED"):
        lines.append(f"tx bitrate: {poll['LINKSPEED']} MBit/s")
    sig_proc, _ = _proc_signal(iface)
    if sig_proc is not None and not poll.get("RSSI"):
        lines.append(f"signal (proc): {sig_proc:.0f} dBm")
    return "\n".join(lines) if lines else "(no station data)"


def kernel_wifi_lines(iface: str, n: int = 5) -> list[str]:
    """Last n kernel log lines mentioning iface (association events, deauth, errors)."""
    try:
        r = subprocess.run(
            ["journalctl", "-k", "-n", "50", "--no-pager"],
            capture_output=True, text=True, timeout=5,
        )
        return [l for l in r.stdout.splitlines() if iface in l][-n:]
    except Exception:
        return []


# ── radio channel + SNR ───────────────────────────────────────────────────────

def current_channel_mhz(iface: str) -> int | None:
    """Current connected frequency in MHz."""
    if _IW:
        try:
            r = subprocess.run(["iw", "dev", iface, "link"], capture_output=True, text=True, timeout=5)
            m = re.search(r"freq:\s*(\d+)", r.stdout)
            if m:
                return int(m.group(1))
        except Exception:
            pass
    # wpa_cli fallback
    status = _wpa_kv(iface, "status")
    if status.get("freq"):
        try:
            return int(status["freq"])
        except ValueError:
            pass
    return None


def snr_line(iface: str) -> str:
    """Signal + noise floor + SNR.  SNR thresholds: ≥25 excellent, 15-25 good, 10-15 fair, <10 poor."""
    signal_dbm: float | None = None
    noise_dbm:  float | None = None

    if _IW:
        try:
            r = subprocess.run(["iw", "dev", iface, "station", "dump"],
                               capture_output=True, text=True, timeout=5)
            m = re.search(r"signal:\s*([-\d.]+)", r.stdout)
            if m:
                signal_dbm = float(m.group(1))
        except Exception:
            pass
        try:
            r = subprocess.run(["iw", "dev", iface, "survey", "dump"],
                               capture_output=True, text=True, timeout=5)
            in_use = False
            for line in r.stdout.splitlines():
                if "frequency:" in line and "[in use]" in line:
                    in_use = True
                elif "frequency:" in line:
                    in_use = False
                if in_use and "noise:" in line:
                    m = re.search(r"noise:\s*([-\d.]+)", line)
                    if m:
                        noise_dbm = float(m.group(1))
                    break
        except Exception:
            pass

    if signal_dbm is None:
        # wpa_cli / proc fallback
        poll = _wpa_kv(iface, "signal_poll")
        if poll.get("RSSI"):
            try:
                signal_dbm = float(poll["RSSI"])
            except ValueError:
                pass
        if signal_dbm is None:
            signal_dbm, noise_dbm = _proc_signal(iface)
        elif noise_dbm is None:
            _, noise_dbm = _proc_signal(iface)

    if signal_dbm is None:
        return "SNR: n/a (no signal data)"

    sig_str = f"signal {signal_dbm:.0f} dBm"
    if noise_dbm is None:
        return f"{sig_str} | noise floor: n/a (iwlwifi driver limitation)"

    snr = signal_dbm - noise_dbm
    quality = "excellent" if snr >= 25 else "good" if snr >= 15 else "fair" if snr >= 10 else "poor"
    return f"{sig_str} | noise {noise_dbm:.0f} dBm | SNR {snr:.0f} dB ({quality})"


def radio_scan_summary(iface: str) -> str:
    """Nearby APs with signal + channel — iw scan dump preferred, wpa_cli scan_results fallback."""
    aps: list[dict] = []

    if _IW:
        try:
            r = subprocess.run(["iw", "dev", iface, "scan", "dump"],
                               capture_output=True, text=True, timeout=10)
            if r.stdout.strip():
                current: dict = {}
                for line in r.stdout.splitlines():
                    if line.startswith("BSS "):
                        if current:
                            aps.append(current)
                        bssid = line.split()[1].split("(")[0].strip()
                        current = {"bssid": bssid, "freq": None, "signal": None, "ssid": ""}
                    elif current:
                        ls = line.strip()
                        if ls.startswith("freq:"):
                            m = re.search(r"freq:\s*(\d+)", ls)
                            if m:
                                current["freq"] = int(m.group(1))
                        elif ls.startswith("signal:"):
                            m = re.search(r"signal:\s*([-\d.]+)", ls)
                            if m:
                                current["signal"] = float(m.group(1))
                        elif ls.startswith("SSID:"):
                            current["ssid"] = ls[5:].strip()
                if current:
                    aps.append(current)
        except Exception:
            pass

    if not aps:
        # wpa_cli scan_results: BSS\tfreq\trssi\tflags\tssid
        out = _wpa_run("scan_results", iface=iface)
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 5 and re.match(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", parts[0]):
                try:
                    aps.append({
                        "bssid":  parts[0],
                        "freq":   int(parts[1]),
                        "signal": float(parts[2]),
                        "ssid":   parts[4],
                    })
                except (ValueError, IndexError):
                    pass

    if not aps:
        return "(no scan data available)"

    our_freq = current_channel_mhz(iface)
    same_ch  = [a for a in aps if a["freq"] == our_freq] if our_freq else []
    top_all  = sorted([a for a in aps if a["signal"] is not None],
                      key=lambda a: a["signal"], reverse=True)[:5]

    parts = [f"Total visible APs: {len(aps)}"]
    if our_freq:
        parts.append(f"Our frequency: {our_freq} MHz ({len(same_ch)} AP(s) on same channel)")
        for a in sorted(same_ch, key=lambda x: x.get("signal") or -999, reverse=True):
            parts.append(f"  {a['bssid']}  {a['signal']} dBm  \"{a['ssid']}\"")
    parts.append("Top 5 by signal:")
    for a in top_all:
        marker = " ← same ch" if a["freq"] == our_freq else ""
        parts.append(f"  {a['bssid']}  {a['signal']} dBm  {a['freq']}MHz  \"{a['ssid']}\"{marker}")
    return "\n".join(parts)


# ── full event snapshot (at event start) ─────────────────────────────────────

def event_start_snapshot(iface: str) -> str:
    parts = ["--- WiFi state ---", station_dump(iface), snr_line(iface)]
    kernel = kernel_wifi_lines(iface)
    if kernel:
        parts += ["--- kernel ---"] + kernel
    parts += ["--- radio scan ---", radio_scan_summary(iface)]
    return "\n".join(parts)


# ── mid-event poll (tracks BSSID/signal drift during degradation) ─────────────

def mid_event_poll(iface: str) -> str:
    """
    Lightweight mid-event sample.  Key diagnostic: is the BSSID changing?
      - BSSID changes     → deauth / forced roam (AP kicked the client)
      - BSSID stable      → association is alive; AP is not routing (CPU/process fault)
      - Signal dropping   → radio issue or the client is moving away
      - Signal stable     → radio is fine; problem is in the AP stack or upstream
    """
    parts = [station_dump(iface), snr_line(iface)]
    kernel = kernel_wifi_lines(iface, n=3)
    if kernel:
        parts += ["--- kernel ---"] + kernel
    return "\n".join(parts)


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(text: str) -> None:
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID",  "")
    if not token or not chat_id:
        log.warning("Telegram not configured — alert skipped")
        return
    import urllib.request
    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    body = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req  = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")


# ── helpers ───────────────────────────────────────────────────────────────────

def utcnow() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _fmt(v: float | None) -> str:
    return str(v) if v is not None else "LOSS"


def _fmt_ms(v: float | None) -> str:
    return f"{v:.0f}ms" if v is not None else "LOSS"


def _ping_target(target: str, timeout: int = 2) -> float | None:
    """Unbound ping to a specific target (used for modem/WAN gateway)."""
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), target],
            capture_output=True, text=True, timeout=timeout + 1,
        )
        if r.returncode == 0:
            m = re.search(r"time=(\d+(?:\.\d+)?)", r.stdout)
            if m:
                return float(m.group(1))
        return None
    except Exception:
        return None


def append_csv(ts: str, wifi_ms: float | None, wired_ms: float | None,
               modem_ms: float | None, wan_ms: float | None,
               alt_ms: float | None = None, alt_measured: bool = False) -> None:
    # alt_ms was appended as a 6th column on 2026-08-06. Rows written before that have 5 fields and
    # every reader treats a missing 6th as "not measured" — NOT as a loss — so the month of history
    # stays usable rather than being rotated away.
    #
    # alt_measured must come from the caller: alt_ms is None both when the bypass was pinged and
    # lost AND when it was skipped because the dongle had no link. Only the first is a LOSS. Keying
    # on ALT_IFACE here (as this once did) wrote LOSS on every sample for a month while the dongle
    # sat unassociated, which read in the report as a permanently failing bypass.
    with open(LOG_CSV, "a") as f:
        f.write(f"{ts},{_fmt(wifi_ms)},{_fmt(wired_ms)},{_fmt(modem_ms)},{_fmt(wan_ms)},"
                f"{_fmt(alt_ms) if alt_measured else ''}\n")


def append_event(text: str) -> None:
    with open(LOG_EVENTS, "a") as f:
        f.write(f"[{utcnow()}] {text}\n")


def duration_s(start_ts: str, end_ts: str) -> int:
    fmt = "%Y-%m-%d %H:%M:%S"
    try:
        return int((
            datetime.strptime(end_ts, fmt) - datetime.strptime(start_ts, fmt)
        ).total_seconds())
    except Exception:
        return -1


# ── daily report ─────────────────────────────────────────────────────────────

# ── root-cause attribution ────────────────────────────────────────────────────
# The four probes are nested path segments — WiFi and Wired both end at pfSense, Modem at the WAN
# gateway, WAN beyond it — so a single fault lights up several rows at once and the raw per-link
# totals count it more than once. Attribute each failing sample to exactly ONE bucket: the most
# upstream cause consistent with what failed. Then "85s on the WiFi hop" means the radio, not a
# router outage that happened to darken WiFi too.
_BUCKETS = [                      # display order = climbing the stack, local first
    ("WiFi hop",     "📡"),
    ("Wired hop",    "🧵"),
    ("pfSense/host", "🏠"),
    ("Modem link",   "🔌"),
    ("Modem unresponsive", "🔍"),
    ("Provider",     "🌐"),
    ("Scheduled",    "🔧"),
    ("Unattributed", "❓"),
]

# Kept out of the "unplanned loss" verdict: Scheduled is downtime we already know about, and
# Modem unresponsive is not downtime at all. Letting either win "dominant cause" buries the
# fault actually worth looking at.
VERDICT_EXCLUDED = {"Scheduled", "Modem unresponsive"}

# Known recurring outages, as UTC HH:MM-HH:MM windows. Loss inside a window is RE-LABELLED
# "Scheduled", never dropped — the time still shows up honestly, so a restart that grows from 60s to
# 5 minutes stays visible, but a known nightly event stops swamping the WiFi-hop signal.
#
# ⚠️ THESE ARE UTC, while the household thinks in local time (Asia/Jerusalem = UTC+3 in summer).
#
# Empty by default since the 2026-09-03 AP swap (Tenda sandy_wanda_6 → Asus sandy_wanda_7). The two
# old windows described the Tenda's own habits — a 01:05 UTC nightly restart and a band-steer roam
# exactly 12h later — and the Asus has not been observed long enough to know whether it has any.
# Re-populate only once a recurring event is actually measured; a guessed window silently re-labels
# real outages as "Scheduled".
MAINTENANCE_WINDOWS = [
    tuple(part.split("-", 1))
    for part in os.environ.get("MAINTENANCE_WINDOWS", "").split(",")
    if "-" in part
]


def in_maintenance(hhmm: str) -> bool:
    return any(lo <= hhmm <= hi for lo, hi in MAINTENANCE_WINDOWS)


def root_cause(wifi: bool, wired: bool, modem: bool, wan: bool) -> str | None:
    """The single component that best explains this sample. None = nothing failed.

    Ordered nearest-hop-first, because a break at one hop darkens everything beyond it: a dead
    router must be tested before the WiFi-only rule or it would be misfiled as a radio problem.
    The probes are nested path prefixes (WiFi/Wired stop at pfSense, Modem at the WAN gateway, WAN
    beyond it), so the nearest hop that is provably broken is the one that explains the rest.
    Note a *provider* outage does NOT cascade downward here — WiFi/Wired/Modem probes all terminate
    at or before the modem, so only the WAN row can see it.

    The bypass probe is deliberately NOT read here. On a single sample it cannot separate a pfSense
    fault from one unlucky packet — see proven_pfsense_runs, which decides that over a run."""
    if not (wifi or wired or modem or wan):
        return None
    if wifi and wired and modem:
        return "pfSense/host"     # inferred: every local path dark → the router or this host
    if modem and not wan:
        # The modem ignored us, yet 8.8.8.8 answered THROUGH it on the same pass — so the path
        # across the modem was working and nobody lost connectivity. Modems routinely rate-limit
        # ICMP aimed at their own management IP while the forwarding fast path runs untouched,
        # and the modem probe's 2s timeout means the WAN ping lands ~2s later, past a brief blip.
        # Real, worth watching as a health signal, but it is not an outage.
        return "Modem unresponsive"
    if modem:
        return "Modem link"       # the modem is dark AND nothing beyond it answers
    if wan:
        return "Provider"         # the modem answers, the internet does not
    if wifi and not wired:
        return "WiFi hop"         # same target as Wired; only the radio path failed
    if wired and not wifi:
        return "Wired hop"
    return "Unattributed"


def proven_pfsense_runs(samples: list[tuple]) -> set[int]:
    """Indices of samples where the bypass probe genuinely convicts pfSense.

    The dongle reaches the modem without crossing pfSense, so "modem dark the routed way, alive on
    the bypass" reads as proof. It is only proof if it HOLDS. Under partial loss both probes are
    querying the same struggling modem and each drops packets independently, so that pairing turns
    up by chance constantly: on 2026-08-08 it scored 2740s against a router that answered every
    single LAN ping that day, and split one modem fault across two buckets in the exact ratio of
    the bypass probe's own success rate.

    A real router fault is sustained, so require the routed path to stay dark for PFSENSE_CONFIRM
    consecutive samples while the bypass answers on every one of them. Any break — the modem
    replying, or the bypass dropping a packet — ends the run and the samples fall through to the
    ordinary per-sample verdict.

    The WAN probe must be dark too. Both it and the modem probe cross pfSense, so a genuine pfSense
    fault takes out both; if 8.8.8.8 answered, traffic was flowing through pfSense and the silent
    modem is the "Modem unresponsive" case, not a router fault."""
    proven: set[int] = set()
    run: list[int] = []

    for i, sample in enumerate(samples):
        routed_dark = sample[3] and sample[4]       # modem AND wan — both cross pfSense
        bypass      = sample[5] if len(sample) > 5 else None
        if routed_dark and bypass is False:         # routed paths dark, bypass answered
            run.append(i)
            continue
        if len(run) >= PFSENSE_CONFIRM:
            proven.update(run)
        run = []

    if len(run) >= PFSENSE_CONFIRM:
        proven.update(run)
    return proven


def attribute(samples: list[tuple]) -> dict[str, tuple[int, int]]:
    """-> {bucket: (seconds_lost, episodes)}. An episode is a run of consecutive samples sharing a
    cause, which separates one 10s outage from two unrelated 5s ones.

    Samples are (hhmm, wifi, wired, modem, wan[, alt]); the leading "HH:MM" (UTC) lets a known
    scheduled outage be re-labelled rather than blamed on the radio."""
    secs: dict[str, int] = {}
    eps: dict[str, int] = {}
    previous = None
    pfsense_proven = proven_pfsense_runs(samples)
    for i, (hhmm, *flags) in enumerate(samples):
        # The bypass verdict is decided over a run, so it overrides the per-sample reading.
        cause = "pfSense/host" if i in pfsense_proven else root_cause(*flags[:4])
        if cause and in_maintenance(hhmm):
            cause = "Scheduled"
        if cause:
            secs[cause] = secs.get(cause, 0) + INTERVAL
            if cause != previous:
                eps[cause] = eps.get(cause, 0) + 1
        previous = cause
    return {name: (secs[name], eps[name]) for name, _ in _BUCKETS if name in secs}


def daily_report() -> None:
    """
    Read yesterday's CSV + events log and send a Telegram digest.
    Run via: python3 monitor.py --report  (triggered by systemd timer at 07:00 UTC)
    """
    yesterday = (datetime.now(tz=timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    wifi_rtt:  list[float] = []
    wired_rtt: list[float] = []
    modem_rtt: list[float] = []
    wan_rtt:   list[float] = []
    wifi_loss = wired_loss = modem_loss = wan_loss = total = 0
    samples: list[tuple] = []      # (hhmm, wifi, wired, modem, wan) loss flags, for attribution

    def _parse_col(parts: list[str], idx: int, rtt_list: list, loss_ref: list) -> None:
        if idx >= len(parts):
            return
        if parts[idx] == "LOSS":
            loss_ref[0] += 1
        else:
            try:
                rtt_list.append(float(parts[idx]))
            except ValueError:
                loss_ref[0] += 1

    if LOG_CSV.exists():
        with open(LOG_CSV) as f:
            for line in f:
                if not line.startswith(yesterday):
                    continue
                parts = line.strip().split(",")
                if len(parts) < 3:
                    continue
                total += 1
                wl, wrl, ml, wanl = [0], [0], [0], [0]
                _parse_col(parts, 1, wifi_rtt,  wl)
                _parse_col(parts, 2, wired_rtt, wrl)
                _parse_col(parts, 3, modem_rtt, ml)
                _parse_col(parts, 4, wan_rtt,   wanl)
                wifi_loss  += wl[0]; wired_loss += wrl[0]
                modem_loss += ml[0]; wan_loss   += wanl[0]
                # 6th column (alt) may be absent on rows predating 2026-08-06, or empty when no
                # dongle is fitted. Either way it is "not measured" (None), never a loss.
                alt_raw = parts[5].strip() if len(parts) > 5 else ""
                alt_flag = None if alt_raw == "" else (alt_raw == "LOSS")
                samples.append((parts[0][11:16],
                                bool(wl[0]), bool(wrl[0]), bool(ml[0]), bool(wanl[0]), alt_flag))

    degradation_events: list[dict] = []
    if LOG_EVENTS.exists():
        cur: dict = {}
        with open(LOG_EVENTS) as f:
            for line in f:
                if yesterday not in line:
                    continue
                if "DEGRADED" in line and "MID-EVENT" not in line:
                    m = re.search(r"wifi=([\d.]+|None)", line)
                    cur = {"start": line[:21].strip("[] "), "peak": float(m.group(1)) if m and m.group(1) != "None" else None}
                elif "RECOVERED" in line and cur:
                    m = re.search(r"duration=(\d+)s", line)
                    cur["duration"] = int(m.group(1)) if m else 0
                    m = re.search(r"peak=([\d.]+)", line)
                    cur["peak"] = float(m.group(1)) if m else cur.get("peak")
                    degradation_events.append(cur)
                    cur = {}

    if total == 0:
        msg = f"📶 WiFi daily report — {yesterday}\nNo data collected."
        send_telegram(msg)
        log.info(f"Daily report sent for {yesterday} (no data)")
        return

    def percentile(arr: list[float], p: int) -> float:
        s = sorted(arr)
        idx = int(len(s) * p / 100)
        return s[min(idx, len(s) - 1)]

    def lost_time(lost_samples: int) -> str:
        """Wall-clock time behind a loss percentage — one missed sample is one INTERVAL offline.
        A loss % alone hides scale: the same 0.1% is 85s on a full day and seconds on a short one."""
        return f" | total {lost_samples * INTERVAL}s"

    wifi_loss_pct  = 100 * wifi_loss  / total
    wired_loss_pct = 100 * wired_loss / total

    # Coverage self-labels: the loop sleeps INTERVAL *after* doing the work, so a healthy day drifts
    # ~1%. A timed-out ping costs up to 2s, so a genuinely bad day shows visibly lower coverage —
    # which makes the number a symptom in its own right, not just bookkeeping.
    cov_min = total * INTERVAL // 60
    cov_pct = 100 * cov_min / 1440
    cov_note = ("normal drift" if cov_pct >= 98 else
                "elevated — timeouts eating the loop" if cov_pct >= 90 else
                "degraded — sustained failures")

    attribution = attribute(samples)
    # Judge the day on UNPLANNED loss only — see VERDICT_EXCLUDED.
    unplanned = {b: v for b, v in attribution.items() if b not in VERDICT_EXCLUDED}
    scheduled_s = attribution.get("Scheduled", (0, 0))[0]
    unresponsive_s = attribution.get("Modem unresponsive", (0, 0))[0]
    if unplanned:
        lost = sum(s for s, _ in unplanned.values())
        worst = max(unplanned, key=lambda b: unplanned[b][0])
        verdict = (f"{'🟢' if lost < 300 else '🟠' if lost < 1800 else '🔴'} "
                   f"{lost}s unplanned · dominant: {worst}")
    else:
        verdict = "🟢 clean — no unplanned loss"
    if scheduled_s:
        verdict += f" · +{scheduled_s}s scheduled"
    if unresponsive_s:
        verdict += f" · +{unresponsive_s}s modem unresponsive (no outage)"

    lines = [
        f"📶 Network daily report — {yesterday}",
        verdict,
        f"Coverage {cov_min}/1440 min ({cov_pct:.1f}% · {cov_note}) · {total} samples @{INTERVAL}s",
    ]

    if attribution:
        peak = max(s for s, _ in attribution.values())
        lines.append("")
        lines.append("━━ ROOT CAUSE ━━ <i>one bucket per sample, most-upstream wins</i>")
        for name, icon in _BUCKETS:
            if name not in attribution:
                continue
            secs, episodes = attribution[name]
            bar = "█" * max(1, round(10 * secs / peak))
            lines.append(f"{icon} {name:13s} {secs:4d}s  {episodes}×  {bar}")
        lines.append("")
        lines.append("━━ RAW ━━ <i>per-link, unattributed</i>")

    if wifi_rtt:
        lines.append(
            f"WiFi  — loss {wifi_loss_pct:.1f}% | "
            f"median {percentile(wifi_rtt, 50):.0f}ms | "
            f"p95 {percentile(wifi_rtt, 95):.0f}ms | "
            f"max {max(wifi_rtt):.0f}ms"
            + lost_time(wifi_loss)
        )
    else:
        lines.append(f"WiFi  — {wifi_loss_pct:.1f}% loss (no good samples)" + lost_time(wifi_loss))

    if wired_rtt:
        lines.append(
            f"Wired — loss {wired_loss_pct:.1f}% | "
            f"median {percentile(wired_rtt, 50):.0f}ms | "
            f"max {max(wired_rtt):.0f}ms"
            + lost_time(wired_loss)
        )
    else:
        lines.append(f"Wired — {wired_loss_pct:.1f}% loss (offline / unreachable)" + lost_time(wired_loss))

    modem_loss_pct = 100 * modem_loss / total if total else 0
    if modem_rtt:
        lines.append(
            f"Modem — loss {modem_loss_pct:.1f}% | "
            f"median {percentile(modem_rtt, 50):.0f}ms | "
            f"max {max(modem_rtt):.0f}ms"
            + lost_time(modem_loss)
        )
    elif modem_loss > 0:
        lines.append(f"Modem — {modem_loss_pct:.1f}% loss" + lost_time(modem_loss))

    wan_loss_pct = 100 * wan_loss / total if total else 0
    if wan_rtt:
        lines.append(
            f"WAN   — loss {wan_loss_pct:.1f}% | "
            f"median {percentile(wan_rtt, 50):.0f}ms | "
            f"max {max(wan_rtt):.0f}ms"
            + lost_time(wan_loss)
        )
    elif total > 0:
        lines.append(f"WAN   — {wan_loss_pct:.1f}% loss (internet down or no data)" + lost_time(wan_loss))

    if degradation_events:
        durations = [e["duration"] for e in degradation_events if "duration" in e]
        total_down = sum(durations)
        peaks = [e["peak"] for e in degradation_events if e.get("peak")]
        lines.append(
            f"Events: {len(degradation_events)}  |  "
            f"total downtime ~{total_down}s  |  "
            f"longest {max(durations) if durations else '?'}s  |  "
            f"peak {max(peaks):.0f}ms" if peaks else ""
        )
        # Highlight the hour with the most events
        hours = [e["start"][11:13] for e in degradation_events if len(e.get("start", "")) > 13]
        if hours:
            from collections import Counter
            worst_hour, count = Counter(hours).most_common(1)[0]
            if count > 1:
                lines.append(f"Hotspot hour: {worst_hour}:xx UTC ({count} events)")
    else:
        lines.append("No degradation events.")

    send_telegram("\n".join(lines))
    log.info(f"Daily report sent for {yesterday}")


# ── main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    _load_dotenv()

    if len(sys.argv) > 1 and sys.argv[1] == "--report":
        daily_report()
        return

    if not LOG_CSV.exists():
        with open(LOG_CSV, "w") as f:
            f.write(_CSV_HEADER)

    modem_enabled = bool(MODEM_TARGET)
    log.info(
        f"wifi-monitor starting — target={TARGET}  modem={MODEM_TARGET or 'disabled'}  "
        f"wan={WAN_TARGET}+{WAN_TARGET_ALT or 'none'}  "
        f"wifi={WIFI_IFACE}  wired={WIRED_IFACE}  "
        f"interval={INTERVAL}s  bad_threshold={BAD_MS}ms×{BAD_CONFIRM}  wan_confirm=×{WAN_CONFIRM}  "
        f"mid_poll={MID_POLL_INTERVAL}s"
    )

    degraded       = False
    wan_down       = False
    bad_streak     = 0
    wan_bad_streak = 0
    wan_ok_streak  = 0
    wan_window: list[tuple[float | None, float | None]] = []   # last WAN_CONFIRM (modem, bypass) samples
    event_start_ts: str | None = None
    event_peak     = 0.0
    next_mid_poll  = 0.0

    while True:
        ts        = utcnow()
        # device-bind (not source-IP) so each NIC's path is measured symmetrically;
        # requires the ARP-hardening sysctl (see ping_one docstring). iface_ipv4 guards
        # that the NIC is up/addressed before we bother pinging it.
        wifi_up   = iface_ipv4(WIFI_IFACE)
        wired_up  = iface_ipv4(WIRED_IFACE)
        wifi_ms   = ping_one(WIFI_IFACE,  TARGET) if wifi_up  else None
        wired_ms  = ping_one(WIRED_IFACE, TARGET) if wired_up else None
        modem_ms  = _ping_target(MODEM_TARGET) if modem_enabled else None
        wan_ms    = ping_wan()
        # Same target as modem_ms, but bound to the dongle so it reaches the modem directly instead
        # of via pfSense. The two together are what prove (rather than infer) a pfSense fault.
        # alt_live distinguishes "the bypass was measured and failed" from "there is no bypass" —
        # only the first licenses a verdict about pfSense.
        alt_live  = bool(ALT_IFACE and iface_ipv4(ALT_IFACE))
        alt_ms    = ping_one(ALT_IFACE, ALT_TARGET) if alt_live else None
        append_csv(ts, wifi_ms, wired_ms, modem_ms, wan_ms, alt_ms, alt_measured=alt_live)

        wifi_bad   = wifi_ms is None or wifi_ms > BAD_MS
        bad_streak = bad_streak + 1 if wifi_bad else 0

        if degraded and wifi_ms is not None:
            event_peak = max(event_peak, wifi_ms)

        # ── WAN up/down transitions ────────────────────────────────────────────
        # Debounced in BOTH directions. A lossy-but-alive WAN (2026-08-08: ~20% loss for
        # three hours) otherwise flips state on every dropped packet — that incident alone
        # sent 320 messages. The state only moves once WAN_CONFIRM samples agree.
        if wan_ms is None:
            wan_bad_streak += 1
            wan_ok_streak   = 0
        else:
            wan_ok_streak  += 1
            wan_bad_streak  = 0

        wan_window.append((modem_ms, alt_ms))
        del wan_window[:-WAN_CONFIRM]

        if not wan_down and wan_bad_streak >= WAN_CONFIRM:
            wan_down = True
            # Isolate the fault to one hop, judged over the whole confirming window rather
            # than the one triggering sample: during patchy loss a single sample flips the
            # verdict at random, so "did this hop EVER answer while the WAN was dark" is the
            # stable question. The bypass reaches the modem without crossing pfSense, so
            # modem-dark + bypass-alive is the one combination that implicates pfSense.
            modem_ever_up = any(m is not None for m, _ in wan_window)
            alt_ever_up   = any(a is not None for _, a in wan_window)

            fix = "if it persists, power-cycle the modem"
            if not modem_enabled:
                cause = f"WiFi→pfSense: {_fmt_ms(wifi_ms)}"
            elif modem_ever_up:
                cause = f"modem still answering ({_fmt_ms(modem_ms)}) → upstream of the modem (Bezeq/ISP)"
            elif alt_live and alt_ever_up:
                cause = "modem answers the bypass but never via pfSense → pfSense suspected"
                fix   = ""          # restarting the modem is the wrong lever here
            elif alt_live:
                cause = "modem dark on BOTH the pfSense path and the bypass → modem or ISP"
            else:
                cause = "modem also LOSS, no bypass probe → pfSense WAN or modem fault"

            msg = f"🌐 WAN down ({wan_bad_streak} samples)\n{cause}"
            if fix:
                msg += f"\n→ {fix}"
            log.warning(f"WAN DOWN  modem={modem_ms} wan={wan_ms} alt={alt_ms}")
            send_telegram(msg)
            append_event(f"WAN_DOWN  modem={modem_ms} wan={wan_ms} alt={alt_ms}")
        elif wan_down and wan_ok_streak >= WAN_CONFIRM:
            wan_down = False
            msg = f"🌐 WAN restored ({wan_ms:.0f}ms)"
            if modem_enabled:
                msg += f"  modem {_fmt_ms(modem_ms)}"
            log.info(f"WAN RESTORED  wan={wan_ms} modem={modem_ms}")
            send_telegram(msg)
            append_event(f"WAN_RESTORED  wan={wan_ms} modem={modem_ms}")

        # ── OK → DEGRADED ─────────────────────────────────────────────────────
        if not degraded and bad_streak >= BAD_CONFIRM:
            degraded       = True
            event_start_ts = ts
            event_peak     = wifi_ms or 9999.0
            next_mid_poll  = time.monotonic() + MID_POLL_INTERVAL

            if wan_ms is None and (not modem_enabled or modem_ms is None):
                fault = "WAN + modem LOSS → pfSense or modem fault"
            elif wan_ms is None and modem_ms is not None:
                fault = f"modem OK ({modem_ms:.0f}ms), WAN LOSS → Bezeq/ISP fault"
            else:
                fault = f"WAN OK ({_fmt_ms(wan_ms)}) → AP fault"

            snapshot = event_start_snapshot(WIFI_IFACE)
            msg = (
                f"⚠️ WiFi degraded\n"
                f"WiFi: {_fmt_ms(wifi_ms)} | {fault}\n\n"
                f"{snapshot}"
            )
            log.warning(f"DEGRADED  wifi={wifi_ms} modem={modem_ms} wan={wan_ms}")
            send_telegram(msg)
            append_event(f"DEGRADED  wifi={wifi_ms} modem={modem_ms} wan={wan_ms}\n{snapshot}")

        # ── DEGRADED → RECOVERED ──────────────────────────────────────────────
        elif degraded and not wifi_bad:
            dur = duration_s(event_start_ts, ts)
            msg = (
                f"✅ WiFi recovered\n"
                f"Duration: ~{dur}s  |  peak RTT: {event_peak:.0f}ms\n"
                f"WiFi: {_fmt_ms(wifi_ms)}  |  modem: {_fmt_ms(modem_ms)}  |  WAN: {_fmt_ms(wan_ms)}"
            )
            log.info(f"RECOVERED  duration={dur}s peak={event_peak:.0f}ms")
            send_telegram(msg)
            append_event(f"RECOVERED  duration={dur}s peak={event_peak:.0f}ms wifi={wifi_ms} wan={wan_ms}")
            degraded       = False
            bad_streak     = 0
            event_start_ts = None
            event_peak     = 0.0
            next_mid_poll  = 0.0

        # ── mid-event poll ─────────────────────────────────────────────────────
        elif degraded and time.monotonic() >= next_mid_poll:
            snap = mid_event_poll(WIFI_IFACE)
            append_event(f"MID-EVENT  wifi={wifi_ms} wan={wan_ms}\n{snap}")
            log.info(f"MID-EVENT  wifi={wifi_ms} wan={wan_ms}")
            next_mid_poll = time.monotonic() + MID_POLL_INTERVAL

        time.sleep(INTERVAL)


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


if __name__ == "__main__":
    main()
