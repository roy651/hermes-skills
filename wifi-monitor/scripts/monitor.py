#!/usr/bin/env python3
"""
WiFi quality monitor — dual-interface ping loop.

Pings TARGET on both WIFI_IFACE (wlp1s0, via Tenda AP → pfSense) and WIRED_IFACE
(eno1, direct to pfSense) every INTERVAL seconds.  Comparing both interfaces
distinguishes Tenda/WiFi faults from pfSense/upstream faults:

  wlp1s0  →  Tenda AP  →  pfSense 192.168.1.1
  eno1               →  pfSense 192.168.1.1  (direct, no AP in path)

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

TARGET           = "192.168.1.1"   # LAN gateway — tests WiFi hop
WAN_TARGET       = os.environ.get("WAN_TARGET", "8.8.8.8")  # internet — tests pfSense WAN
WIFI_IFACE       = os.environ.get("WIFI_IFACE",           "wlp1s0")
WIRED_IFACE      = os.environ.get("WIRED_IFACE",          "eno1")
INTERVAL         = int(os.environ.get("INTERVAL",         "5"))
BAD_MS           = float(os.environ.get("BAD_MS",         "150"))
BAD_CONFIRM      = int(os.environ.get("BAD_CONFIRM",      "2"))
MID_POLL_INTERVAL = int(os.environ.get("MID_POLL_INTERVAL", "30"))

_HERE = Path(__file__).resolve().parent
_LOGS = _HERE.parent / "logs"
_LOGS.mkdir(exist_ok=True)

LOG_CSV    = _LOGS / "wifi_monitor.csv"
_CSV_HEADER = "timestamp_utc,wifi_ms,wired_ms,wan_ms\n"
LOG_EVENTS = _LOGS / "wifi_events.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(_LOGS / "monitor.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ── ping ──────────────────────────────────────────────────────────────────────

def ping_one(iface: str | None, timeout: int = 2) -> float | None:
    """Ping via iface (bound) or WAN_TARGET (unbound if iface is None).  Returns RTT ms or None."""
    try:
        target = TARGET if iface else WAN_TARGET
        cmd = ["ping", "-c", "1", "-W", str(timeout), target]
        if iface:
            cmd = ["ping", "-I", iface, "-c", "1", "-W", str(timeout), target]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 1,
        )
        if r.returncode == 0:
            m = re.search(r"time=(\d+(?:\.\d+)?)", r.stdout)
            if m:
                return float(m.group(1))
        return None
    except Exception:
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


def append_csv(ts: str, wifi_ms: float | None, wired_ms: float | None, wan_ms: float | None) -> None:
    with open(LOG_CSV, "a") as f:
        f.write(f"{ts},{_fmt(wifi_ms)},{_fmt(wired_ms)},{_fmt(wan_ms)}\n")


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

def daily_report() -> None:
    """
    Read yesterday's CSV + events log and send a Telegram digest.
    Run via: python3 monitor.py --report  (triggered by systemd timer at 07:00 UTC)
    """
    yesterday = (datetime.now(tz=timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    wifi_rtt:  list[float] = []
    wired_rtt: list[float] = []
    wan_rtt:   list[float] = []
    wifi_loss  = 0
    wired_loss = 0
    wan_loss   = 0
    total      = 0

    if LOG_CSV.exists():
        with open(LOG_CSV) as f:
            for line in f:
                if not line.startswith(yesterday):
                    continue
                parts = line.strip().split(",")
                if len(parts) < 3:
                    continue
                total += 1
                if parts[1] == "LOSS":
                    wifi_loss += 1
                else:
                    try:
                        wifi_rtt.append(float(parts[1]))
                    except ValueError:
                        wifi_loss += 1
                if parts[2] == "LOSS":
                    wired_loss += 1
                else:
                    try:
                        wired_rtt.append(float(parts[2]))
                    except ValueError:
                        wired_loss += 1
                if len(parts) >= 4:
                    if parts[3] == "LOSS":
                        wan_loss += 1
                    else:
                        try:
                            wan_rtt.append(float(parts[3]))
                        except ValueError:
                            wan_loss += 1

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

    wifi_loss_pct  = 100 * wifi_loss  / total
    wired_loss_pct = 100 * wired_loss / total

    lines = [f"📶 WiFi daily report — {yesterday}", f"Samples: {total}  ({total * INTERVAL // 60} min coverage)"]

    if wifi_rtt:
        lines.append(
            f"WiFi  — loss {wifi_loss_pct:.1f}% | "
            f"median {percentile(wifi_rtt, 50):.0f}ms | "
            f"p95 {percentile(wifi_rtt, 95):.0f}ms | "
            f"max {max(wifi_rtt):.0f}ms"
        )
    else:
        lines.append(f"WiFi  — {wifi_loss_pct:.1f}% loss (no good samples)")

    if wired_rtt:
        lines.append(
            f"Wired — loss {wired_loss_pct:.1f}% | "
            f"median {percentile(wired_rtt, 50):.0f}ms | "
            f"max {max(wired_rtt):.0f}ms"
        )
    else:
        lines.append(f"Wired — {wired_loss_pct:.1f}% loss (offline / unreachable)")

    wan_loss_pct = 100 * wan_loss / total if total else 0
    if wan_rtt:
        lines.append(
            f"WAN   — loss {wan_loss_pct:.1f}% | "
            f"median {percentile(wan_rtt, 50):.0f}ms | "
            f"max {max(wan_rtt):.0f}ms"
        )
    elif total > 0:
        lines.append(f"WAN   — {wan_loss_pct:.1f}% loss (internet down or no data)")

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

    log.info(
        f"wifi-monitor starting — target={TARGET}  wan={WAN_TARGET}  "
        f"wifi={WIFI_IFACE}  wired={WIRED_IFACE}  "
        f"interval={INTERVAL}s  bad_threshold={BAD_MS}ms×{BAD_CONFIRM}  "
        f"mid_poll={MID_POLL_INTERVAL}s"
    )

    degraded       = False
    wan_down       = False
    bad_streak     = 0
    event_start_ts: str | None = None
    event_peak     = 0.0
    next_mid_poll  = 0.0

    while True:
        ts       = utcnow()
        wifi_ms  = ping_one(WIFI_IFACE)
        wired_ms = ping_one(WIRED_IFACE)
        wan_ms   = ping_one(None)          # unbound — uses default route (eno1 metric 100)
        append_csv(ts, wifi_ms, wired_ms, wan_ms)

        wifi_bad   = wifi_ms is None or wifi_ms > BAD_MS
        bad_streak = bad_streak + 1 if wifi_bad else 0

        if degraded and wifi_ms is not None:
            event_peak = max(event_peak, wifi_ms)

        # ── WAN up/down transitions (independent of WiFi state) ───────────────
        wan_bad_now = wan_ms is None
        if not wan_down and wan_bad_now:
            wan_down = True
            msg = (
                f"🌐 WAN down ({WAN_TARGET} unreachable)\n"
                f"WiFi→pfSense: {'LOSS' if wifi_ms is None else f'{wifi_ms:.0f}ms'}"
            )
            log.warning(f"WAN DOWN  wifi={wifi_ms} wired={wired_ms}")
            send_telegram(msg)
            append_event(f"WAN_DOWN  wifi={wifi_ms} wired={wired_ms}")
        elif wan_down and not wan_bad_now:
            wan_down = False
            msg = f"🌐 WAN restored ({WAN_TARGET} {wan_ms:.0f}ms)"
            log.info(f"WAN RESTORED  {wan_ms:.0f}ms")
            send_telegram(msg)
            append_event(f"WAN_RESTORED  wan={wan_ms}")

        # ── OK → DEGRADED ─────────────────────────────────────────────────────
        if not degraded and bad_streak >= BAD_CONFIRM:
            degraded       = True
            event_start_ts = ts
            event_peak     = wifi_ms or 9999.0
            next_mid_poll  = time.monotonic() + MID_POLL_INTERVAL

            if wan_ms is None:
                fault = "WAN also down — pfSense or ISP fault"
            elif wired_ms is not None and wired_ms < BAD_MS:
                fault = f"wired OK ({wired_ms:.0f}ms), WAN OK ({wan_ms:.0f}ms) → Tenda fault"
            else:
                fault = f"wired LOSS, WAN OK ({wan_ms:.0f}ms) → eno1 routing issue"

            snapshot = event_start_snapshot(WIFI_IFACE)
            msg = (
                f"⚠️ WiFi degraded\n"
                f"WiFi: {'LOSS' if wifi_ms is None else f'{wifi_ms:.0f}ms'} | {fault}\n\n"
                f"{snapshot}"
            )
            log.warning(f"DEGRADED  wifi={wifi_ms} wired={wired_ms} wan={wan_ms}")
            send_telegram(msg)
            append_event(f"DEGRADED  wifi={wifi_ms} wired={wired_ms} wan={wan_ms}\n{snapshot}")

        # ── DEGRADED → RECOVERED ──────────────────────────────────────────────
        elif degraded and not wifi_bad:
            dur = duration_s(event_start_ts, ts)
            msg = (
                f"✅ WiFi recovered\n"
                f"Duration: ~{dur}s  |  peak RTT: {event_peak:.0f}ms\n"
                f"WiFi: {wifi_ms:.0f}ms  |  WAN: {'LOSS' if wan_ms is None else f'{wan_ms:.0f}ms'}"
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
