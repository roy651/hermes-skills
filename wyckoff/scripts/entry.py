#!/usr/bin/env python3
"""Sunday Wyckoff weekly run: prescreen → LLM Wyckoff on candidates → news-validate the
top cut → emit tiered entry picks (accumulation STRONG / MARKUP-PULLBACK / BORDERLINE) to Telegram.

The weekly digest IS the entry signal. Portfolio exit-watch is the separate daily job.
"""
from __future__ import annotations
import argparse
import fcntl
import html
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")

import data as market_data
import analysis as wyckoff
import notifier
import news as news_validator
import finnhub
import events as wyckoff_events
import digest
from prescreener import screen_universe, _factor_warnings, _load_factor_tags

TZ = ZoneInfo("Asia/Jerusalem")
LOOKBACK_DAYS = 120

MAX_PICKS = 5               # total picks emitted (STRONG + BORDERLINE)
NEWS_CUT = 8                # news-validate only the top-N by composite (saves API calls)
ANALYZE_WORKERS = 4         # concurrent LLM analyses — keep low; the local proxy chokes at 10
STRONG_MIN_CRITERIA = 7     # Gate B threshold
NEWS_RECS = {"buy", "add", "reduce", "sell"}   # recs worth a news check
ENTRY_RECS = {"buy", "add"}                    # Gate A

# ── analysis ───────────────────────────────────────────────────────────────

def _market_ctx(spy_ctx: dict, c: dict) -> dict:
    return {
        "spy_pct_off_high": spy_ctx.get("spy_pct_off_high"),
        "spy_ret_6m": spy_ctx.get("spy_ret_6m"),
        "spy_ret_12m": spy_ctx.get("spy_ret_12m"),
        "rel_6m": c.get("rel_6m"),
        "rel_12m": c.get("rel_12m"),
    }


def _reconcile_with_events(result: dict, has_event: bool) -> dict:
    """Programmatic events are ground truth. With no confirming Spring/SOS/LPS detected, a
    bullish high-confidence markup read cannot stand — demote it in code, not the prompt.
    This makes 'Markup (high) · 9/9 · Buy' impossible without detected structure (S1/S2)."""
    if has_event:
        return result
    if result.get("phase") == "markup":
        result["phase_confidence"] = "low"
    if result.get("recommendation") in ("buy", "add"):
        result["recommendation"] = "watch"
    try:
        result["criteria_met"] = min(int(result.get("criteria_met") or 0), STRONG_MIN_CRITERIA - 1)
    except (TypeError, ValueError):
        result["criteria_met"] = 0
    result["note"] = "[unconfirmed — detector found no SOS/LPS] " + (result.get("note") or "")
    return result


def _analyze_candidate(c: dict, spy_ctx: dict) -> dict:
    """Fetch → detect events → Wyckoff-analyze one candidate (entry mode). No news.

    Detection runs on full history (252d) so the markup-pullback lookback matches the
    prescreen; the LLM still sees the last LOOKBACK_DAYS for a focused read."""
    ticker = c["ticker"]
    td = market_data.fetch_ohlcv(ticker, days=252)
    df = td.df
    price = float(df["close"].iloc[-1])
    ev = wyckoff_events.detect_events(df)
    event_score, event_labels = wyckoff_events.event_summary(ev)
    has_event = wyckoff_events.has_entry_event(ev)
    result = wyckoff.analyze(
        ticker, df.tail(LOOKBACK_DAYS), held=False, name=td.name, mode="entry",
        market_ctx=_market_ctx(spy_ctx, c), detected_events=event_labels,
    )
    result = _reconcile_with_events(result, has_event)
    return {
        "ticker": ticker,
        "result": result,
        "price": price,
        "name": td.name,
        "currency": td.currency,
        "quant_score": c.get("score"),
        "adv_musd": c.get("adv_musd"),
        "sector": c.get("sector"),
        "market_cap": None,
        "event_score": event_score,
        "event_labels": event_labels,
        "has_event": has_event,
        # entry came from the markup-pullback lane (not a range SOS/LPS) → confirm-before-acting tier
        "is_markup": ev["markup_pullback"] is not None and not (ev["sos"] or ev["lps"]),
        "news_info": None,
    }


