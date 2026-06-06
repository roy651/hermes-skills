#!/usr/bin/env python3
"""Daily Wyckoff analysis — fetches data, runs LLM analysis, sends Telegram digest."""
from __future__ import annotations
import argparse
import html
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")

import yaml
import data as market_data
import analysis as wyckoff
import holdings as portfolio
import notifier
from prescreener import _get_spy_context

TZ = ZoneInfo("Asia/Jerusalem")

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


def _format_result(result: dict, holding: dict | None, price: float, name: str = "", currency: str = "USD") -> str:
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
    if holding:
        cost_str = f"{_sym}{holding['avg_cost']:.2f}"

    title = f"<b>{ticker}</b>"
    if name and name != ticker:
        title += f" <i>({html.escape(name)})</i>"

    if holding:
        qty = holding["qty"]
        cost = holding["avg_cost"]
        pnl_pct = (price - cost) / cost * 100
        pnl_sign = "+" if pnl_pct >= 0 else ""
        header = f"{title} · {qty} @ {cost_str} · {price_str} ({pnl_sign}{pnl_pct:.1f}%)"
    else:
        header = f"{title} · {price_str}"

    lines = [header]
    lines.append(f"  {phase_icon} {html.escape(phase.title())} ({html.escape(str(confidence))}) · {criteria}/9 criteria")
    if signals:
        lines.append(f"  Signals: {html.escape(', '.join(str(s) for s in signals))}")
    action_line = f"  {rec_label}"
    if entry:
        action_line += f" · Entry ${html.escape(str(entry))}"
    if stop:
        action_line += f" · Stop ${html.escape(str(stop))}"
    lines.append(action_line)
    if note:
        lines.append(f"  <i>{html.escape(str(note))}</i>")
    return "\n".join(lines)


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--section",
        choices=["portfolio", "watchlist", "all"],
        default="portfolio",
        help="Which section to run (default: portfolio — daily exit-watch)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the digest instead of sending to Telegram",
    )
    args = parser.parse_args()

    cfg_path = Path(__file__).parent.parent / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    watchlist = [t.upper() for t in cfg.get("watchlist", [])]
    lookback = cfg.get("llm", {}).get("lookback_days", 120)

    holdings = portfolio.load()

    if args.section == "portfolio":
        all_tickers = list(holdings.keys())
    elif args.section == "watchlist":
        all_tickers = [t for t in watchlist if t not in holdings]
    else:
        all_tickers = list(dict.fromkeys(list(holdings.keys()) + watchlist))

    date_str = datetime.now(tz=TZ).strftime("%Y-%m-%d")
    portfolio_lines = []
    watchlist_lines = []
    errors = []

    # Market regime context — grounds Wyckoff criteria 1 (broad trend) and 2 (rel strength)
    market_ctx = None
    if all_tickers:
        try:
            market_ctx = _get_spy_context()
        except Exception as e:
            print(f"[daily] SPY context fetch failed: {e}", file=sys.stderr)

    def _analyze(ticker: str):
        td = market_data.fetch_ohlcv(ticker, days=lookback)
        price = float(td.df["close"].iloc[-1])
        held = ticker in holdings
        mode = "exit" if held else "entry"
        result = wyckoff.analyze(ticker, td.df, held=held, name=td.name, mode=mode, market_ctx=market_ctx)
        block = _format_result(result, holdings.get(ticker), price, name=td.name, currency=td.currency)
        return held, block

    # Parallel (4 workers) — keep low so the local LLM proxy doesn't choke; preserve order
    ordered: dict[int, tuple[bool, str]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(_analyze, t): i for i, t in enumerate(all_tickers)}
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                ordered[i] = fut.result()
            except Exception as e:
                errors.append(f"{all_tickers[i]}: {e}")
                print(f"[daily] error on {all_tickers[i]}: {e}", file=sys.stderr)
    for i in range(len(all_tickers)):
        if i in ordered:
            held, block = ordered[i]
            (portfolio_lines if held else watchlist_lines).append(block)

    section_label = {
        "portfolio": "Portfolio — Exit Watch",
        "watchlist": "Watchlist",
        "all": "Daily",
    }[args.section]
    parts = [f"📊 <b>Wyckoff {section_label} — {date_str}</b>"]

    if portfolio_lines:
        parts.append("\n<b>Portfolio</b>")
        parts.extend(portfolio_lines)

    if watchlist_lines:
        parts.append("\n<b>Watchlist</b>")
        parts.extend(watchlist_lines)

    if errors:
        import html
        safe_errors = ", ".join(html.escape(str(e)) for e in errors)
        parts.append(f"\n<i>Errors: {safe_errors}</i>")

    msg = "\n".join(parts)
    if args.dry_run:
        print(msg)
    else:
        notifier.send(msg)
    print(f"[daily] {'(dry-run) ' if args.dry_run else ''}digest for {len(all_tickers)} tickers", file=sys.stderr)


if __name__ == "__main__":
    run()
