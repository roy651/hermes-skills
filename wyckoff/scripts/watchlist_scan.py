#!/usr/bin/env python3
"""Daily no-LLM watchlist scan — the entry-pipeline tripwire.

Watches the *curated* config `watchlist` (names awaiting a defined Wyckoff entry) against
per-name `watchlist_levels` (support/resistance seeded weekly from the LLM read). Purely
arithmetic: fires a "spring/LPS watch" as price approaches support, a "SOS/breakout watch"
as it approaches or closes above resistance. A name with NO defined levels is a candidate
still awaiting a base — it has no decision level to watch, so it stays silent (no generic
%-move alarm) until the weekly LLM read seeds levels. This keeps the tripwire about
*decision levels*, not raw volatility. Zero Claude credits.

Every alert ends with a nudge to reply for a manual LLM verify — the scan only needs to be a
good "wake me up" band, not a precise entry rule. Silent (no Telegram) when nothing trips, to
keep the channel clean. NOT the same as price_alerts.py, which scans the prescreen-candidate
pool (data/watchlist_candidates.json); this one is the hand-picked config watchlist.
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
import notifier

TZ = ZoneInfo("Asia/Jerusalem")
CONFIG = Path(__file__).parent.parent / "config.yaml"
MOVE_THRESHOLD = 0.035  # 3.5% — generic fallback for names without defined levels


def _load_cfg() -> dict:
    return yaml.safe_load(CONFIG.read_text()) or {}


def _check(ticker: str, levels: dict | None, band: float) -> dict | None:
    """Return an alert dict if the ticker trips a level band or the generic %-move, else None."""
    try:
        td = market_data.fetch_ohlcv(ticker, days=5)
        close = td.df["close"]
        if len(close) < 2:
            return None
        prev, curr = float(close.iloc[-2]), float(close.iloc[-1])
        pct_chg = (curr - prev) / prev

        triggers: list[str] = []
        if levels:
            sup = levels.get("support")
            res = levels.get("resistance")
            if sup:
                dist = (curr - sup) / sup
                if curr < sup:
                    triggers.append(f"🔻 broke support {sup:g} ({dist*100:+.1f}%) — spring/breakdown watch")
                elif dist <= band:
                    triggers.append(f"🟡 nearing support {sup:g} ({dist*100:+.1f}%) — spring/LPS watch")
            if res:
                dist = (curr - res) / res
                if curr > res:
                    triggers.append(f"🚀 closed above resistance {res:g} ({dist*100:+.1f}%) — SOS/breakout watch")
                elif -band <= dist <= 0:
                    triggers.append(f"🟢 nearing resistance {res:g} ({dist*100:+.1f}%) — breakout watch")

        # Generic %-move: extra signal ONLY for names that already have defined levels but
        # moved big without hitting a band (e.g. a mid-range lurch). Level-less names get no
        # %-alarm — they're base-watch candidates, handled by the weekly LLM read, not this
        # deterministic tripwire.
        if levels and not triggers and abs(pct_chg) >= MOVE_THRESHOLD:
            triggers.append(f"⚡ {pct_chg*100:+.1f}% day — big move off its levels, eyeball it")

        if not triggers:
            return None
        return {
            "ticker": ticker, "name": td.name, "price": curr,
            "pct_chg": pct_chg, "currency": td.currency, "triggers": triggers,
        }
    except Exception as e:
        print(f"[watchlist_scan] skip {ticker}: {e}", file=sys.stderr)
        return None


def run():
    cfg = _load_cfg()
    watchlist = [t.upper() for t in cfg.get("watchlist", [])]
    levels_map = {k.upper(): v for k, v in (cfg.get("watchlist_levels") or {}).items()}
    band = float(cfg.get("scan_band_pct", 1.0)) / 100.0

    if not watchlist:
        print("[watchlist_scan] empty watchlist — nothing to scan", file=sys.stderr)
        return

    print(f"[watchlist_scan] scanning {len(watchlist)} names (band ±{band*100:g}%)...", file=sys.stderr)
    alerts: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_check, t, levels_map.get(t), band): t for t in watchlist}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                alerts.append(r)

    if not alerts:
        print("[watchlist_scan] no triggers — staying silent", file=sys.stderr)
        return

    # Order: level-band trips first (most actionable), then generic movers.
    alerts.sort(key=lambda a: (a["triggers"][0].startswith("⚡"), -abs(a["pct_chg"])))

    date_str = datetime.now(tz=TZ).strftime("%Y-%m-%d")
    lines = [f"🎯 <b>Wyckoff Watchlist Scan — {date_str}</b>",
             "<i>Entry-pipeline tripwire (no-LLM). Levels refreshed weekly.</i>", ""]
    for a in alerts:
        _sym = {"USD": "$", "ILS": "₪"}.get(a["currency"], a["currency"] + " ")
        name_part = f" ({a['name']})" if a["name"] != a["ticker"] else ""
        lines.append(f"<b>{a['ticker']}</b>{name_part} · {_sym}{a['price']:.2f}")
        for t in a["triggers"]:
            lines.append(f"   {t}")
        lines.append("")
    lines.append("↳ <i>Reply with a ticker to run a full LLM Wyckoff verify.</i>")

    notifier.send("\n".join(lines))
    print(f"[watchlist_scan] sent {len(alerts)} alert(s)", file=sys.stderr)


if __name__ == "__main__":
    run()
