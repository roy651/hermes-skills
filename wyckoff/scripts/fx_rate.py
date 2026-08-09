#!/usr/bin/env python3
"""USD/ILS with a fallback that keeps itself current.

Both the exit run and the portfolio valuation need this rate, and both used to carry a
hardcoded 3.7. By August 2026 the real rate was 3.01 — so any run that hit a fetch failure
silently valued the ILS sleeve ~19% wrong and nobody would have known.

A constant in source will always drift. Instead: every successful fetch writes the rate to
disk, and a failed fetch reads the last good one. Since the daily jobs fetch it anyway, the
fallback is never more than a day or two stale without anyone maintaining it.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import data as market_data

_CACHE = Path(__file__).parent.parent / "data" / "fx_usdils.json"
_SEED = 3.01              # only used on a cold cache; current as of 2026-08-09
_STALE_WARN_DAYS = 14


def latest() -> float:
    """Live USD/ILS, or the last good value if the fetch fails."""
    try:
        rate = float(market_data.fetch_ohlcv("USDILS=X", days=5).df["close"].iloc[-1])
        if rate > 0:
            _save(rate)
            return rate
        raise ValueError(f"nonsensical rate {rate}")
    except Exception as e:
        rate, asof = _cached()
        age = f", cached {asof}" if asof else ", no cache — using seed"
        print(f"[fx] USDILS fetch failed ({e}); using {rate}{age}", file=sys.stderr)
        return rate


def _save(rate: float) -> None:
    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps(
            {"usdils": round(rate, 4), "asof": datetime.now(timezone.utc).isoformat()}))
    except OSError as e:
        print(f"[fx] could not cache rate: {e}", file=sys.stderr)


def _cached() -> tuple[float, str | None]:
    try:
        blob = json.loads(_CACHE.read_text())
        rate, asof = float(blob["usdils"]), blob.get("asof", "")
        days = (datetime.now(timezone.utc) - datetime.fromisoformat(asof)).days
        if days > _STALE_WARN_DAYS:
            print(f"[fx] WARNING cached USDILS is {days} days old", file=sys.stderr)
        return rate, f"{asof[:10]} ({days}d old)"
    except (OSError, ValueError, KeyError):
        return _SEED, None


if __name__ == "__main__":
    print(f"USD/ILS = {latest():.4f}")
