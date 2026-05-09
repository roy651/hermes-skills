#!/usr/bin/env python3
"""Daily Wyckoff analysis — fetches data, runs LLM analysis, sends Telegram digest."""
from __future__ import annotations
import sys
import os
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


def _format_result(result: dict, holding: dict | None, price: float) -> str:
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

    if holding:
        qty = holding["qty"]
        cost = holding["avg_cost"]
        pnl_pct = (price - cost) / cost * 100
        pnl_sign = "+" if pnl_pct >= 0 else ""
        header = f"<b>{ticker}</b> · {qty} @ ${cost:.2f} · ${price:.2f} ({pnl_sign}{pnl_pct:.1f}%)"
    else:
        header = f"<b>{ticker}</b> · ${price:.2f}"

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
    return "\n".join(lines)


def run():
    cfg_path = Path(__file__).parent.parent / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    watchlist = [t.upper() for t in cfg.get("watchlist", [])]
    lookback = cfg.get("llm", {}).get("lookback_days", 120)

    holdings = portfolio.load()
    all_tickers = list(dict.fromkeys(list(holdings.keys()) + watchlist))

    date_str = datetime.now(tz=TZ).strftime("%Y-%m-%d")
    portfolio_lines = []
    watchlist_lines = []
    errors = []

    for ticker in all_tickers:
        try:
            df = market_data.fetch_ohlcv(ticker, days=lookback)
            price = float(df["close"].iloc[-1])
            held = ticker in holdings
            result = wyckoff.analyze(ticker, df, held=held)
            block = _format_result(result, holdings.get(ticker), price)
            if held:
                portfolio_lines.append(block)
            else:
                watchlist_lines.append(block)
        except Exception as e:
            errors.append(f"{ticker}: {e}")
            print(f"[daily] error on {ticker}: {e}", file=sys.stderr)

    parts = [f"📊 <b>Wyckoff Daily — {date_str}</b>"]

    if portfolio_lines:
        parts.append("\n<b>Portfolio</b>")
        parts.extend(portfolio_lines)

    if watchlist_lines:
        parts.append("\n<b>Watchlist</b>")
        parts.extend(watchlist_lines)

    if errors:
        parts.append(f"\n<i>Errors: {', '.join(errors)}</i>")

    notifier.send("\n".join(parts))
    print(f"[daily] sent digest for {len(all_tickers)} tickers", file=sys.stderr)


if __name__ == "__main__":
    run()
