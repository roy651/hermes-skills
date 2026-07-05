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
from datetime import datetime, timezone
from pathlib import Path


# ── config ────────────────────────────────────────────────────────────────────

TARGET           = "192.168.1.1"
WIFI_IFACE       = os.environ.get("WIFI_IFACE",           "wlp1s0")
WIRED_IFACE      = os.environ.get("WIRED_IFACE",          "eno1")
INTERVAL         = int(os.environ.get("INTERVAL",         "5"))
BAD_MS           = float(os.environ.get("BAD_MS",         "150"))
BAD_CONFIRM      = int(os.environ.get("BAD_CONFIRM",      "2"))
MID_POLL_INTERVAL = int(os.environ.get("MID_POLL_INTERVAL", "30"))  # seconds between mid-event polls

_HERE = Path(__file__).resolve().parent
_LOGS = _HERE.parent / "logs"
_LOGS.mkdir(exist_ok=True)

LOG_CSV    = _LOGS / "wifi_monitor.csv"
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

def ping_one(iface: str, timeout: int = 2) -> float | None:
    """Ping TARGET bound to iface.  Returns RTT ms, or None on loss/error."""
    try:
        r = subprocess.run(
            ["ping", "-I", iface, "-c", "1", "-W", str(timeout), TARGET],
            capture_output=True, text=True, timeout=timeout + 1,
        )
        if r.returncode == 0:
            m = re.search(r"time=(\d+(?:\.\d+)?)", r.stdout)
            if m:
                return float(m.group(1))
        return None
    except Exception:
        return None


# ── WiFi state snapshot ───────────────────────────────────────────────────────

def station_dump(iface: str) -> str:
    """Key fields from 'iw dev <iface> station dump': signal, bitrate, BSSID, connected time."""
    try:
        r = subprocess.run(
            ["iw", "dev", iface, "station", "dump"],
            capture_output=True, text=True, timeout=5,
        )
        wanted = ("signal:", "tx bitrate:", "rx bitrate:", "connected time:", "BSSID")
        lines = [l.strip() for l in r.stdout.splitlines() if any(k in l for k in wanted)]
        return "\n".join(lines) if lines else "(no station data)"
    except Exception:
        return "(iw unavailable)"


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


# ── radio channel scan + SNR ──────────────────────────────────────────────────

def current_channel_mhz(iface: str) -> int | None:
    """Current connected frequency in MHz from 'iw dev <iface> link'."""
    try:
        r = subprocess.run(["iw", "dev", iface, "link"], capture_output=True, text=True, timeout=5)
        m = re.search(r"freq:\s*(\d+)", r.stdout)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def snr_line(iface: str) -> str:
    """
    Returns a one-line SNR summary: signal + noise floor + derived SNR + quality label.

    Signal comes from 'iw station dump' (RSSI in dBm).
    Noise floor comes from 'iw dev <iface> survey dump' — the kernel tracks per-channel
    noise; the in-use channel entry has '[in use]' on its frequency line.

    SNR = signal_dBm − noise_floor_dBm.  Thresholds:
      ≥ 25 dB → excellent  |  15–25 → good  |  10–15 → fair  |  < 10 → poor
    """
    signal_dbm: float | None = None
    noise_dbm:  float | None = None

    try:
        r = subprocess.run(
            ["iw", "dev", iface, "station", "dump"],
            capture_output=True, text=True, timeout=5,
        )
        m = re.search(r"signal:\s*([-\d.]+)", r.stdout)
        if m:
            signal_dbm = float(m.group(1))
    except Exception:
        pass

    try:
        r = subprocess.run(
            ["iw", "dev", iface, "survey", "dump"],
            capture_output=True, text=True, timeout=5,
        )
        in_use_block = False
        for line in r.stdout.splitlines():
            if "frequency:" in line and "[in use]" in line:
                in_use_block = True
            elif "frequency:" in line:
                in_use_block = False
            if in_use_block and "noise:" in line:
                m = re.search(r"noise:\s*([-\d.]+)", line)
                if m:
                    noise_dbm = float(m.group(1))
                break
    except Exception:
        pass

    if signal_dbm is None:
        return "SNR: n/a (no signal data)"

    sig_str = f"signal {signal_dbm:.0f} dBm"

    if noise_dbm is None:
        return f"{sig_str} | noise floor: n/a | SNR: n/a"

    snr = signal_dbm - noise_dbm
    if snr >= 25:
        quality = "excellent"
    elif snr >= 15:
        quality = "good"
    elif snr >= 10:
        quality = "fair"
    else:
        quality = "poor"

    return f"{sig_str} | noise {noise_dbm:.0f} dBm | SNR {snr:.0f} dB ({quality})"


