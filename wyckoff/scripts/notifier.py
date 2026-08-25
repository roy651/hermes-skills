from __future__ import annotations
import os
import re
import socket
import sys
from datetime import datetime
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
    # Log the id Telegram assigned + which bot sent it. A digest arriving twice is otherwise
    # unattributable: one id here but two messages in the chat means the second copy came from
    # somewhere else (another host holding this token), not from a double send on this box.
    try:
        print(f"[notifier] sent message_id={resp.json()['result']['message_id']} "
              f"chat={chat_id} bot={token.split(':')[0]}")
    except (ValueError, KeyError):
        pass


# Digests otherwise exist only inside Telegram, so a later review has nothing to read back. Archive
# every outbound message here — the one choke point every digest passes through. Gitignored (data/).
_ARCHIVE = Path(__file__).parent.parent / "data" / "reports"


def _archive(text: str) -> None:
    try:
        _ARCHIVE.mkdir(parents=True, exist_ok=True)
        headline = re.sub(r"<[^>]+>", "", text.split("\n")[0])
        slug = re.sub(r"[^a-z0-9]+", "-", headline.lower()).strip("-")[:40] or "digest"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (_ARCHIVE / f"{stamp}-{slug}.txt").write_text(text)
    except OSError as e:                      # a full/read-only disk must never block the alert itself
        print(f"[notifier] archive failed: {e}", file=sys.stderr)


def send(text: str) -> None:
    _archive(text)
    # WYCKOFF_SILENT lets a producer keep writing to the archive while posting nothing. The
    # weekly digest reads those archives and delivers one consolidated message, so the slow
    # LLM jobs that feed it run unchanged and simply stop double-posting.
    if os.environ.get("WYCKOFF_SILENT") == "1":
        print(f"[notifier] silent mode — archived {len(text)} chars, not sent", file=sys.stderr)
        return
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
