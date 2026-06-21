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


def format_managed_block(result: dict, holding: dict, price: float, engine: dict,
                         name: str = "", currency: str = "USD") -> str:
    """Exit-watch block where the deterministic engine (risk + deterioration + ladder) DECIDES the
    action and the LLM read only supplies the phase label + a one-line narrative.
    `engine` = {"risk": <risk.assess>, "det": <deterioration_score>, "ladder": <ladder.recommend>}.
    """
    rk, det_a, lad = engine["risk"], engine["det"], engine["ladder"]
    ticker = result["ticker"]
    phase = result.get("phase", "unclear")
    confidence = result.get("phase_confidence", "")
    note = result.get("note", "")
    sym = {"USD": "$", "ILS": "₪"}.get(currency, currency + " ")
    qty, cost = holding["qty"], holding["avg_cost"]
    pnl_pct = (price - cost) / cost * 100 if cost else 0.0
    psign = "+" if pnl_pct >= 0 else ""

    title = f"<b>{ticker}</b>"
    if name and name != ticker:
        title += f" <i>({html.escape(name)})</i>"
    header = f"{title} · {qty} @ {sym}{cost:.2f} · {sym}{price:.2f} ({psign}{pnl_pct:.1f}%)"

    action = lad["action"]
    a_emoji = ("🟢" if action.startswith("ADD") else "🔴" if action.startswith("EXIT")
               else "🟠" if action.startswith("TRIM") else "✅")
    delta = lad["delta_qty"]
    delta_str = f" ({'buy' if delta > 0 else 'sell'} {abs(round(delta))})" if delta else ""

    lines = [header]
    lines.append(f"  {PHASE_EMOJI.get(phase, '⬜')} {html.escape(phase.title())} "
                 f"({html.escape(str(confidence))}) · exit {det_a['score']}/9")
    if det_a["signals"]:
        lines.append(f"  Signals: {html.escape(', '.join(det_a['signals']))}")
    lines.append(f"  {a_emoji} <b>{html.escape(action)}</b>{delta_str} · "
                 f"Stop {sym}{rk['stop']} ({rk['distance_pct']}% away) · {lad['pos_pct']}% of port")
    if note:
        lines.append(f"  <i>{html.escape(str(note))}</i>")
    return "\n".join(lines)
