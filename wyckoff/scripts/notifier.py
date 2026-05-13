from __future__ import annotations
import os
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv(Path.home() / ".hermes" / ".env")


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
