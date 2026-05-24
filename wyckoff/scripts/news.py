#!/usr/bin/env python3
"""News validation for Wyckoff recommendations — checks for M&A, regulatory events, analyst consensus."""
from __future__ import annotations
import json
import os
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path.home() / ".hermes" / ".env")

API_URL = os.environ.get("WYCKOFF_NEWS_API_URL", "https://openrouter.ai/api/v1/chat/completions")
NEWS_MODEL = os.environ.get("WYCKOFF_NEWS_MODEL", "perplexity/sonar")


def validate(ticker: str, name: str, recommendation: str) -> dict:
    """
    Validate a strong recommendation against recent news.
    Returns: {"clean": bool, "flag": str | None, "analyst_consensus": str, "summary": str}
    """
    prompt = (
        f"For stock {ticker} ({name}), a Wyckoff technical analysis recommends: {recommendation.upper()}.\n\n"
        f"Search for news in the past 60 days about {ticker} that might change this signal:\n"
        f"- Pending merger, acquisition, or buyout\n"
        f"- Going-private transaction or delisting\n"
        f"- Major regulatory action (FDA, SEC, DOJ)\n"
        f"- Bankruptcy filing or severe earnings miss\n"
        f"- Analyst consensus (are most analysts bullish, neutral, or bearish?)\n\n"
        f"Respond ONLY with valid JSON, no markdown:\n"
        f'{{"clean": true, "flag": null, "analyst_consensus": "bullish|neutral|bearish|unknown", "summary": "1-2 sentence news context"}}\n'
        f"Set clean=false and describe the flag if there is a corporate event that would invalidate the {recommendation} signal."
    )

    messages = [{"role": "user", "content": prompt}]
    resp = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {os.environ.get('WYCKOFF_NEWS_API_KEY') or os.environ.get('LLM_API_KEY', 'local')}",
            "Content-Type": "application/json",
        },
        json={"model": NEWS_MODEL, "messages": messages, "temperature": 0, "max_tokens": 300},
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
        return json.loads(text)
    except json.JSONDecodeError:
        return {"clean": True, "flag": None, "analyst_consensus": "unknown", "summary": text[:200]}