def _analyze_candidates(candidates: list[dict], spy_ctx: dict) -> tuple[list[dict], list[str]]:
    bundles: list[dict] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=ANALYZE_WORKERS) as pool:
        futures = {pool.submit(_analyze_candidate, c, spy_ctx): c["ticker"] for c in candidates}
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                bundles.append(fut.result())
            except Exception as e:
                errors.append(f"{ticker}: {e}")
                print(f"[weekly] analysis error on {ticker}: {e}", file=sys.stderr)
    return bundles, errors


# ── scoring / tiering ────────────────────────────────────────────────────────

def _criteria(result: dict) -> int:
    try:
        return int(result.get("criteria_met") or 0)
    except (TypeError, ValueError):
        return 0


def _composite(bundle: dict) -> float:
    """Structure-dominant 0–1 rank. Entry-event presence is a multiplier (not just additive),
    so an ungrounded 'range-only' momentum name cannot rank alongside confirmed structure."""
    crit = _criteria(bundle["result"]) / 9.0
    ev = bundle.get("event_score", 0) / 4.0                       # range + Spring + SOS + LPS
    quant = min(float(bundle.get("quant_score") or 0), 5.0) / 5.0  # clamp (N3)
    has_ev = 1.0 if bundle.get("has_event") else 0.0
    return (0.4 * crit + 0.4 * ev + 0.2 * quant) * (0.5 + 0.5 * has_ev)


def _gates(bundle: dict) -> dict:
    """Four STRONG gates. Gate C requires news validated AND clean (unvalidated → never STRONG).
    Gate D is the hard programmatic event flag from events.py."""
    r = bundle["result"]
    news = bundle.get("news_info")
    return {
        "A_rec": r.get("recommendation", "") in ENTRY_RECS,
        "B_criteria": _criteria(r) >= STRONG_MIN_CRITERIA,
        "C_news": news is not None and news.get("clean", True),
        "D_event": bool(bundle.get("has_event")),
    }


def _missing(bundle: dict) -> list[str]:
    g = _gates(bundle)
    miss = []
    if not g["A_rec"]:
        miss.append("rec≠buy/add")
    if not g["B_criteria"]:
        miss.append(f"criteria {_criteria(bundle['result'])}, need ≥{STRONG_MIN_CRITERIA}")
    if not g["C_news"]:
        miss.append("news unverified/flagged")
    if not g["D_event"]:
        miss.append("no confirmed SOS/LPS")  # a lone Spring is early-stage, not a confirmed entry
    return miss


def _position_size(criteria: int, event_score: int = 0) -> str:
    # "full position" requires the complete Spring→SOS→LPS chain (event_score 4), not just a high LLM count (N1)
    if criteria >= 9 and event_score >= 4:
        return "full position"
    if criteria >= 7:
        return "50% position"
    if criteria >= 5:
        return "30% position"
    return "starter only"


# ── digest ────────────────────────────────────────────────────────────────

def _stats_line(b: dict) -> str:
    parts = []
    if b.get("adv_musd"):
        parts.append(f"ADV ${b['adv_musd']:.0f}M")
    cap = b.get("market_cap")
    if cap:
        parts.append(f"Cap ${cap / 1e9:.1f}B" + (" ⚠️small-cap" if cap < 2e9 else ""))
    if b.get("sector"):
        parts.append(b["sector"])
    out = []
    if parts:
        out.append("  💧 " + " · ".join(parts))
    if b.get("event_labels"):
        out.append("  🔎 Events: " + ", ".join(b["event_labels"]))
    return "\n".join(out)


