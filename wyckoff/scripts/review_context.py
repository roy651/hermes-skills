#!/usr/bin/env python3
"""Gather the evidence chain for a portfolio brainstorm, deterministically (no LLM, no Telegram).

Prints, in the order the review should read them:
  1. the most recent archived entry / exit digests (the reports the review is *about*)
  2. prior brainstorm records, so a session builds on the last one instead of relitigating it
  3. recent conversation history — where the user's intent lives (deferred calls, strategic overrides)
  4. job health — a crashed job and a job with nothing to report look identical from the outside
  5. current holdings, watchlist and parked list

    python review_context.py                # default: 30d of transcript, 2 prior reviews
    python review_context.py --days 60 --reviews 3
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

SKILL = Path(__file__).parent.parent
STATE_DB = Path.home() / ".hermes" / "state.db"
TRADING_TERMS = ("wyckoff", "portfolio", "trim", "stop", "buy", "sell", "entry", "exit",
                 "watchlist", "hold", "position", "yield", "bond", "תיק", "מניה", "לקנות", "למכור")


def _rule(title: str) -> None:
    print(f"\n{'=' * 78}\n== {title}\n{'=' * 78}")


def latest_digests(n: int = 2) -> None:
    """The archived Telegram digests. notifier.send() writes these; --dry-run runs never reach it."""
    _rule("1. LATEST ARCHIVED DIGESTS")
    reports = sorted((SKILL / "data" / "reports").glob("*.txt"), reverse=True)
    if not reports:
        print("(none — digests are archived only on a REAL run; a --dry-run never calls notifier.send)")
        return
    entry = [r for r in reports if "entry" in r.name][:1]
    exit_ = [r for r in reports if "exit" in r.name][:1]
    for path in (entry + exit_) or reports[:n]:
        print(f"\n----- {path.name} -----")
        print(path.read_text().strip())


def prior_reviews(n: int) -> None:
    _rule(f"2. PRIOR BRAINSTORM RECORDS (latest {n})")
    records = sorted((SKILL / "data" / "reviews").glob("*.md"), reverse=True)
    if not records:
        print("(none yet — this is the first review)")
        return
    print("all records: " + ", ".join(r.stem for r in records) + "\n")
    for path in records[:n]:
        print(f"\n----- {path.name} -----")
        print(path.read_text().strip())


def recent_conversation(days: int) -> None:
    """Non-cron messages from the Hermes transcript — the user's intent, which no report captures."""
    _rule(f"3. CONVERSATION — last {days}d (trading-related, non-cron)")
    if not STATE_DB.exists():
        print(f"(no transcript db at {STATE_DB})")
        return
    since = (datetime.now() - timedelta(days=days)).timestamp()
    con = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT timestamp, role, content FROM messages "
        "WHERE timestamp >= ? AND session_id NOT LIKE 'cron_%' AND role IN ('user','assistant') "
        "ORDER BY timestamp", (since,)).fetchall()
    hits = 0
    for ts, role, content in rows:
        text = " ".join((content or "").split())
        if not any(term in text.lower() for term in TRADING_TERMS):
            continue
        hits += 1
        print(f"[{datetime.fromtimestamp(ts):%Y-%m-%d %H:%M}] {role:9s} {text[:400]}")
    print(f"\n({hits} trading-related messages of {len(rows)} total)")


def job_health() -> None:
    """A scheduled job that crashes writes a traceback nobody reads and sends nothing at all."""
    _rule("4. JOB HEALTH — tail of each log, tracebacks flagged")
    for log in sorted((SKILL / "logs").glob("*.log")):
        body = log.read_text(errors="replace")
        tail = [l for l in body.strip().split("\n") if l.strip()][-4:]
        broken = "Traceback" in body or "Error" in body
        print(f"\n--- {log.name} {'*** CONTAINS A TRACEBACK/ERROR ***' if broken else ''}")
        print("\n".join(f"    {l}" for l in tail))


def portfolio_state() -> None:
    _rule("5. CURRENT STATE")
    holdings = SKILL / "data" / "holdings.json"
    if holdings.exists():
        data = json.loads(holdings.read_text())
        print(f"holdings ({len(data)}):")
        for ticker, f in data.items():
            extra = {k: v for k, v in f.items() if k not in ("qty", "avg_cost")}
            print(f"  {ticker:12s} qty={f.get('qty')} avg_cost={f.get('avg_cost')}"
                  + (f"  {extra}" if extra else ""))
    config = SKILL / "config.yaml"
    if config.exists():
        print("\nwatchlist / parked / levels (from config.yaml):")
        keep = False
        for line in config.read_text().split("\n"):
            if line and not line.startswith((" ", "#")):
                keep = line.startswith(("watchlist", "parked"))
            if keep and line.strip() and not line.strip().startswith("#"):
                print(f"  {line}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="transcript lookback (default 30)")
    ap.add_argument("--reviews", type=int, default=2, help="prior review records to print (default 2)")
    args = ap.parse_args()

    print(f"PORTFOLIO REVIEW CONTEXT — generated {datetime.now():%Y-%m-%d %H:%M}")
    latest_digests()
    prior_reviews(args.reviews)
    recent_conversation(args.days)
    job_health()
    portfolio_state()


if __name__ == "__main__":
    sys.exit(main())
