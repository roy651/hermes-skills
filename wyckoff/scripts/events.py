#!/usr/bin/env python3
"""Programmatic Wyckoff event detection — pure pandas, no LLM.

Detects the structural events the LLM otherwise has to eyeball from raw OHLCV:
a horizontal trading range, a Spring, an SOS, and an LPS. Thresholds are documented
in references/wyckoff-events-glossary.md and are deliberately conservative.

CLI (calibration):  python events.py TICKER [days]
"""
from __future__ import annotations
import pandas as pd

# Detection thresholds (see references/wyckoff-events-glossary.md)
RANGE_LOOKBACK = 60       # bars to look for a trading range
RANGE_MAX_WIDTH = 0.20    # max band-to-band spread to count as horizontal
RANGE_BAND_Q = 0.10       # support/resistance = 10th/90th percentile (range body, not extremes)
TOUCH_TOL = 0.02          # within 2% of the band counts as a touch
MIN_TOUCHES = 3           # touches of each band required
EVENT_SCAN = 40           # bars back to scan for Spring/SOS/LPS
SPRING_PIERCE = 0.99      # low must dip below support * this
SOS_GAIN = 0.04           # single-bar advance to qualify as an SOS
SOS_VOL_X = 1.5           # SOS volume vs 20d average
LPS_VOL_X = 0.7           # LPS volume must be below SOS_volume * this
LPS_NEAR_SOS = 0.03       # LPS close within 3% of the SOS high


def detect_range(df: pd.DataFrame) -> dict | None:
    """Identify a horizontal trading range in the recent window, or None.

    Support/resistance are percentile *bands* (the range body), not absolute extremes —
    so a Spring (lowest low) or Upthrust (highest high) sits below/above the band and stays
    detectable. Width is measured band-to-band.
    """
    window = df.tail(RANGE_LOOKBACK)
    if len(window) < 20:
        return None
    lows, highs = window["low"], window["high"]
    support = float(lows.quantile(RANGE_BAND_Q))
    resistance = float(highs.quantile(1 - RANGE_BAND_Q))
    if support <= 0 or (resistance / support - 1) >= RANGE_MAX_WIDTH:
        return None
    sup_touches = int((lows <= support * (1 + TOUCH_TOL)).sum())
    res_touches = int((highs >= resistance * (1 - TOUCH_TOL)).sum())
    if sup_touches < MIN_TOUCHES or res_touches < MIN_TOUCHES:
        return None
    return {
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "mid": round((support + resistance) / 2, 2),
        "width_pct": round((resistance / support - 1) * 100, 1),
        "duration": int(len(window)),
        "support_touches": sup_touches,
        "resistance_touches": res_touches,
        "start": str(window.index[0]),
        "end": str(window.index[-1]),
    }


def detect_events(df: pd.DataFrame) -> dict:
    """Return {'range','spring','sos','lps'} — each value a dict or None."""
    out = {"range": None, "spring": None, "sos": None, "lps": None}
    rng = detect_range(df)
    out["range"] = rng
    if not rng:
        return out

    sup, mid = rng["support"], rng["mid"]
    n = len(df)
    start = max(1, n - EVENT_SCAN)
    close = df["close"].values
    low = df["low"].values
    high = df["high"].values
    vol = df["volume"].values
    v20 = df["volume"].rolling(20).mean().values
    idx = df.index

    # Spring — most recent pierce below support that closes back above it
    for i in range(n - 1, start - 1, -1):
        if low[i] < sup * SPRING_PIERCE and close[i] > sup:
            confirmed = any(close[j] > close[i] for j in range(i + 1, min(i + 4, n)))
            out["spring"] = {"date": str(idx[i]), "low": round(float(low[i]), 2), "confirmed": confirmed}
            break

    # SOS — most recent strong up-bar on expanded volume, at/above range mid
    for i in range(n - 1, start - 1, -1):
        pc = close[i - 1]
        if pc <= 0 or pd.isna(v20[i]) or v20[i] <= 0:
            continue
        if (close[i] - pc) / pc > SOS_GAIN and vol[i] > SOS_VOL_X * v20[i] and close[i] >= mid:
            out["sos"] = {
                "date": str(idx[i]),
                "gain_pct": round(float((close[i] - pc) / pc * 100), 1),
                "vol_x": round(float(vol[i] / v20[i]), 1),
                "high": round(float(high[i]), 2),
            }
            break

    # LPS — first low-volume pullback after the SOS that holds above mid, near the SOS high
    if out["sos"]:
        sos_high = out["sos"]["high"]
        sos_pos = list(map(str, idx)).index(out["sos"]["date"])
        sos_vol = vol[sos_pos]
        for i in range(sos_pos + 1, n):
            if (
                close[i] > mid
                and sos_vol > 0
                and vol[i] < LPS_VOL_X * sos_vol
                and abs(close[i] - sos_high) / sos_high < LPS_NEAR_SOS
            ):
                out["lps"] = {
                    "date": str(idx[i]),
                    "close": round(float(close[i]), 2),
                    "vol_x_sos": round(float(vol[i] / sos_vol), 2),
                }
                break

    return out


def event_summary(events: dict) -> tuple[int, list[str]]:
    """(score, display_labels). score: 0 none, 1 range only, 2+ range plus event(s)."""
    if not events.get("range"):
        return 0, []
    score, labels = 1, ["range"]
    sp = events.get("spring")
    if sp:
        score += 1
        labels.append(f"Spring {sp['date']}" + ("✓" if sp["confirmed"] else ""))
    so = events.get("sos")
    if so:
        score += 1
        labels.append(f"SOS {so['date']}")
    lp = events.get("lps")
    if lp:
        score += 1
        labels.append(f"LPS {lp['date']}")
    return score, labels


def has_entry_event(events: dict) -> bool:
    """True if a Spring, SOS, or LPS was detected (the hard Gate D signal)."""
    return any(events.get(k) for k in ("spring", "sos", "lps"))


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from dotenv import load_dotenv
    load_dotenv(Path.home() / ".hermes" / ".env")
    import data as market_data

    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 252
    td = market_data.fetch_ohlcv(ticker, days=days)
    ev = detect_events(td.df)
    score, labels = event_summary(ev)
    print(f"{ticker} ({td.name}) — {days}d  →  event_score={score}  {labels}")
    print(json.dumps(ev, indent=2))
