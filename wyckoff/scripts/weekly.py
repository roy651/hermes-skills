#!/usr/bin/env python3
"""Sunday Wyckoff weekly run: prescreener → full LLM on candidates + portfolio."""
from __future__ import annotations
import html
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")

import data as market_data
import analysis as wyckoff
import holdings as portfolio
import notifier
import news as news_validator
from prescreener import screen_universe, CANDIDATES_FILE, TOP_N

TZ = ZoneInfo("Asia/Jerusalem")
LOOKBACK_DAYS = 120
STRONG_RECS = {"buy", "add", "reduce", "sell"}

_PHASE_EMOJI = {
    "accumulation": "🟡",
    "markup": "✅",
    "distribution": "⚠️",
    "markdown": "🔴",
    "unclear": "⬜",
}

_REC_EMOJI = {
    "buy": "🟢 Buy",
    "add": "🟢 Add",
    "hold": "✅ Hold",
    "reduce": "🟠 Reduce",
    "sell": "🔴 Sell",
    "watch": "🔵 Watch",
    "pass": "⬜ Pass",
}

_FLAG_LABELS = {
    "off_high": "range",
    "above_ma200": "MA200✓",
    "atr_contraction": "ATR↓",
    "vol_contraction": "vol↓",
    "bb_squeeze": "squeeze",
}


def _format_result(
    result: dict,
    holding: dict | None,
    price: float,
    name: str = "",
    currency: str = "USD",
    news_info: dict | None = None,
) -> str:
    ticker = result["ticker"]
    phase = result.get("phase", "unclear")
    confidence = result.get("phase_confidence", "")
    criteria = result.get("criteria_met", "?")
    rec = result.get("recommendation", "")
    note = result.get("note", "")
    signals = result.get("active_signals", [])
    entry = result.get("entry_zone")
    stop = result.get("stop")

    phase_icon = _PHASE_EMOJI.get(phase, "⬜")
    rec_label = _REC_EMOJI.get(rec, rec)
    _sym = {"USD": "$", "ILS": "₪"}.get(currency, currency + " ")
    price_str = f"{_sym}{price:.2f}"

    title = f"<b>{ticker}</b>"
    if name and name != ticker:
        title += f" <i>({name})</i>"

    if holding:
        qty = holding["qty"]
        cost = holding["avg_cost"]
        pnl_pct = (price - cost) / cost * 100
        pnl_sign = "+" if pnl_pct >= 0 else ""
        cost_str = f"{_sym}{cost:.2f}"
        header = f"{title} · {qty} @ {cost_str} · {price_str} ({pnl_sign}{pnl_pct:.1f}%)"
    else:
        header = f"{title} · {price_str}"

    lines = [header]
    lines.append(f"  {phase_icon} {phase.title()} ({confidence}) · {criteria}/9 criteria")
    if signals:
        lines.append(f"  Signals: {', '.join(signals)}")
    action_line = f"  {rec_label}"
    if entry:
        action_line += f" · Entry ${entry}"
    if stop:
        action_line += f" · Stop ${stop}"
    lines.append(action_line)
    if note:
        lines.append(f"  <i>{note}</i>")

    if news_info:
        if not news_info.get("clean", True):
            flag = news_info.get("flag") or "unknown issue"
            lines.append(f"  ⚠️ NEWS FLAG: {html.escape(flag)}")
        consensus = news_info.get("analyst_consensus", "unknown")
        if consensus and consensus != "unknown":
            lines.append(f"  👥 Analysts: {html.escape(consensus)}")
        summary = news_info.get("summary", "")
        if summary:
            lines.append(f"  <i>📰 {html.escape(summary)}</i>")

    return "\n".join(lines)


def _analyze_one(ticker: str, holdings_map: dict) -> dict:
    """Fetch data, run Wyckoff analysis, optionally validate news. Returns a result bundle."""
    held = ticker in holdings_map
    td = market_data.fetch_ohlcv(ticker, days=LOOKBACK_DAYS)
    price = float(td.df["close"].iloc[-1])
    result = wyckoff.analyze(ticker, td.df, held=held, name=td.name)

    news_info = None
    rec = result.get("recommendation", "")
    if rec in STRONG_RECS:
        try:
            news_info = news_validator.validate(ticker, td.name, rec)
        except Exception as e:
            print(f"[weekly] news validation failed for {ticker}: {e}", file=sys.stderr)

    return {
        "ticker": ticker,
        "result": result,
        "holding": holdings_map.get(ticker),
        "price": price,
        "name": td.name,
        "currency": td.currency,
        "news_info": news_info,
    }


