#!/usr/bin/env python3
"""Append-only execution ledger — the source of truth for actual entries/exits.

holdings.json is a *current-state snapshot* (qty + avg_cost, overwritten on every broker re-import)
and carries no dates; positions_state.entry_date is a tracker baseline, not a real fill date. So there
is no way to reconstruct "what did I buy/sell, when, at what price" after the fact. This ledger fixes
that going forward: each execution is appended as one JSON line with date/side/qty/price. It lives in
data/ (gitignored, runtime-only — it contains positions/prices) exactly like holdings.json.

Usage:
  trade_log.py add --date 2026-07-11 --ticker KMB --side buy  --qty 10 --price 112.41 [--note "starter"]
  trade_log.py add ... --side sell [--cost 108.20] [--no-cash]
  trade_log.py review [--weeks 4]        # performance-since review of trades in the window (no-LLM)

Prices are in the trade currency: USD, or shekels (not agorot) for .TA tickers.

A sell records its cost basis, so the daily brief can count realized P/L after the position has
left holdings.json. Without --cost it is the ledger's own average cost, or holdings.json's when
the ledger has no buys for the ticker — so log a sell BEFORE removing the holding. Every add also
moves the cash balance (cash.py) by the fill's value; --no-cash skips that for backfills.

`add` is how the agent records a fill the user reports in chat ("bought/sold X @ Y"); `review` is the
periodic entry-execution check ("how did the last 4 weeks' entries do?"). Both are deterministic — no
Claude credits. `review` prints; pass --send to also post the digest to Telegram.
"""
from __future__ import annotations
import sys
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")

import data as market_data
import cash

TZ = ZoneInfo("Asia/Jerusalem")
LOG = Path(__file__).parent.parent / "data" / "trade_log.jsonl"
_SYM = {"USD": "$", "ILS": "₪"}


