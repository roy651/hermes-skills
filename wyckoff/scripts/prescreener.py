#!/usr/bin/env python3
"""Weekly Wyckoff prescreener — pure quantitative filters, no LLM.

Fetches S&P 500 + NASDAQ 100 + sector ETFs, scores each on 5 accumulation
criteria, and sends the top ~30 candidates to Telegram for approval.
"""
from __future__ import annotations
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import io
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")

import data as market_data
import notifier

TZ = ZoneInfo("Asia/Jerusalem")

SECTOR_ETFS = [
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLC", "XLY", "XLP", "XLU", "XLB", "XLRE",
    "IWM", "MDY", "IJR", "EFA", "EEM", "GLD", "SLV", "TLT", "HYG", "LQD",
]

CANDIDATES_FILE = Path(__file__).parent.parent / "data" / "watchlist_candidates.json"
TOP_N = 30
MIN_SCORE = 3


_WIKI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def _wiki_tables(url: str) -> list:
    resp = requests.get(url, headers=_WIKI_HEADERS, timeout=30)
    resp.raise_for_status()
    return pd.read_html(io.StringIO(resp.text))


def _get_universe() -> list[str]:
    tickers: list[str] = []

    try:
        tables = _wiki_tables("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        sp500 = tables[0]["Symbol"].tolist()
        tickers.extend(sp500)
        print(f"[prescreener] S&P 500: {len(sp500)} tickers", file=sys.stderr)
    except Exception as e:
        print(f"[prescreener] S&P 500 fetch failed: {e}", file=sys.stderr)

    try:
        tables = _wiki_tables("https://en.wikipedia.org/wiki/Nasdaq-100")
        ndx: list[str] = []
        for t in tables:
            if "Ticker" in t.columns and len(t) > 50:
                ndx = t["Ticker"].dropna().tolist()
                break
        if ndx:
            tickers.extend(ndx)
            print(f"[prescreener] NASDAQ 100: {len(ndx)} tickers", file=sys.stderr)
    except Exception as e:
        print(f"[prescreener] NASDAQ 100 fetch failed: {e}", file=sys.stderr)

    tickers.extend(SECTOR_ETFS)

    # Yahoo Finance uses hyphens (BRK-B), Wikipedia uses dots (BRK.B)
    cleaned = [t.replace(".", "-") for t in tickers]
    return list(dict.fromkeys(cleaned))  # deduplicate, preserve order


def _score(df: pd.DataFrame) -> tuple[int, dict[str, int]]:
    """Return (total_score, breakdown) — each criterion is 0 or 1."""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    price = float(close.iloc[-1])

    scores: dict[str, int] = {}

    # 1. Price 15–65% off 52-week high (not at highs, not in freefall)
    hi_52 = float(high.tail(252).max())
    pct_off = (hi_52 - price) / hi_52
    scores["off_high"] = 1 if 0.15 <= pct_off <= 0.65 else 0

    # 2. Not in deep markdown: price ≥ 90% of 200-day MA
    if len(close) >= 200:
        ma200 = float(close.tail(200).mean())
        scores["above_ma200"] = 1 if price >= 0.90 * ma200 else 0
    else:
        scores["above_ma200"] = 0

    # 3. ATR contraction vs its own 90-day median
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    atr_pct = atr14 / close
    valid = atr_pct.dropna()
    if len(valid) >= 60:
        scores["atr_contraction"] = 1 if float(valid.iloc[-1]) < float(valid.tail(90).median()) else 0
    else:
        scores["atr_contraction"] = 0

    # 4. Volume contraction: 20-day avg < 50-day avg
    if len(volume) >= 50:
        scores["vol_contraction"] = 1 if float(volume.tail(20).mean()) < float(volume.tail(50).mean()) else 0
    else:
        scores["vol_contraction"] = 0

    # 5. Bollinger Band squeeze: current BB width < 60th percentile of last 90 days
    sma20 = close.rolling(20).mean()
    bb_width = (2 * close.rolling(20).std()) / sma20
    bb_valid = bb_width.dropna()
    if len(bb_valid) >= 60:
        scores["bb_squeeze"] = 1 if float(bb_valid.iloc[-1]) < float(bb_valid.tail(90).quantile(0.60)) else 0
    else:
        scores["bb_squeeze"] = 0

    return sum(scores.values()), scores


def _fetch_and_score(ticker: str) -> dict | None:
    try:
        td = market_data.fetch_ohlcv(ticker, days=252)
        total, breakdown = _score(td.df)
        price = float(td.df["close"].iloc[-1])
        hi_52 = float(td.df["high"].tail(252).max())
        pct_off = (hi_52 - price) / hi_52 * 100
        return {
            "ticker": ticker,
            "name": td.name,
            "price": round(price, 2),
            "pct_off_52w_high": round(pct_off, 1),
            "score": total,
            "breakdown": breakdown,
        }
    except Exception as e:
        print(f"[prescreener] skip {ticker}: {e}", file=sys.stderr)
        return None


def run():
    universe = _get_universe()
    print(f"[prescreener] scanning {len(universe)} tickers…", file=sys.stderr)

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch_and_score, t): t for t in universe}
        for i, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            if r:
                results.append(r)
            if i % 100 == 0:
                print(f"[prescreener] {i}/{len(universe)} fetched", file=sys.stderr)

    # Sort: score desc, then pct off high asc (closer to support wins ties)
    results.sort(key=lambda x: (-x["score"], x["pct_off_52w_high"]))
    top = [r for r in results if r["score"] >= MIN_SCORE][:TOP_N]

    CANDIDATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES_FILE.write_text(json.dumps({
        "generated": datetime.now(tz=TZ).isoformat(),
        "total_scanned": len(results),
        "candidates": top,
    }, indent=2))

    date_str = datetime.now(tz=TZ).strftime("%Y-%m-%d")
    lines = [
        f"📋 <b>Wyckoff Watchlist Candidates — {date_str}</b>",
        f"<i>{len(top)} candidates from {len(results)} tickers scanned (≥{MIN_SCORE}/5 criteria)</i>",
        "",
    ]

    _flag_labels = {
        "off_high": "range",
        "above_ma200": "MA200✓",
        "atr_contraction": "ATR↓",
        "vol_contraction": "vol↓",
        "bb_squeeze": "squeeze",
    }

    for r in top:
        flags = [label for key, label in _flag_labels.items() if r["breakdown"].get(key)]
        name_part = f" ({r['name']})" if r["name"] != r["ticker"] else ""
        lines.append(
            f"<b>{r['ticker']}</b>{name_part} · ${r['price']} "
            f"· {r['pct_off_52w_high']:.0f}% off hi · {r['score']}/5 [{', '.join(flags)}]"
        )

    lines.append("")
    lines.append("<i>Add approved tickers via: manage.py watchlist-add TICKER</i>")

    notifier.send("\n".join(lines))
    print(f"[prescreener] sent {len(top)} candidates to Telegram", file=sys.stderr)


if __name__ == "__main__":
    run()
