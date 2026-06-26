from __future__ import annotations
import os
import socket
from pathlib import Path
import requests
import urllib3.util.connection as _urllib3_conn
from dotenv import load_dotenv

load_dotenv(Path.home() / ".hermes" / ".env")

# api.telegram.org publishes both A and AAAA records, but this host's WiFi advertises an IPv6 default
# route with no working IPv6 egress. requests/urllib3 (unlike curl, which does Happy-Eyeballs) tries the
# IPv6 address and hangs until timeout. Force IPv4-only DNS resolution. Harmless if IPv6 egress is later
# restored — IPv4 to Telegram works either way.
_urllib3_conn.allowed_gai_family = lambda: socket.AF_INET


_MAX = 4096


def _post(token: str, chat_id: int, text: str) -> None:
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )
    resp.raise_for_status()


def send(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ["TELEGRAM_TOKEN"]
    chat_id = int(os.environ.get("TELEGRAM_CHAT_ID", "391626535"))
    if len(text) <= _MAX:
        _post(token, chat_id, text)
        return
    # Split on double-newlines (between ticker blocks) to avoid cutting mid-block
    chunks, current = [], []
    for line in text.split("\n"):
        if sum(len(l) + 1 for l in current) + len(line) + 1 > _MAX:
            _post(token, chat_id, "\n".join(current))
            current = []
        current.append(line)
    if current:
        _post(token, chat_id, "\n".join(current))
