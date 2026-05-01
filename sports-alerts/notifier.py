from __future__ import annotations
import os
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv(Path.home() / ".hermes" / ".env")


def send(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ["TELEGRAM_TOKEN"]
    chat_id = int(os.environ.get("TELEGRAM_CHAT_ID", "391626535"))
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )
    resp.raise_for_status()
