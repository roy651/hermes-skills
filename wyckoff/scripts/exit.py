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
import risk
import deterioration
import ladder
import events
import finnhub
import reddit
from prescreener import _get_spy_context

TZ = ZoneInfo("Asia/Jerusalem")

_LOCK_PATH = "/tmp/wyckoff_daily.lock"
_lock_fh = None              # kept alive for the process lifetime; flock releases when the fd closes
MAX_RUNTIME_SEC = 1500       # 25 min hard ceiling — bounds a hang so the lock can't be held forever


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


def _validate_voted(verdict_fn, *args, **kwargs) -> dict:
    """One validation; escalate to best-of-3 ONLY when it flags (a lone flag on a borderline name is
    the coin-flip we saw run-to-run). Majority wins; a split vote is tagged 'contested' so the name
    reads as genuinely ambiguous rather than flip-flopping. Confirms/unavailable stay at one call —
    so the extra cost lands only on the handful of names that are actually contested."""
    first = verdict_fn(*args, **kwargs)
    if not first or first.get("valid") is not False:
        return first
    votes = [first] + [verdict_fn(*args, **kwargs) for _ in range(2)]
    flags = sum(1 for v in votes if v and v.get("valid") is False)
    confirms = sum(1 for v in votes if v and v.get("valid") is True)
    if flags + confirms < 2:
        return first                                   # not enough valid votes — keep the flag
    contested = bool(flags and confirms)
    if flags >= confirms:                              # majority (a tie favours the cautious flag)
        note = next((v.get("note", "") for v in votes if v and v.get("valid") is False), "")
        return {"valid": False, "note": ("(contested) " if contested else "") + note}
    note = next((v.get("note", "") for v in votes if v and v.get("valid") is True), "")
    return {"valid": True, "note": "(contested) " + note}   # the flag was outvoted — a coin-flip


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
    watchlist_lines = []
    errors = []

    # Market regime context — grounds Wyckoff criteria 1 (broad trend) and 2 (rel strength)
    market_ctx = None
    if all_tickers:
        try:
            market_ctx = _get_spy_context()
        except Exception as e:
            print(f"[daily] SPY context fetch failed: {e}", file=sys.stderr)
    if market_ctx is not None:
        try:
            _spy = market_data.fetch_ohlcv("SPY", days=lookback).df["close"]
            market_ctx["spy_window_return"] = float(_spy.iloc[-1] / _spy.iloc[0] - 1)
        except Exception as e:
            print(f"[daily] SPY window-return fetch failed: {e}", file=sys.stderr)

    # 1. Fetch OHLCV for everything in parallel (network-bound)
    data: dict = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(market_data.fetch_ohlcv, t, lookback): t for t in all_tickers}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                data[t] = fut.result()
            except Exception as e:
                errors.append(f"{t}: {e}")
                print(f"[daily] fetch failed for {t}: {e}", file=sys.stderr)

    # USD/ILS rate to normalise ILS holdings for the portfolio-value + concentration math
    usdils = 3.7
    try:
        usdils = float(market_data.fetch_ohlcv("USDILS=X", days=5).df["close"].iloc[-1])
    except Exception as e:
        print(f"[daily] USDILS fetch failed, using {usdils}: {e}", file=sys.stderr)

    def _to_usd(cur: str) -> float:
        return (1.0 / usdils) if cur == "ILS" else 1.0

    held_tickers = [t for t in all_tickers if t in holdings and t in data]
    watch_tickers = [t for t in all_tickers if t not in holdings and t in data]

    total_value_usd = sum(
        holdings[t]["qty"] * float(data[t].df["close"].iloc[-1]) * _to_usd(data[t].currency)
        for t in held_tickers
    )

    # 2. Deterministic engine for held positions (sequential — fast, owns the shared state)
    state = risk.load_state()
    engines: dict = {}
    for t in held_tickers:
        td = data[t]
        price = float(td.df["close"].iloc[-1])
        h = holdings[t]
        cost_local = h["avg_cost"] / 100 if td.currency == "ILS" else h["avg_cost"]
        loss_pct = (price / cost_local - 1) if cost_local else None
        rk = risk.assess(t, td.df, h["qty"], state=state)
        ds = deterioration.deterioration_score(td.df, market_ctx, loss_pct=loss_pct)
        evs = events.detect_events(td.df)
        # Ratchet follows EXECUTION, not past advice: derive the scale-out stage from the actual holding
        # vs baseline (qty/baseline), so a recomputed (or once-buggy) recommendation can't stick.
        baseline = rk["baseline_qty"] or h["qty"]
        ratio = h["qty"] / baseline if baseline else 1.0
        executed_stage = 2 if ratio <= 0.625 else 1 if ratio <= 0.875 else 0
        rec = ladder.recommend(
            qty=h["qty"], price=price * _to_usd(td.currency), portfolio_value=total_value_usd,
            is_core=(t == "DGRO"), det_score=ds["score"], stop_hit=rk["stop_hit"],
            max_stage=executed_stage, baseline_qty=baseline,
            has_entry_event=events.has_entry_event(evs), has_structural=ds["has_structural"],
            established_markdown=ds["established_markdown"],
        )
        state[t]["max_stage"] = executed_stage          # reflects executed scale-out, not the recommendation
        engines[t] = {"risk": rk, "det": ds, "ladder": rec}
    if not args.dry_run:
        risk.save_state(state)

    # Real catalysts to ground the validator (entry funnel already uses finnhub; ex-div is paid-tier, so
    # we pass earnings-soon + recent headlines and let the validator spot ex-div/splits/M&A in them).
    earnings_soon: set = set()
    try:
        earnings_soon = finnhub.earnings_within(set(held_tickers), days=14)
    except Exception as e:
        print(f"[daily] earnings calendar unavailable: {e}", file=sys.stderr)

    # 3. LLM in parallel: VALIDATE each held verdict; ENTRY-analyse each watchlist name
    def _llm(t: str):
        td = data[t]
        if t in engines:
            e = engines[t]
            if e["ladder"]["action"] == "HOLD":      # skip LLM validation on holds — keeps the run well under the watchdog
                return t, {"valid": None, "note": ""}
            verdict = {"action": e["ladder"]["action"], "score": e["det"]["score"],
                       "signals": e["det"]["signals"], "stop": e["risk"]["stop"],
                       "qty": holdings[t]["qty"], "price": round(float(td.df["close"].iloc[-1]), 2)}
            catalyst = {"earnings_soon": t in earnings_soon, "headlines": []}
            try:
                catalyst["headlines"] = [n["headline"] for n in finnhub.company_news(t, days=21, limit=5)]
            except Exception:
                pass
            return t, _validate_voted(wyckoff.validate, t, td.df, td.name, verdict, market_ctx, catalyst=catalyst)
        try:
            return t, wyckoff.analyze(t, td.df, held=False, name=td.name, mode="entry", market_ctx=market_ctx)
        except Exception as e:
            print(f"[daily] entry analyze failed for {t}: {e}", file=sys.stderr)
            return t, {"ticker": t, "phase": "unclear", "note": "(read unavailable)"}

    # Warm the proxy (refresh the Claude token while only one call is in flight) + start degradation
    # tracking, BEFORE the concurrent batch — a batch racing an expired token silently drops to qwen.
    wyckoff.reset_degradation()
    _hc_ok, _hc_backend = wyckoff.backend_warmup()
    if not _hc_ok:
        print(f"[daily] ⚠️ backend not Claude at warmup: {_hc_backend}", file=sys.stderr)

    llm_out: dict = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(_llm, t): t for t in held_tickers + watch_tickers}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                tt, out = fut.result()
                llm_out[tt] = out
            except Exception as e:
                errors.append(f"{t}: {e}")
                print(f"[daily] llm error on {t}: {e}", file=sys.stderr)

    # Reddit mention data — annotation layer only, fetched after LLM (non-blocking; failure is silent)
    rd_cfg = cfg.get("reddit") or {}
    rd_threshold = float(rd_cfg.get("velocity_warn_threshold", 2.0))
    reddit_data: dict = {}
    try:
        reddit_data = reddit.fetch_mentions(pages=int(rd_cfg.get("pages", 2)))
        print(f"[daily] Reddit: {len(reddit_data)} tickers fetched", file=sys.stderr)
    except Exception as e:
        print(f"[daily] Reddit fetch failed (non-fatal): {e}", file=sys.stderr)

    # 4. Assemble: group held positions by ACTION (Exit -> Trim -> Add -> Hold), each sorted by exit score
    buckets: dict = {"EXIT": [], "TRIM": [], "ADD": [], "HOLD": []}
    for t in all_tickers:
        if t not in data:
            continue
        td = data[t]
        price = float(td.df["close"].iloc[-1])
        if t in engines:
            action = engines[t]["ladder"]["action"]
            cat = ("EXIT" if action.startswith("EXIT") else "TRIM" if action.startswith("TRIM")
                   else "ADD" if action.startswith("ADD") else "HOLD")
            block = digest.format_managed_block(
                holdings[t], price, engines[t], validation=llm_out.get(t),
                name=td.name, currency=td.currency)
            ann = reddit.annotation_line(reddit_data.get(t), rd_threshold)
            if ann:
                block += "\n" + ann
            buckets[cat].append((engines[t]["det"]["score"], block))
        else:
            result = llm_out.get(t) or {"ticker": t, "phase": "unclear"}
            block = digest.format_block(
                result, None, price, name=td.name, currency=td.currency, gate_action=False)
            ann = reddit.annotation_line(reddit_data.get(t), rd_threshold)
            if ann:
                block += "\n" + ann
            watchlist_lines.append(block)

    section_label = {
        "portfolio": "Exit",
        "watchlist": "Watchlist",
        "all": "Exit — All",
    }[args.section]
    parts = [f"📊 <b>Wyckoff {section_label} — {date_str}</b>"]

    degraded = wyckoff.degradation()
    if degraded:
        parts.append("⚠️ <b>DEGRADED</b> — Claude was unavailable; analysis ran on "
                     f"<code>{html.escape(', '.join(sorted(degraded)))}</code>, not Claude. "
                     "Re-auth the claude CLI and re-run for a Claude-quality read.")

    if any(buckets.values()):
        for cat, label in (("EXIT", "Exit"), ("TRIM", "Trim"), ("ADD", "Add"), ("HOLD", "Hold")):
            blocks = buckets[cat]
            if blocks:
                parts.append(f"\n<b>— {label} ({len(blocks)}) —</b>")
                # blank line before each block (the leading \n) so assets read as separate groups, not a blob
                parts.extend("\n" + block for _score, block in sorted(blocks, key=lambda x: x[0], reverse=True))

    if watchlist_lines:
        parts.append("\n<b>Watchlist</b>")
        parts.extend(watchlist_lines)

    if errors:
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
