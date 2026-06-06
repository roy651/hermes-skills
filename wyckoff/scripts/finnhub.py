#!/usr/bin/env python3
"""Finnhub client — earnings calendar, market cap, company news, analyst consensus.

Free tier (https://finnhub.io/, ~60 req/min). Key in FINNHUB_API_KEY (~/.hermes/.env).
Uses plain `requests` — no extra dependency.
"""
from __future__ import annotations
import os
from datetime import date, timedelta
from pathlib import Path
import requests
from dotenv import load_dotenv

# Hermes env first, then skill-local .env as a dev fallback (does not override real env)
load_dotenv(Path.home() / ".hermes" / ".env")
load_dotenv(Path(__file__).parent.parent / ".env", override=False)

BASE = "https://finnhub.io/api/v1"
_KEY = os.environ.get("FINNHUB_API_KEY", "")


class FinnhubError(RuntimeError):
    pass


def _get(path: str, **params):
    if not _KEY:
        raise FinnhubError("FINNHUB_API_KEY not set")
    params["token"] = _KEY
    resp = requests.get(f"{BASE}{path}", params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def earnings_within(tickers: set[str], days: int = 14) -> set[str]:
    """Subset of `tickers` reporting earnings within the next `days` days (single API call)."""
    today = date.today()
    data = _get(
        "/calendar/earnings",
        **{"from": today.isoformat(), "to": (today + timedelta(days=days)).isoformat()},
    )
    reporting = {row.get("symbol") for row in (data or {}).get("earningsCalendar", [])}
    return {t for t in tickers if t in reporting}


def market_cap(ticker: str) -> float | None:
    """Market cap in USD (Finnhub reports it in millions). None if unavailable."""
    data = _get("/stock/profile2", symbol=ticker)
    mc = (data or {}).get("marketCapitalization")
    return float(mc) * 1e6 if mc else None


def company_news(ticker: str, days: int = 30, limit: int = 12) -> list[dict]:
    """Recent headlines: [{'headline', 'summary'}], newest first, capped at `limit`."""
    today = date.today()
    data = _get(
        "/company-news",
        symbol=ticker,
        **{"from": (today - timedelta(days=days)).isoformat(), "to": today.isoformat()},
    )
    rows = data or []
    return [{"headline": r.get("headline", ""), "summary": r.get("summary", "")} for r in rows[:limit]]


def analyst_consensus(ticker: str) -> str:
    """bullish | neutral | bearish | unknown, from the latest recommendation-trends period."""
    data = _get("/stock/recommendation", symbol=ticker)
    if not data:
        return "unknown"
    latest = data[0]
    buy = latest.get("strongBuy", 0) + latest.get("buy", 0)
    sell = latest.get("strongSell", 0) + latest.get("sell", 0)
    hold = latest.get("hold", 0)
    if buy + sell + hold == 0:
        return "unknown"
    if buy >= sell * 1.5 and buy > hold:
        return "bullish"
    if sell >= buy:
        return "bearish"
    return "neutral"
