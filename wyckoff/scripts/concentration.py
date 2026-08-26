#!/usr/bin/env python3
"""What the portfolio actually IS, as opposed to what moved today.

The daily movers panel narrates whichever names produced the biggest dollar swing, which in
practice means 2-4% satellites — while two positions carry ~70% of the book and are mentioned
only when they happen to win the dollar top-three. A report that never states its own
concentration lets the largest risk in the portfolio go unexamined indefinitely.

The headline number is the **effective number of positions** (inverse Herfindahl). Holding
fourteen names means nothing if two of them are most of the money: the effective count answers
"how many positions is this really?" and is usually far below the nominal one.

Usage:  concentration.py [--dry-run]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")

import data as market_data
import fx_rate
import israeli_fund
import holdings as portfolio
import notifier

BAR_WIDTH = 14
TOP_N = 6
HEAVY_PCT = 25.0        # a single position above this is worth naming every week


def _bar(pct: float, of: float) -> str:
    filled = int(round(BAR_WIDTH * pct / of)) if of else 0
    return "█" * max(filled, 1 if pct > 0 else 0) + "·" * (BAR_WIDTH - filled)


def collect() -> tuple[list[dict], float]:
    held = portfolio.load()
    # Same FX path portfolio_value.py uses: live rate when available, last-good rate persisted
    # by fx_rate otherwise. A second, divergent conversion would make the two reports disagree.
    try:
        usdils = float(market_data.fetch_ohlcv("USDILS=X", days=5).df["close"].iloc[-1])
    except Exception:
        usdils = fx_rate.latest()
    rows, total, unpriced = [], 0.0, []
    for ticker, h in held.items():
        try:
            # Israeli funds are not on Yahoo — exit.py already routes them here, and without
            # the same fallback this silently DROPPED ~22% of the book and inflated every other
            # weight by a quarter. A concentration report that omits a position is worse than
            # no report, so anything still unpriceable is named rather than quietly discarded.
            if h.get("fund_id") or h.get("globes_id"):
                td = israeli_fund.as_ticker_data(h.get("fund_id"), h.get("globes_id"), name=ticker)
            else:
                td = market_data.fetch_ohlcv(ticker, days=10)
            px = float(td.df["close"].iloc[-1])
            usd = px * h["qty"] * ((1.0 / usdils) if td.currency == "ILS" else 1.0)
        except Exception as e:
            print(f"[concentration] {ticker}: {e}", file=sys.stderr)
            unpriced.append(ticker)
            continue
        rows.append({"ticker": ticker, "usd": usd,
                     "strategic": bool(h.get("strategic") or portfolio.no_trailing_stop(h))})
        total += usd
    for r in rows:
        r["pct"] = r["usd"] / total * 100 if total else 0.0
    return sorted(rows, key=lambda r: -r["usd"]), total, unpriced


def build_section(as_section: bool = True) -> str:
    rows, total, unpriced = collect()
    if not rows:
        return "⚖️ <b>Concentration</b> — <i>no priceable holdings.</i>"

    top = rows[:TOP_N]
    widest = top[0]["pct"]
    lines = ["⚖️ <b>Concentration</b>", "<pre>"]
    for r in top:
        lines.append(f"{r['ticker'][:10]:<10}{r['pct']:>5.1f}%  {_bar(r['pct'], widest)}")
    rest = rows[TOP_N:]
    if rest:
        lines.append(f"{'other ' + str(len(rest)):<10}{sum(r['pct'] for r in rest):>5.1f}%")
    lines.append("</pre>")

    # Effective N: 1 / sum(weight^2). Fourteen names can behave like three.
    eff = 1 / sum((r["pct"] / 100) ** 2 for r in rows) if total else 0
    top2 = sum(r["pct"] for r in rows[:2])
    core = sum(r["pct"] for r in rows if r["strategic"])
    lines.append(f"<b>{len(rows)}</b> positions, but an effective <b>{eff:.1f}</b> "
                 f"— top two are <b>{top2:.0f}%</b> of the book.")
    if core:
        lines.append(f"<i>{core:.0f}% is flagged strategic / no-trailing-stop — deliberate ballast, "
                     f"not drift.</i>")
    if unpriced:
        lines.append(f"⚠️ <b>{', '.join(unpriced)}</b> could not be priced and is EXCLUDED — "
                     f"every weight above is overstated until this is fixed.")
    heavy = [r for r in rows if r["pct"] >= HEAVY_PCT and not r["strategic"]]
    if heavy:
        lines.append("⚠️ <b>" + ", ".join(r["ticker"] for r in heavy) +
                     f"</b> above {HEAVY_PCT:.0f}% without a strategic flag — size by intent, "
                     f"not by accident.")
    return "\n".join(lines)


if __name__ == "__main__":
    msg = build_section()
    print(msg) if "--dry-run" in sys.argv else notifier.send(msg)