def _analyze_batch(tickers: list[str], holdings_map: dict, label: str = "") -> tuple[list[str], list[str]]:
    """Run Wyckoff analysis on a list of tickers in parallel (10 workers).

    Returns (formatted_blocks, error_strings).
    """
    blocks: list[tuple[int, str]] = []  # (original_index, block)
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=10) as pool:
        future_to_idx = {pool.submit(_analyze_one, t, holdings_map): i for i, t in enumerate(tickers)}
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            ticker = tickers[idx]
            try:
                bundle = fut.result()
                block = _format_result(
                    bundle["result"],
                    bundle["holding"],
                    bundle["price"],
                    name=bundle["name"],
                    currency=bundle["currency"],
                    news_info=bundle["news_info"],
                )
                blocks.append((idx, block))
            except Exception as e:
                errors.append(f"{ticker}: {e}")
                print(f"[weekly] error on {ticker} ({label}): {e}", file=sys.stderr)

    # Restore original order
    blocks.sort(key=lambda x: x[0])
    return [b for _, b in blocks], errors


def _send_prescreener_message(candidates: list[dict], date_str: str) -> None:
    lines = [
        f"📋 <b>Wyckoff Watchlist Candidates — {date_str}</b>",
        f"<i>{len(candidates)} candidates (≥3/5 criteria)</i>",
        "",
    ]
    for r in candidates:
        flags = [label for key, label in _FLAG_LABELS.items() if r["breakdown"].get(key)]
        name_part = f" ({r['name']})" if r["name"] != r["ticker"] else ""
        lines.append(
            f"<b>{r['ticker']}</b>{name_part} · ${r['price']} "
            f"· {r['pct_off_52w_high']:.0f}% off hi · {r['score']}/5 [{', '.join(flags)}]"
        )
    lines.append("")
    lines.append("<i>Add approved tickers via: manage.py watchlist-add TICKER</i>")
    notifier.send("\n".join(lines))


def run():
    date_str = datetime.now(tz=TZ).strftime("%Y-%m-%d")

    # Step 1: Prescreener
    print("[weekly] running prescreener...", file=sys.stderr)
    candidates = screen_universe()

    # Step 2: Send prescreener candidates list to Telegram
    _send_prescreener_message(candidates, date_str)
    print(f"[weekly] sent prescreener list ({len(candidates)} candidates)", file=sys.stderr)

    # Step 3: Full Wyckoff analysis on prescreener candidates
    candidate_tickers = [c["ticker"] for c in candidates]
    print(f"[weekly] running Wyckoff on {len(candidate_tickers)} candidates...", file=sys.stderr)
    # Candidates are not holdings — pass empty map so they show as watchlist entries
    candidate_blocks, candidate_errors = _analyze_batch(candidate_tickers, {}, label="candidates")

    # Send candidates Wyckoff report
    candidate_parts = [f"🔍 <b>Wyckoff Candidates Analysis — {date_str}</b>", ""]
    candidate_parts.extend(candidate_blocks)
    if candidate_errors:
        safe_errors = ", ".join(html.escape(str(e)) for e in candidate_errors)
        candidate_parts.append(f"\n<i>Errors: {safe_errors}</i>")
    notifier.send("\n".join(candidate_parts))
    print(f"[weekly] sent candidates Wyckoff report", file=sys.stderr)

    # Step 4: Full Wyckoff analysis on portfolio holdings
    holdings_map = portfolio.load()
    holding_tickers = list(holdings_map.keys())
    print(f"[weekly] running Wyckoff on {len(holding_tickers)} holdings...", file=sys.stderr)
    holdings_blocks, holdings_errors = _analyze_batch(holding_tickers, holdings_map, label="portfolio")

    # Step 5: Send portfolio Wyckoff report
    portfolio_parts = [f"📊 <b>Wyckoff Portfolio Analysis — {date_str}</b>", ""]
    portfolio_parts.extend(holdings_blocks)
    if holdings_errors:
        safe_errors = ", ".join(html.escape(str(e)) for e in holdings_errors)
        portfolio_parts.append(f"\n<i>Errors: {safe_errors}</i>")
    notifier.send("\n".join(portfolio_parts))
    print(f"[weekly] sent portfolio Wyckoff report", file=sys.stderr)

    total = len(candidate_tickers) + len(holding_tickers)
    print(f"[weekly] done — {total} tickers analyzed", file=sys.stderr)


if __name__ == "__main__":
    run()
