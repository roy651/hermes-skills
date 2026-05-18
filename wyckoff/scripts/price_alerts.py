#!/usr/bin/env python3
"""Daily Wyckoff price alert scan — no LLM, fires only on significant moves (>3.5%)."""
from __future__ import annotations
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")

import data as market_data
import holdings as portfolio
import notifier

TZ = ZoneInfo("Asia/Jerusalem")
CANDIDATES_FILE = Path(__file__).parent.parent / "data" / "watchlist_candidates.json"
MOVE_THRESHOLD = 0.035  # 3.5%


def _get_candidates() -> list[str]:
    if not CANDIDATES_FILE.exists():
        return []
    try:
        doc = json.loads(CANDIDATES_FILE.read_text())
        return [c["ticker"] for c in doc.get("candidates", [])]
    except Exception:
        return []


def _check_ticker(ticker: str) -> dict | None:
    try:
        td = market_data.fetch_ohlcv(ticker, days=5)
        close = td.df["close"]
        if len(close) < 2:
            return None
        prev, curr = float(close.iloc[-2]), float(close.iloc[-1])
        pct_chg = (curr - prev) / prev
        if abs(pct_chg) >= MOVE_THRESHOLD:
            return {
                "ticker": ticker,
                "name": td.name,
                "price": curr,
                "pct_chg": pct_chg,
                "currency": td.currency,
            }
        return None
    except Exception as e:
        print(f"[price_alerts] skip {ticker}: {e}", file=sys.stderr)
        return None


def run():
    holdings_map = portfolio.load()
    candidates = _get_candidates()
    all_tickers = list(dict.fromkeys(list(holdings_map.keys()) + candidates))
    print(f"[price_alerts] scanning {len(all_tickers)} tickers...", file=sys.stderr)

    alerts: list[dict] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_check_ticker, t): t for t in all_tickers}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                alerts.append(r)

    if not alerts:
        print("[price_alerts] no significant movers — silent", file=sys.stderr)
        return

    alerts.sort(key=lambda x: abs(x["pct_chg"]), reverse=True)

    date_str = datetime.now(tz=TZ).strftime("%Y-%m-%d")
    lines = [f"⚡ <b>Wyckoff Price Alerts — {date_str}</b>",
             f"<i>Moves ≥{MOVE_THRESHOLD*100:.0f}% today</i>", ""]

    for a in alerts:
        ticker = a["ticker"]
        pct = a["pct_chg"] * 100
        pct_str = f"{'+' if pct > 0 else ''}{pct:.1f}%"
        emoji = "🟢" if pct > 0 else "🔴"
        _sym = {"USD": "$", "ILS": "₪"}.get(a["currency"], a["currency"] + " ")
        label = "📦 holding" if ticker in holdings_map else "👁 candidate"
        name_part = f" ({a['name']})" if a["name"] != ticker else ""
        lines.append(f"{emoji} <b>{ticker}</b>{name_part} · {_sym}{a['price']:.2f} · {pct_str} [{label}]")

    notifier.send("\n".join(lines))
    print(f"[price_alerts] sent {len(alerts)} alerts", file=sys.stderr)


if __name__ == "__main__":
    run()
