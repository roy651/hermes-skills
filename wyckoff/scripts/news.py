#!/usr/bin/env python3
"""News / fundamental validation for Wyckoff recommendations.

Pulls recent headlines + analyst consensus from Finnhub, then asks the local claude-proxy
LLM whether any corporate event (M&A, regulatory, bankruptcy, severe miss) would invalidate
the technical signal. Replaces the previous Perplexity Sonar dependency.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv(Path.home() / ".hermes" / ".env")

import finnhub

API_URL = os.environ.get("LLM_API_URL", "http://localhost:8765/v1/chat/completions")
NEWS_MODEL = os.environ.get("WYCKOFF_NEWS_MODEL", "claude-haiku-4-5")


def validate(ticker: str, name: str, recommendation: str) -> dict:
    """Returns {"clean": bool, "flag": str|None, "analyst_consensus": str, "summary": str}."""
    try:
        headlines = finnhub.company_news(ticker, days=30)
    except Exception:
        headlines = []
    try:
        consensus = finnhub.analyst_consensus(ticker)
    except Exception:
        consensus = "unknown"

    if not headlines:
        return {
            "clean": True,
            "flag": None,
            "analyst_consensus": consensus,
            "summary": "No recent news found.",
        }

    news_block = "\n".join(f"- {h['headline']}" for h in headlines if h["headline"])
    prompt = (
        f"Stock {ticker} ({name}). A Wyckoff technical analysis recommends: {recommendation.upper()}.\n\n"
        f"Recent headlines (past 30 days):\n{news_block}\n\n"
        f"Does any of this contain a corporate event that would INVALIDATE the {recommendation} signal "
        f"— pending merger/acquisition/buyout, going-private, major regulatory action (FDA/SEC/DOJ), "
        f"bankruptcy, or a severe earnings miss?\n\n"
        f"Respond ONLY with valid JSON, no markdown:\n"
        f'{{"clean": true, "flag": null, "summary": "1-2 sentence news context"}}\n'
        f"Set clean=false and describe the flag if such an event exists."
    )

    resp = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {os.environ.get('LLM_API_KEY', 'local')}",
            "Content-Type": "application/json",
        },
        json={"model": NEWS_MODEL, "messages": [{"role": "user", "content": prompt}],
              "temperature": 0, "max_tokens": 300},
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = {"clean": True, "flag": None, "summary": text[:200]}

    parsed.setdefault("clean", True)
    parsed.setdefault("flag", None)
    parsed.setdefault("summary", "")
    parsed["analyst_consensus"] = consensus
    return parsed