def radio_scan_summary(iface: str) -> str:
    """
    Dumps cached kernel scan results (no new scan triggered, no root needed).
    Returns a summary: our channel + APs on the same channel + top interferers.
    Useful to see channel congestion at the moment degradation begins.
    """
    try:
        r = subprocess.run(
            ["iw", "dev", iface, "scan", "dump"],
            capture_output=True, text=True, timeout=10,
        )
        if not r.stdout.strip():
            return "(no cached scan results — may not have scanned recently)"

        # Parse BSS blocks
        aps: list[dict] = []
        current: dict = {}
        for line in r.stdout.splitlines():
            if line.startswith("BSS "):
                if current:
                    aps.append(current)
                bssid = line.split()[1].split("(")[0].strip()
                current = {"bssid": bssid, "freq": None, "signal": None, "ssid": ""}
            elif current:
                line = line.strip()
                if line.startswith("freq:"):
                    m = re.search(r"freq:\s*(\d+)", line)
                    if m:
                        current["freq"] = int(m.group(1))
                elif line.startswith("signal:"):
                    m = re.search(r"signal:\s*([-\d.]+)", line)
                    if m:
                        current["signal"] = float(m.group(1))
                elif line.startswith("SSID:"):
                    current["ssid"] = line[5:].strip()
        if current:
            aps.append(current)

        if not aps:
            return "(scan dump returned data but could not parse BSS entries)"

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

    except Exception as e:
        return f"(scan dump failed: {e})"


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


def append_csv(ts: str, wifi_ms: float | None, wired_ms: float | None) -> None:
    row = f"{ts},{wifi_ms if wifi_ms is not None else 'LOSS'},{wired_ms if wired_ms is not None else 'LOSS'}\n"
    with open(LOG_CSV, "a") as f:
        f.write(row)


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


# ── main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    _load_dotenv()

    if not LOG_CSV.exists():
        with open(LOG_CSV, "w") as f:
            f.write("timestamp_utc,wifi_ms,wired_ms\n")

    log.info(
        f"wifi-monitor starting — target={TARGET}  "
        f"wifi={WIFI_IFACE}  wired={WIRED_IFACE}  "
        f"interval={INTERVAL}s  bad_threshold={BAD_MS}ms×{BAD_CONFIRM}  "
        f"mid_poll={MID_POLL_INTERVAL}s"
    )

    degraded       = False
    bad_streak     = 0
    event_start_ts: str | None  = None
    event_peak     = 0.0
    next_mid_poll  = 0.0  # monotonic time of next mid-event poll

    while True:
        ts       = utcnow()
        wifi_ms  = ping_one(WIFI_IFACE)
        wired_ms = ping_one(WIRED_IFACE)
        append_csv(ts, wifi_ms, wired_ms)

        wifi_bad   = wifi_ms is None or wifi_ms > BAD_MS
        bad_streak = bad_streak + 1 if wifi_bad else 0

        if degraded and wifi_ms is not None:
            event_peak = max(event_peak, wifi_ms)

        # ── OK → DEGRADED ─────────────────────────────────────────────────────
        if not degraded and bad_streak >= BAD_CONFIRM:
            degraded       = True
            event_start_ts = ts
            event_peak     = wifi_ms or 9999.0
            next_mid_poll  = time.monotonic() + MID_POLL_INTERVAL

            wired_note = (
                f"wired OK ({wired_ms:.0f}ms) → Tenda fault"
                if wired_ms is not None and wired_ms < BAD_MS
                else f"wired also degraded ({wired_ms}ms) → may be pfSense/upstream"
            )
            snapshot = event_start_snapshot(WIFI_IFACE)
            msg = (
                f"⚠️ WiFi degraded\n"
                f"WiFi: {'LOSS' if wifi_ms is None else f'{wifi_ms:.0f}ms'} | {wired_note}\n\n"
                f"{snapshot}"
            )
            log.warning(f"DEGRADED  wifi={wifi_ms} wired={wired_ms}")
            send_telegram(msg)
            append_event(f"DEGRADED  wifi={wifi_ms} wired={wired_ms}\n{snapshot}")

        # ── DEGRADED → RECOVERED ──────────────────────────────────────────────
        elif degraded and not wifi_bad:
            dur = duration_s(event_start_ts, ts)
            msg = (
                f"✅ WiFi recovered\n"
                f"Duration: ~{dur}s  |  peak RTT: {event_peak:.0f}ms\n"
                f"WiFi now: {wifi_ms:.0f}ms  |  wired: {wired_ms:.0f}ms"
            )
            log.info(f"RECOVERED  duration={dur}s peak={event_peak:.0f}ms")
            send_telegram(msg)
            append_event(f"RECOVERED  duration={dur}s peak={event_peak:.0f}ms wifi={wifi_ms} wired={wired_ms}")
            degraded       = False
            bad_streak     = 0
            event_start_ts = None
            event_peak     = 0.0
            next_mid_poll  = 0.0

        # ── mid-event poll (tracks BSSID/signal drift while degraded) ─────────
        elif degraded and time.monotonic() >= next_mid_poll:
            snap = mid_event_poll(WIFI_IFACE)
            append_event(f"MID-EVENT  wifi={wifi_ms} wired={wired_ms}\n{snap}")
            log.info(f"MID-EVENT  wifi={wifi_ms} wired={wired_ms}")
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
