#!/usr/bin/env python3
"""Weekly no-LLM thesis-watch for the *parked* list — the promotion tripwire.

`parked` names (config.yaml) have a live thesis but NO defined low-risk entry yet — still
falling-knife or awaiting a base. They're deliberately kept OUT of the daily watchlist (they'd
only fire noise) and are NOT traded. This job gives them a light WEEKLY touch: a deterministic
deterioration read (0-9) per name, plus a week-over-week move, and a verdict that flags the one
thing we're waiting for — a name that STOPS deteriorating / starts basing. That's the cue to
promote it into `watchlist` with real levels (then the daily tripwire takes over).

Zero Claude credits (reuses deterioration.py, same engine as explain.py). Posts a compact weekly
digest to Telegram; silent only if the parked list is empty. NOT the daily watchlist_scan.py.
"""
from __future__ import annotations
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")

import yaml
import data as market_data
import deterioration as det
import notifier
from prescreener import _get_spy_context

TZ = ZoneInfo("Asia/Jerusalem")
CONFIG = Path(__file__).parent.parent / "config.yaml"
LOOKBACK = 120  # days of history for the structural read (matches the LLM lookback)


def _market_ctx() -> dict | None:
    try:
        mkt = _get_spy_context()
    except Exception:
        return None
    try:
        spy = market_data.fetch_ohlcv("SPY", days=LOOKBACK).df["close"]
        mkt["spy_window_return"] = float(spy.iloc[-1] / spy.iloc[0] - 1)
    except Exception:
        pass
    return mkt


def _verdict(ds: dict) -> tuple[str, int]:
    """Return (verdict_line, sort_rank). Lower rank = more actionable (basing) → sorts first."""
    c = ds["criteria"]
    score = ds["score"]
    deteriorating = c.get("ma_rollover") or c.get("rel_weak") or c.get("support_break")
    if ds.get("has_structural") and score >= 5:
        return ("⚠️ distributive structure (topping) — thesis at risk, consider dropping", 2)
    if score <= 2 and not deteriorating:
        return ("🟢 stabilizing — base-watch; candidate to PROMOTE to watchlist (seed levels)", 0)
    return (f"🔻 still deteriorating ({score}/9) — hold parked, no base yet", 1)


def _check(ticker: str, mkt: dict | None) -> dict | None:
    try:
        td = market_data.fetch_ohlcv(ticker, days=LOOKBACK)
        df = td.df
        if len(df) < 20:
            return None
        close = df["close"]
        curr = float(close.iloc[-1])
        wk_ago = float(close.iloc[-6]) if len(close) >= 6 else float(close.iloc[0])
        wow = (curr - wk_ago) / wk_ago if wk_ago else 0.0
        ds = det.deterioration_score(df, mkt, loss_pct=None)
        verdict, rank = _verdict(ds)
        active = [k for k, v in ds["criteria"].items() if v]
        return {
            "ticker": ticker, "name": td.name, "price": curr, "currency": td.currency,
            "wow": wow, "score": ds["score"], "active": active,
            "verdict": verdict, "rank": rank,
        }
    except Exception as e:
        print(f"[parked_scan] skip {ticker}: {e}", file=sys.stderr)
        return None


def run():
    cfg = yaml.safe_load(CONFIG.read_text()) or {}
    parked = [t.upper() for t in cfg.get("parked", [])]
    if not parked:
        print("[parked_scan] empty parked list — nothing to watch", file=sys.stderr)
        return

    print(f"[parked_scan] reading {len(parked)} parked name(s)...", file=sys.stderr)
    mkt = _market_ctx()
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_check, t, mkt): t for t in parked}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                rows.append(r)

    if not rows:
        print("[parked_scan] no reads — staying silent", file=sys.stderr)
        return

    rows.sort(key=lambda r: (r["rank"], r["score"]))  # basing candidates first
    date_str = datetime.now(tz=TZ).strftime("%Y-%m-%d")
    lines = [f"🅿️ <b>Wyckoff Parked Watch — {date_str}</b>",
             "<i>Weekly thesis-watch (no-LLM). Waiting for one to stop deteriorating → promote.</i>", ""]
    for r in rows:
        sym = {"USD": "$", "ILS": "₪"}.get(r["currency"], r["currency"] + " ")
        name_part = f" ({r['name']})" if r["name"] != r["ticker"] else ""
        lines.append(f"<b>{r['ticker']}</b>{name_part} · {sym}{r['price']:.2f} "
                     f"(1wk {r['wow']*100:+.1f}%) · {r['score']}/9")
        lines.append(f"   {r['verdict']}")
        lines.append("")
    lines.append("↳ <i>A 🟢 name is ready to promote — reply to seed watchlist levels.</i>")

    notifier.send("\n".join(lines))
    print(f"[parked_scan] sent digest for {len(rows)} name(s)", file=sys.stderr)


if __name__ == "__main__":
    run()
