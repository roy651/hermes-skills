#!/usr/bin/env python3
"""Monthly bond-sleeve review — the thesis-level exit discipline for rate/formula-driven holdings.

Bonds/Treasury ETFs are (correctly) exempt from the Wyckoff trailing stop: their price is a
function of yields & duration, not a supply-demand trend, so a trailing stop mis-fires (it would
sell at the yield high on rate noise — see holdings.no_trailing_stop / stop_check.py). That leaves
them with NO mechanical exit. This job is their discipline instead: once a month it gathers price,
unrealised P/L, and the rate backdrop (5-year Treasury yield level & trend) and asks the LLM one
focused question per name — is the duration thesis intact: HOLD / TRIM / ADD — then posts to Telegram.

Usage:  bond_review.py [--dry-run]
"""
from __future__ import annotations

import re
import sys
import html
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")

import data as market_data
import holdings as portfolio
import analysis as wyckoff
import notifier

# The reference rate for a duration sleeve. ^FVX = CBOE 5-Year Treasury Note Yield index (percent).
_RATE_TICKER = "^FVX"

_SYSTEM = (
    "You are a fixed-income portfolio reviewer. You judge whether holding a bond / Treasury ETF "
    "position still makes sense given the rate environment. Do NOT use Wyckoff or chart patterns — "
    "a bond's price is a function of yields and duration, not a supply-demand trend. Weigh three "
    "things: (1) the rate/duration thesis (where are yields headed vs. this position's duration), "
    "(2) carry — the yield being earned while held, and (3) the recent trend in yields. Give ONE "
    "clear stance in CAPS — HOLD, TRIM, or ADD — followed by a two-to-three-sentence rationale. "
    "Be concise and decisive; no hedging essays."
)


def _rate_backdrop() -> tuple[str, dict]:
    """Latest 5yr Treasury yield + 1m/3m change in basis points. Returns (human_line, ctx)."""
    td = market_data.fetch_ohlcv(_RATE_TICKER, days=120)
    c = td.df["close"]
    now = float(c.iloc[-1])
    mo1 = float(c.iloc[-22]) if len(c) > 22 else float(c.iloc[0])
    mo3 = float(c.iloc[-63]) if len(c) > 63 else float(c.iloc[0])
    d1 = round((now - mo1) * 100)   # yield is in %, ×100 → basis points
    d3 = round((now - mo3) * 100)
    trend = "rising" if d1 > 3 else "falling" if d1 < -3 else "flat"
    line = (f"5yr Treasury yield: <b>{now:.2f}%</b> — {trend} "
            f"(1mo {d1:+d}bp, 3mo {d3:+d}bp)")
    ctx = f"5-year Treasury yield is {now:.2f}%, {trend} (1-month {d1:+d}bp, 3-month {d3:+d}bp)."
    return line, ctx


def _review_one(ticker: str, h: dict, rate_ctx: str) -> tuple[str, str]:
    """Returns (telegram_block, plain_note). One LLM call per name."""
    td = market_data.fetch_ohlcv(ticker, days=120)
    price = float(td.df["close"].iloc[-1])
    sym = {"USD": "$", "ILS": "₪"}.get(td.currency, td.currency + " ")
    cost_local = h["avg_cost"] / 100 if td.currency == "ILS" else h["avg_cost"]
    pnl = (price / cost_local - 1) * 100 if cost_local else 0.0
    label = f"{ticker} ({td.name})" if td.name and td.name != ticker else ticker

    user_parts = [
        f"Holding under review: {label} — a rate/duration-driven bond ETF (exempt from any "
        f"technical stop; its exit is a rate/thesis call).",
        f"Position: {h['qty']:g} units, avg cost {sym}{cost_local:g}, last price {sym}{price:g} "
        f"→ unrealised {pnl:+.1f}%.",
        f"Rate backdrop: {rate_ctx}",
        "Given the current rate path vs. this position's duration, is the thesis for holding it "
        "intact? Answer HOLD, TRIM, or ADD, then a 2-3 sentence rationale.",
    ]
    try:
        verdict = wyckoff._call_llm(_SYSTEM, user_parts, raw=True).strip()
    except Exception as e:
        verdict = f"(LLM unavailable: {e})"

    # The LLM answers in prose that often wraps the stance in markdown (**HOLD**). Telegram
    # is HTML parse_mode, so convert **bold** → <b>bold</b> after escaping (else asterisks show raw).
    verdict_html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(verdict))
    block = (f"<b>{ticker}</b> · {sym}{price:g}  (avg {sym}{cost_local:g}, {pnl:+.1f}%)\n"
             f"   {verdict_html}")
    return block, verdict


def run(dry_run: bool = False) -> None:
    held = portfolio.load()
    sleeve = {t: h for t, h in held.items() if portfolio.no_trailing_stop(h)}
    if not sleeve:
        print("[bond_review] no bond-sleeve holdings — nothing to review", file=sys.stderr)
        return

    wyckoff.reset_degradation()
    wyckoff.backend_warmup()   # refresh the Claude token before the batch; also records a fallback

    try:
        rate_line, rate_ctx = _rate_backdrop()
    except Exception as e:
        rate_line, rate_ctx = "5yr Treasury yield: (unavailable)", f"5-year Treasury yield unavailable ({e})."

    blocks = []
    for ticker, h in sleeve.items():
        try:
            block, _ = _review_one(ticker, h, rate_ctx)
            blocks.append(block)
        except Exception as e:
            blocks.append(f"<b>{ticker}</b> — review failed: {html.escape(str(e))}")
            print(f"[bond_review] {ticker}: {e}", file=sys.stderr)

    lines = ["🏦 <b>Wyckoff Bond-Sleeve Review</b> — monthly rate/duration check (no trailing stop applies)",
             rate_line, ""]
    lines += ["\n".join(("", b)) if i else b for i, b in enumerate(blocks)]

    degraded = wyckoff.degradation()
    if degraded:
        lines.append("")
        lines.append("⚠️ <b>DEGRADED</b> — Claude was unavailable; ran on "
                     f"<code>{html.escape(', '.join(sorted(degraded)))}</code>, not Claude. Re-run after re-auth.")
    lines.append("\n<i>Bonds have no mechanical stop — exit is a discretionary rate/thesis decision.</i>")

    msg = "\n".join(lines)
    print(msg) if dry_run else notifier.send(msg)
    print(f"[bond_review] reviewed {len(sleeve)} holding(s)", file=sys.stderr)


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