def _append(rec: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _load() -> list[dict]:
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def currency(ticker: str) -> str:
    return "ILS" if ticker.upper().endswith(".TA") else "USD"


def replay(trades: list[dict] | None = None) -> tuple[list[dict], dict]:
    """Walk the ledger per ticker on average cost. Returns (sells, open_lots).

    Each sell comes back with its cost per share (the recorded one, else the running ledger
    average, else None) and cycle_open: the date the position last went from flat to held, or
    None when the ledger never saw it bought. open_lots maps ticker -> {qty, avg, cycle_open}.
    Same-day buys sort ahead of sells, so a lot bought and sold on one day nets correctly."""
    trades = _load() if trades is None else trades
    ordered = sorted(enumerate(trades), key=lambda it: (it[1]["date"], it[1]["side"] != "buy", it[0]))
    lots: dict[str, dict] = {}
    sells = []
    for _, t in ordered:
        lot = lots.setdefault(t["ticker"], dict(qty=0.0, avg=None, cycle_open=None))
        q, px = float(t["qty"]), float(t["price"])
        if t["side"] == "buy":
            if lot["qty"] <= 0:
                lot.update(qty=0.0, avg=None, cycle_open=t["date"])
            lot["avg"] = px if lot["avg"] is None else (lot["qty"] * lot["avg"] + q * px) / (lot["qty"] + q)
            lot["qty"] += q
            continue
        cost = t.get("cost")
        if cost is None and lot["qty"] > 0:
            cost = lot["avg"]
        sells.append({**t, "cost": cost, "cycle_open": lot["cycle_open"] if lot["qty"] > 0 else None})
        lot["qty"] = max(lot["qty"] - q, 0.0)
    return sells, {k: v for k, v in lots.items() if v["qty"] > 0}


def _holdings_cost(ticker: str) -> float | None:
    import holdings as portfolio
    h = portfolio.load().get(ticker)
    if not h:
        return None
    return h["avg_cost"] / 100 if currency(ticker) == "ILS" else h["avg_cost"]   # agorot -> ₪


def cmd_add(a) -> None:
    side = a.side.lower()
    if side not in ("buy", "sell"):
        sys.exit("side must be buy or sell")
    ticker = a.ticker.upper()
    rec = {
        "date": a.date, "ticker": ticker, "side": side,
        "qty": a.qty, "price": a.price, "note": a.note or "",
        "logged_at": datetime.now(tz=TZ).isoformat(timespec="seconds"),
    }
    if side == "sell":
        cost = a.cost
        if cost is None:
            lot = replay()[1].get(ticker)
            cost = lot["avg"] if lot else _holdings_cost(ticker)
        if cost is None:
            print(f"[trade_log] no cost basis for {ticker} — realized P/L will not count this sell; "
                  f"re-log with --cost", file=sys.stderr)
        rec["cost"] = round(cost, 6) if cost is not None else None
    _append(rec)
    cost_txt = f"  (cost {rec['cost']:g})" if rec.get("cost") else ""
    note_txt = f"  # {rec['note']}" if rec["note"] else ""
    print(f"logged: {rec['date']} {side.upper()} {rec['qty']} {ticker} @ {rec['price']}{cost_txt}{note_txt}")

    if a.no_cash:
        return
    value = a.qty * a.price
    if currency(ticker) == "USD":
        import fx_rate
        value *= fx_rate.latest()
    delta = value if side == "sell" else -value
    c = cash.adjust(delta, f"{side} {a.qty:g} {ticker} @ {a.price:g}")
    if c is None:
        print("[trade_log] cash balance not set — run cash.py --set <ILS>", file=sys.stderr)
    else:
        print(f"cash: {delta:+,.0f} ILS -> ₪{c['ils']:,.0f}")


def _last_price(ticker: str) -> tuple[float | None, str]:
    try:
        td = market_data.fetch_ohlcv(ticker, days=5)
        return float(td.df["close"].iloc[-1]), td.currency
    except Exception as e:
        print(f"[trade_log] price fetch failed {ticker}: {e}", file=sys.stderr)
        return None, "USD"


def cmd_review(a) -> None:
    trades = _load()
    if not trades:
        print("[trade_log] ledger is empty — nothing to review "
              "(seed with `trade_log.py add ...`)", file=sys.stderr)
        return
    cutoff = (datetime.now(tz=TZ).date() - timedelta(weeks=a.weeks)).isoformat()
    window = [t for t in trades if t["date"] >= cutoff]
    if not window:
        print(f"[trade_log] no trades in the last {a.weeks} week(s) "
              f"(ledger has {len(trades)} older)", file=sys.stderr)
        return

    lines = [f"📒 <b>Entry-Execution Review — last {a.weeks}wk</b>",
             "<i>Actual logged fills vs. current price (no-LLM).</i>", ""]
    plain = [f"Entry-execution review — last {a.weeks} weeks:"]
    for t in sorted(window, key=lambda x: x["date"]):
        last, cur = _last_price(t["ticker"])
        sym = _SYM.get(cur, cur + " ")
        if last is None:
            lines.append(f"<b>{t['ticker']}</b> {t['side']} — price unavailable")
            continue
        move = (last / t["price"] - 1) * 100 if t["price"] else 0.0
        if t["side"] == "buy":
            tag = f"now {sym}{last:.2f} → {move:+.1f}% since entry"
        else:  # sell: positive move = you left gains on the table; negative = good exit
            tag = f"now {sym}{last:.2f} → {move:+.1f}% since exit " + ("(left gains)" if move > 1 else "(good exit)" if move < -1 else "(flat)")
        note = f" · {t['note']}" if t.get("note") else ""
        lines.append(f"<b>{t['ticker']}</b> · {t['date']} · {t['side'].upper()} "
                     f"{t['qty']}@{sym}{t['price']:g} — {tag}{note}")
        plain.append(f"  {t['date']} {t['side'].upper():4} {t['ticker']:6} {t['qty']}@{t['price']:g} -> {tag}")

    print("\n".join(plain))
    if a.send:
        import notifier
        notifier.send("\n".join(lines))
        print("[trade_log] posted review to Telegram", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(description="Append-only trade execution ledger")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("add", help="append one execution")
    pa.add_argument("--date", required=True, help="YYYY-MM-DD fill date")
    pa.add_argument("--ticker", required=True)
    pa.add_argument("--side", required=True, help="buy or sell")
    pa.add_argument("--qty", required=True, type=float)
    pa.add_argument("--price", required=True, type=float)
    pa.add_argument("--note", default="")
    pa.add_argument("--cost", type=float, help="sell only: cost per share, if not the ledger/holdings average")
    pa.add_argument("--no-cash", action="store_true", help="do not move the cash balance (backfills)")
    pa.set_defaults(func=cmd_add)

    pr = sub.add_parser("review", help="performance-since review of recent fills")
    pr.add_argument("--weeks", type=int, default=4)
    pr.add_argument("--send", action="store_true", help="also post the digest to Telegram")
    pr.set_defaults(func=cmd_review)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
