#!/usr/bin/env python3
"""Append-only execution ledger — the source of truth for actual entries/exits.

holdings.json is a *current-state snapshot* (qty + avg_cost, overwritten on every broker re-import)
and carries no dates; positions_state.entry_date is a tracker baseline, not a real fill date. So there
is no way to reconstruct "what did I buy/sell, when, at what price" after the fact. This ledger fixes
that going forward: each execution is appended as one JSON line with date/side/qty/price. It lives in
data/ (gitignored, runtime-only — it contains positions/prices) exactly like holdings.json.

Usage:
  trade_log.py add --date 2026-07-11 --ticker KMB --side buy  --qty 10 --price 112.41 [--note "starter"]
  trade_log.py review [--weeks 4]        # performance-since review of trades in the window (no-LLM)

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


def cmd_add(a) -> None:
    side = a.side.lower()
    if side not in ("buy", "sell"):
        sys.exit("side must be buy or sell")
    rec = {
        "date": a.date, "ticker": a.ticker.upper(), "side": side,
        "qty": a.qty, "price": a.price, "note": a.note or "",
        "logged_at": datetime.now(tz=TZ).isoformat(timespec="seconds"),
    }
    _append(rec)
    print(f"logged: {rec['date']} {side.upper()} {rec['qty']} {rec['ticker']} @ {rec['price']}"
          f"{'  # ' + rec['note'] if rec['note'] else ''}")


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
    pa.set_defaults(func=cmd_add)

    pr = sub.add_parser("review", help="performance-since review of recent fills")
    pr.add_argument("--weeks", type=int, default=4)
    pr.add_argument("--send", action="store_true", help="also post the digest to Telegram")
    pr.set_defaults(func=cmd_review)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
