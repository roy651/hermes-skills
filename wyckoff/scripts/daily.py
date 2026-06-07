#!/usr/bin/env python3
"""Daily Wyckoff analysis — fetches data, runs LLM analysis, sends Telegram digest."""
from __future__ import annotations
import argparse
import fcntl
import html
import sys
import os
import threading
import time
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
import digest
from prescreener import _get_spy_context

TZ = ZoneInfo("Asia/Jerusalem")

_LOCK_PATH = "/tmp/wyckoff_daily.lock"
_lock_fh = None              # kept alive for the process lifetime; flock releases when the fd closes
MAX_RUNTIME_SEC = 900        # 15 min hard ceiling — bounds a hang so the lock can't be held forever


def _acquire_singleton_lock() -> bool:
    """Non-blocking exclusive lock so a slow run can't be duplicated by an agent retry."""
    global _lock_fh
    _lock_fh = open(_LOCK_PATH, "w")
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (BlockingIOError, OSError):
        return False


def _start_watchdog(seconds: int) -> None:
    """Daemon timer: if the run hangs past `seconds`, alert + force-exit (releasing the lock)."""
    def _kill():
        time.sleep(seconds)
        print(f"[daily] watchdog: exceeded {seconds}s — force exit", file=sys.stderr)
        try:
            notifier.send(f"⚠️ <b>Wyckoff Exit-Watch</b> watchdog: run exceeded {seconds // 60} min and was killed.")
        except Exception:
            pass
        os._exit(2)
    threading.Thread(target=_kill, daemon=True).start()


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

    if not args.dry_run:
        if not _acquire_singleton_lock():
            print("[daily] another run already in progress — exiting (singleton lock)", file=sys.stderr)
            return
        _start_watchdog(MAX_RUNTIME_SEC)

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
        block = digest.format_block(result, holdings.get(ticker), price, name=td.name, currency=td.currency, gate_action=False)
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
    try:
        run()
    except Exception as e:
        # The job runs detached, so surface a hard failure to Telegram (not just the log).
        import traceback
        traceback.print_exc()
        if "--dry-run" not in sys.argv:
            try:
                notifier.send(f"⚠️ <b>Wyckoff Exit-Watch failed</b>: {html.escape(str(e)[:300])}")
            except Exception:
                pass
        sys.exit(1)
