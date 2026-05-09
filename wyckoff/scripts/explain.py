#!/usr/bin/env python3
"""Deep-dive explanation for a single ticker. Called by Hermes when the user asks
for more detail about a specific paper or wants to understand its Wyckoff analysis."""
from __future__ import annotations
import sys
import json
import os
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")

import data as market_data
import holdings as portfolio
import notifier

API_URL = "https://openrouter.ai/api/v1/chat/completions"

_SYSTEM = """You are a knowledgeable financial educator and Wyckoff method analyst.
The user wants a plain-language explanation of a specific stock or ETF and its current Wyckoff situation.

Your response must cover:
1. **What is this instrument?** — one sentence describing what the ETF/stock is (what index it tracks, what sector, who runs it, etc.)
2. **Current Wyckoff phase** — explain what phase it's in and what that means in plain terms
3. **Key events** — describe detected signals (Spring, UT, SOS, etc.) in plain language, not jargon
4. **What to watch for** — concrete price levels or behaviors that would confirm or invalidate the current thesis
5. **Recommendation explained** — why the recommendation is what it is, and what would change it

Write in plain, non-jargon Hebrew. Use short paragraphs. Do NOT use markdown headers — use Telegram HTML: <b>bold</b> for section labels, <i>italic</i> for emphasis. Keep the total response under 400 words."""


def explain(ticker: str):
    cfg_path = Path(__file__).parent.parent / "config.yaml"
    import yaml
    cfg = yaml.safe_load(cfg_path.read_text())
    lookback = cfg.get("llm", {}).get("lookback_days", 120)

    td = market_data.fetch_ohlcv(ticker.upper(), days=lookback)
    price = float(td.df["close"].iloc[-1])
    held = ticker.upper() in portfolio.load()
    label = f"{ticker.upper()} ({td.name})" if td.name != ticker.upper() else ticker.upper()
    context = "Currently HELD in portfolio." if held else "On watchlist (not held)."

    csv = td.df.to_csv()
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"Ticker: {label}\nCurrent price: {price:.2f} {td.currency}\n{context}\n\nOHLCV (last {len(td.df)} trading days):\n{csv}"},
    ]

    model = os.environ.get("WYCKOFF_LLM_MODEL", "anthropic/claude-sonnet-4-5")
    resp = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {os.environ.get('LLM_API_KEY') or os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
        },
        json={"model": model, "messages": messages, "temperature": 0.3, "max_tokens": 1024},
        timeout=90,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"].strip()
    notifier.send(f"🔍 <b>{label}</b> — ניתוח מפורט\n\n{text}")
    print(f"[explain] sent deep-dive for {ticker}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: explain.py <TICKER>")
        sys.exit(1)
    explain(sys.argv[1])