def _pick_block(b: dict, with_size: bool = True) -> list[str]:
    out = [digest.format_block(
        b["result"], None, b["price"], name=b["name"],
        currency=b["currency"], news_info=b.get("news_info"), gate_action=True,
    )]
    stats = _stats_line(b)
    if stats:
        out.append(stats)
    if with_size:
        out.append(f"  📐 Suggested size: {_position_size(_criteria(b['result']), b.get('event_score', 0))}")
    return out


def _build_weekly_digest(
    spy_ctx: dict,
    strong: list[dict],
    markup: list[dict],
    borderline: list[dict],
    factor_tags: dict,
    date_str: str,
    errors: list[str],
) -> str:
    spy_off = spy_ctx.get("spy_pct_off_high", 0) * 100
    r6 = spy_ctx.get("spy_ret_6m", 0) * 100
    r12 = spy_ctx.get("spy_ret_12m", 0) * 100
    lines = [
        f"📈 <b>Wyckoff Weekly — {date_str}</b>",
        f"<i>SPY {spy_off:.1f}% off 52w high · 6m {r6:+.1f}% · 12m {r12:+.1f}%</i>",
        "",
        f"🟢 <b>STRONG — accumulation confirmed ({len(strong)})</b>",
    ]
    if strong:
        for b in strong:
            lines.extend(_pick_block(b))
            lines.append("")
    else:
        lines.append("<i>None this week — no base completed a Spring→SOS→LPS accumulation sequence.</i>")
        lines.append("")

    lines.append(f"🟣 <b>MARKUP-PULLBACK — confirm before acting ({len(markup)})</b>")
    if markup:
        lines.append("<i>Leaders pulling back to a recent breakout (these bypass the off-high floor). "
                     "A quiet-rally distribution top can look identical at entry — confirm by eye / lean on "
                     "the daily exit-watch, and keep half size.</i>")
        for b in markup:
            lines.extend(_pick_block(b))
            lines.append("")
    else:
        lines.append("<i>None.</i>")
        lines.append("")

    lines.append(f"🟡 <b>BORDERLINE ({len(borderline)})</b>")
    if borderline:
        for b in borderline:
            lines.extend(_pick_block(b, with_size=False))
            miss = _missing(b)
            if miss:
                lines.append(f"  <i>Missing: {', '.join(miss)}</i>")
            lines.append("")
    else:
        lines.append("<i>None.</i>")
        lines.append("")

    warnings = _factor_warnings(strong + markup + borderline, factor_tags)
    if warnings:
        lines.extend(warnings)
        lines.append("")

    if errors:
        safe = ", ".join(html.escape(str(e)) for e in errors)
        lines.append(f"<i>Errors: {safe}</i>")

    return "\n".join(lines).strip()


# ── run ──────────────────────────────────────────────────────────────────────

_LOCK_PATH = "/tmp/wyckoff_weekly.lock"
_lock_fh = None              # kept alive for the process lifetime; flock releases when the fd closes
MAX_RUNTIME_SEC = 1500       # 25 min hard ceiling — bounds a hang so the lock can't be held forever


def _acquire_singleton_lock() -> bool:
    """Non-blocking exclusive lock so a slow run can't be duplicated by an agent retry.
    flock is held until the process exits (incl. crash/kill), so it never goes stale."""
    global _lock_fh
    _lock_fh = open(_LOCK_PATH, "w")
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (BlockingIOError, OSError):
        return False


def _start_watchdog(seconds: int) -> None:
    """Daemon timer: if the run hangs past `seconds`, alert + force-exit (releasing the lock).
    A daemon thread never keeps the process alive, so a normal finish ignores it."""
    def _kill():
        time.sleep(seconds)
        print(f"[weekly] watchdog: exceeded {seconds}s — force exit", file=sys.stderr)
        try:
            notifier.send(f"⚠️ <b>Wyckoff Weekly</b> watchdog: run exceeded {seconds // 60} min and was killed.")
        except Exception:
            pass
        os._exit(2)
    threading.Thread(target=_kill, daemon=True).start()


