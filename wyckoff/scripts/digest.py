"""Shared Telegram digest formatting for weekly.py and daily.py.

One `format_block` is the single source of truth for a per-ticker block, so the two
schedulers can't drift (the drift between two copies caused the earlier HTML-escape bug).
All dynamic/LLM-sourced text is html-escaped here.
"""
from __future__ import annotations
import html
import re

PHASE_EMOJI = {
    "accumulation": "🟡",
    "markup": "✅",
    "distribution": "⚠️",
    "markdown": "🔴",
    "unclear": "⬜",
}

REC_EMOJI = {
    "buy": "🟢 Buy",
    "add": "🟢 Add",
    "hold": "✅ Hold",
    "reduce": "🟠 Reduce",
    "sell": "🔴 Sell",
    "watch": "🔵 Watch",
    "pass": "⬜ Pass",
}


def entry_below_price(entry, price: float) -> bool:
    """True if the whole entry zone sits below the current price (a limit/pullback order)."""
    nums = re.findall(r"\d+\.?\d*", str(entry))
    if not nums:
        return False
    return price > max(float(x) for x in nums)


def format_block(
    result: dict,
    holding: dict | None,
    price: float,
    name: str = "",
    currency: str = "USD",
    news_info: dict | None = None,
    gate_action: bool = False,
) -> str:
    """One per-ticker digest block.

    gate_action=True  (weekly entry funnel): show Entry/Stop only for a buy/add rec, and flag
                       an entry zone that sits below current price as a limit/pullback order.
    gate_action=False (daily exit-watch):    show Entry/Stop unconditionally.
    """
    ticker = result["ticker"]
    phase = result.get("phase", "unclear")
    confidence = result.get("phase_confidence", "")
    criteria = result.get("criteria_met", "?")
    rec = result.get("recommendation", "")
    note = result.get("note", "")
    signals = result.get("active_signals", [])
    entry = result.get("entry_zone")
    stop = result.get("stop")

    phase_icon = PHASE_EMOJI.get(phase, "⬜")
    rec_label = REC_EMOJI.get(rec, rec)
    sym = {"USD": "$", "ILS": "₪"}.get(currency, currency + " ")
    price_str = f"{sym}{price:.2f}"

    title = f"<b>{ticker}</b>"
    if name and name != ticker:
        title += f" <i>({html.escape(name)})</i>"

    if holding:
        qty = holding["qty"]
        cost = holding["avg_cost"]
        pnl_pct = (price - cost) / cost * 100
        pnl_sign = "+" if pnl_pct >= 0 else ""
        cost_str = f"{sym}{cost:.2f}"
        header = f"{title} · {qty} @ {cost_str} · {price_str} ({pnl_sign}{pnl_pct:.1f}%)"
    else:
        header = f"{title} · {price_str}"

    lines = [header]
    lines.append(f"  {phase_icon} {html.escape(phase.title())} ({html.escape(str(confidence))}) · {criteria}/9 criteria")
    if signals:
        lines.append(f"  Signals: {html.escape(', '.join(str(s) for s in signals))}")

    action_line = f"  {rec_label}"
    if (not gate_action) or rec in ("buy", "add"):
        if entry:
            action_line += f" · Entry ${html.escape(str(entry))}"
            if gate_action and entry_below_price(entry, price):
                action_line += " ⏳ limit (await pullback)"
        if stop:
            action_line += f" · Stop ${html.escape(str(stop))}"
    lines.append(action_line)
    if note:
        lines.append(f"  <i>{html.escape(str(note))}</i>")

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
