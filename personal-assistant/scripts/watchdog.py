#!/usr/bin/env python3
"""Personal Assistant bot health check. Silent if ok, Telegram alert if unreachable."""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
USER_ID_ROY = os.environ.get("USER_ID_ROY", "391626535")
HEALTH_URL = "http://127.0.0.1:8766/health"


def _alert(msg: str):
    if not BOT_TOKEN:
        print(f"[watchdog] no token, cannot alert: {msg}", flush=True)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": USER_ID_ROY, "text": msg},
            timeout=10,
        )
    except Exception as e:
        print(f"[watchdog] alert send failed: {e}", flush=True)


try:
    resp = requests.get(HEALTH_URL, timeout=5)
    if resp.status_code == 200:
        print("[watchdog] ok", flush=True)
        sys.exit(0)
    print(f"[watchdog] unhealthy status: {resp.status_code}", flush=True)
    _alert("⚠️ Personal Assistant bot returned an unexpected health status")
except requests.exceptions.ConnectionError:
    print("[watchdog] connection refused — bot is down", flush=True)
    _alert("🔴 Personal Assistant bot is down (connection refused)")
except Exception as e:
    print(f"[watchdog] error: {e}", flush=True)
    _alert(f"⚠️ Personal Assistant bot health check failed: {e}")