def run(dry_run: bool = False) -> None:
    if not dry_run:
        if not _acquire_singleton_lock():
            print("[weekly] another run already in progress — exiting (singleton lock)", file=sys.stderr)
            return
        _start_watchdog(MAX_RUNTIME_SEC)
    date_str = datetime.now(tz=TZ).strftime("%Y-%m-%d")
    factor_tags = _load_factor_tags()

    # Stage 1: quantitative prescreen
    print("[weekly] running prescreener...", file=sys.stderr)
    candidates, spy_ctx = screen_universe()
    print(f"[weekly] {len(candidates)} candidates from prescreen", file=sys.stderr)

    # Stage 2: drop candidates reporting earnings within 14 days (signal unreliable across earnings)
    try:
        soon = finnhub.earnings_within({c["ticker"] for c in candidates}, days=14)
        if soon:
            candidates = [c for c in candidates if c["ticker"] not in soon]
            print(f"[weekly] excluded {len(soon)} earnings-imminent: {sorted(soon)}", file=sys.stderr)
    except Exception as e:
        print(f"[weekly] earnings calendar unavailable, skipping exclusion: {e}", file=sys.stderr)

    # Stage 3: LLM Wyckoff on each candidate (entry mode, market context)
    bundles, errors = _analyze_candidates(candidates, spy_ctx)
    print(f"[weekly] analyzed {len(bundles)} candidates", file=sys.stderr)

    # Stage 4: news-validate only the top cut by composite
    bundles.sort(key=_composite, reverse=True)
    for b in bundles[:NEWS_CUT]:
        rec = b["result"].get("recommendation", "")
        if rec in NEWS_RECS:
            try:
                b["news_info"] = news_validator.validate(b["ticker"], b["name"], rec)
            except Exception as e:
                print(f"[weekly] news validation failed for {b['ticker']}: {e}", file=sys.stderr)

    # Stage 5: tier gated picks. Accumulation STRONG (range-lane SOS/LPS) gets the high-conviction
    # label; markup-pullback entries get their OWN confirm-before-acting tier — the MP lane bypasses
    # the off-high floor + rel-perf cap and carries an entry-irreducible quiet-top FP (review 4),
    # so it must not share the autonomous-STRONG label. BORDERLINE fills the rest.
    gated = [b for b in bundles if all(_gates(b).values())]
    strong = sorted([b for b in gated if not b.get("is_markup")], key=_composite, reverse=True)[:MAX_PICKS]
    markup = sorted([b for b in gated if b.get("is_markup")], key=_composite, reverse=True)[:MAX_PICKS]
    picked = {b["ticker"] for b in strong + markup}
    borderline = sorted(
        [b for b in bundles if b["ticker"] not in picked], key=_composite, reverse=True
    )[: max(0, MAX_PICKS - len(strong) - len(markup))]

    # Enrich the final picks with market cap (cheap — only a few lookups)
    for b in strong + markup + borderline:
        try:
            b["market_cap"] = finnhub.market_cap(b["ticker"])
        except Exception as e:
            print(f"[weekly] market cap unavailable for {b['ticker']}: {e}", file=sys.stderr)

    msg = _build_weekly_digest(spy_ctx, strong, markup, borderline, factor_tags, date_str, errors)
    if dry_run:
        print(msg)
    else:
        notifier.send(msg)
    print(
        f"[weekly] {'(dry-run) ' if dry_run else ''}done — "
        f"{len(strong)} STRONG, {len(markup)} MARKUP-PULLBACK, {len(borderline)} BORDERLINE",
        file=sys.stderr,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print digest instead of sending")
    args = parser.parse_args()
    try:
        run(dry_run=args.dry_run)
    except Exception as e:
        # The job runs detached, so surface a hard failure to Telegram (not just the log).
        import traceback
        traceback.print_exc()
        if not args.dry_run:
            try:
                notifier.send(f"⚠️ <b>Wyckoff Weekly failed</b>: {html.escape(str(e)[:300])}")
            except Exception:
                pass
        sys.exit(1)
