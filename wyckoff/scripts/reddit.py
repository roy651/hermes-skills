"""ApeWisdom Reddit mention data — no API key, stdlib-only.

Every fetch is archived to data/reddit_history/. ApeWisdom serves only a live snapshot: the
mention counts for a past date are not retrievable afterwards from any source we have. So a
day not stored is a day of point-in-time text data permanently lost, and text is the one
candidate signal family we currently cannot backtest at all for exactly that reason.
Archiving costs nothing and is the only way the option stays open.""" 
from __future__ import annotations
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from html import escape as _esc

_ARCHIVE = Path(__file__).parent.parent / "data" / "reddit_history"

_URL = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/{page}"
_UA  = "wyckoff-monitor/1.0"


def fetch_mentions(pages: int = 2) -> dict[str, dict]:
    """Return {ticker: {rank, mentions, velocity}} for top ~25*pages US stocks.

    velocity = (mentions_today - mentions_yesterday) / max(yesterday, 1).
    A 200% velocity means today's mentions are 3× yesterday's."""
    results: dict[str, dict] = {}
    for page in range(1, pages + 1):
        try:
            req = urllib.request.Request(
                _URL.format(page=page), headers={"User-Agent": _UA}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.load(resp)
            for item in data.get("results", []):
                ticker = str(item.get("ticker", "")).upper().strip()
                if not ticker or len(ticker) > 5:
                    continue
                mentions = int(item.get("mentions", 0) or 0)
                prev     = int(item.get("mentions_24h_ago", 0) or 0)
                velocity = (mentions - prev) / max(prev, 1)
                results[ticker] = {
                    "rank":     int(item.get("rank", 999) or 999),
                    "mentions": mentions,
                    "velocity": round(velocity, 2),
                }
        except Exception:
            pass
    _archive(results)
    return results


def _archive(results: dict) -> None:
    """One file per UTC day; last write of the day wins. Never raises — archiving must not
    be able to break a digest."""
    if not results:
        return
    try:
        _ARCHIVE.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        path = _ARCHIVE / f"{now:%Y-%m-%d}.json"
        path.write_text(json.dumps(
            {"fetched_at": now.isoformat(), "tickers": results}, separators=(",", ":")))
    except OSError as e:
        print(f"[reddit] archive failed: {e}", file=sys.stderr)


def annotation_line(rd: dict | None, threshold: float = 2.0) -> str | None:
    """One italic annotation line for a pick or holding block. Returns None if no Reddit data."""
    if not rd:
        return None
    vel = rd["velocity"]
    if vel > 0:
        vel_str = f"↑{vel * 100:.0f}%"
    elif vel < 0:
        vel_str = f"↓{abs(vel) * 100:.0f}%"
    else:
        vel_str = "→"
    warn = " ⚠️ high buzz — watch distribution" if vel >= threshold else ""
    return f"<i>Reddit #{rd['rank']}  {vel_str}{warn}</i>"


def radar_message(
    reddit_data: dict[str, dict],
    picked: set[str],
    bundles: list[dict],
    top_n: int = 10,
    threshold: float = 2.0,
    date_str: str = "",
) -> str:
    """Side-path Radar: top-N velocity movers cross-referenced against Wyckoff prescreener output.
    Sent as a separate Telegram message after the main weekly digest."""
    by_velocity = sorted(
        reddit_data.items(), key=lambda x: x[1]["velocity"], reverse=True
    )[:top_n]

    if not by_velocity:
        return ""

    candidate_map = {b["ticker"]: b for b in bundles}

    lines = [
        f"📡 <b>Reddit Radar — {_esc(date_str)}</b>",
        "<i>Top velocity movers (mention acceleration vs. yesterday)</i>",
        "",
    ]

    for ticker, rd in by_velocity:
        vel = rd["velocity"]
        vel_str = f"↑{vel * 100:.0f}%" if vel > 0 else (f"↓{abs(vel) * 100:.0f}%" if vel < 0 else "→")
        prefix = "🔥" if vel >= threshold else "  "

        if ticker in picked:
            label = "⚠️ in Wyckoff picks — watch distribution"
        elif ticker in candidate_map:
            b = candidate_map[ticker]
            r = b.get("result", {})
            phase = str(r.get("phase", "?")).title()
            conf  = r.get("phase_confidence", "")
            crit  = r.get("criteria_met", "?")
            label = f"prescreened — {phase}{f' ({conf})' if conf else ''} · {crit}/9"
        else:
            label = "not in Wyckoff screen"

        lines.append(f"{prefix} <b>{_esc(ticker)}</b>  {vel_str}  #{rd['rank']}  — {label}")

    lines += [
        "",
        "<i>Source: ApeWisdom · r/wallstreetbets + r/stocks + r/options</i>",
    ]
    return "\n".join(lines)
