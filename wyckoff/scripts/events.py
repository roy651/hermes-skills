#!/usr/bin/env python3
"""Programmatic Wyckoff event detection — pure pandas, no LLM.

Detects the structural events the LLM otherwise has to eyeball from raw OHLCV:
a horizontal trading range, a Spring, an SOS, and an LPS, and enforces their
chronological order (Spring → SOS → LPS). Thresholds are documented in
references/wyckoff-events-glossary.md and calibrated against tests/validate_events.py.

CLI (calibration):  python events.py TICKER [days]
"""
from __future__ import annotations
import pandas as pd

# --- Range (detect_range) ---
RANGE_LOOKBACK = 60        # bars examined for a trading range
RANGE_MAX_WIDTH = 0.20     # max band-to-band spread to count as horizontal
RANGE_BAND_Q = 0.10        # support/resistance = 10th/90th percentile (range body, not extremes)
TOUCH_TOL = 0.02           # within 2% of a band counts as a touch
TOUCH_CLUSTER_GAP = 5      # touches >this many bars apart are separate visits (M3)
MIN_TOUCH_CLUSTERS = 2     # distinct, time-separated visits required per band (M3)

# --- Spring ---
SPRING_PIERCE = 0.99       # low must dip below support * this, then close back above support

# --- SOS (single-bar OR multi-bar trigger) ---
SOS_GAIN = 0.04            # single-bar advance
SOS_VOL_X = 1.5            # single-bar volume vs 20d average
SOS_CUM_BARS = 3           # multi-bar window
SOS_CUM_GAIN = 0.06        # cumulative advance over the window
SOS_CUM_VOL_X = 1.3        # window-average volume vs 20d average

# --- LPS ---
LPS_VOL_X = 0.7            # LPS volume must be below SOS-bar volume * this (contracting)


def _cluster_count(positions: list[int], gap: int) -> int:
    """Number of time-separated clusters in a sorted list of bar positions."""
    if not positions:
        return 0
    clusters = 1
    for prev, cur in zip(positions, positions[1:]):
        if cur - prev > gap:
            clusters += 1
    return clusters


def detect_range(df: pd.DataFrame) -> dict | None:
    """Identify a horizontal trading range in the recent window, or None.

    Support/resistance are percentile *bands* (range body), so a Spring (lowest low) or
    Upthrust (highest high) sits outside the band and stays detectable. Touches must form
    >= MIN_TOUCH_CLUSTERS time-separated visits of each band (a trending window has its
    band-touches bunched at one end and is correctly rejected).
    """
    window = df.tail(RANGE_LOOKBACK)
    if len(window) < 20:
        return None
    lows, highs = window["low"], window["high"]
    support = float(lows.quantile(RANGE_BAND_Q))
    resistance = float(highs.quantile(1 - RANGE_BAND_Q))
    if support <= 0 or (resistance / support - 1) >= RANGE_MAX_WIDTH:
        return None

    sup_pos = [i for i, v in enumerate(lows.values) if v <= support * (1 + TOUCH_TOL)]
    res_pos = [i for i, v in enumerate(highs.values) if v >= resistance * (1 - TOUCH_TOL)]
    sup_clusters = _cluster_count(sup_pos, TOUCH_CLUSTER_GAP)
    res_clusters = _cluster_count(res_pos, TOUCH_CLUSTER_GAP)
    if sup_clusters < MIN_TOUCH_CLUSTERS or res_clusters < MIN_TOUCH_CLUSTERS:
        return None

    return {
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "mid": round((support + resistance) / 2, 2),
        "width_pct": round((resistance / support - 1) * 100, 1),
        "duration": int(len(window)),
        "support_clusters": sup_clusters,
        "resistance_clusters": res_clusters,
        "start": str(window.index[0]),
        "end": str(window.index[-1]),
    }


def detect_events(df: pd.DataFrame) -> dict:
    """Return {'range','spring','sos','lps'} (each a dict or None), with Spring→SOS→LPS
    chronology enforced: an SOS that predates the most recent Spring is a failed/prior
    attempt and is dropped (along with any LPS that depended on it)."""
    out = {"range": None, "spring": None, "sos": None, "lps": None}
    rng = detect_range(df)
    out["range"] = rng
    if not rng:
        return out

    sup, mid, resistance = rng["support"], rng["mid"], rng["resistance"]
    n = len(df)
    scan_start = max(SOS_CUM_BARS, n - RANGE_LOOKBACK)   # M2: scan the whole range span
    close = df["close"].values
    low = df["low"].values
    high = df["high"].values
    vol = df["volume"].values
    v20 = df["volume"].rolling(20).mean().values
    idx = df.index

    spring_i = sos_i = lps_i = None

    # Spring — most recent pierce below support that closes back above it
    for i in range(n - 1, scan_start - 1, -1):
        if low[i] < sup * SPRING_PIERCE and close[i] > sup:
            confirmed = any(close[j] > close[i] for j in range(i + 1, min(i + 4, n)))
            out["spring"] = {"date": str(idx[i]), "low": round(float(low[i]), 2), "confirmed": confirmed}
            spring_i = i
            break

    # SOS — most recent strong advance: single big up-bar OR multi-bar push clearing resistance
    for i in range(n - 1, scan_start - 1, -1):
        if pd.isna(v20[i]) or v20[i] <= 0 or close[i - 1] <= 0:
            continue
        single = (
            (close[i] - close[i - 1]) / close[i - 1] > SOS_GAIN
            and vol[i] > SOS_VOL_X * v20[i]
            and close[i] >= mid
        )
        multi = False
        if i >= SOS_CUM_BARS and close[i - SOS_CUM_BARS] > 0:
            cum = (close[i] - close[i - SOS_CUM_BARS]) / close[i - SOS_CUM_BARS]
            vol_avg = float(vol[i - SOS_CUM_BARS + 1 : i + 1].mean())
            multi = cum > SOS_CUM_GAIN and close[i] > resistance and vol_avg > SOS_CUM_VOL_X * v20[i]
        if single or multi:
            gain = (close[i] - close[i - 1]) / close[i - 1] if single else (
                (close[i] - close[i - SOS_CUM_BARS]) / close[i - SOS_CUM_BARS]
            )
            out["sos"] = {
                "date": str(idx[i]),
                "gain_pct": round(float(gain * 100), 1),
                "vol_x": round(float(vol[i] / v20[i]), 1),
                "high": round(float(high[i]), 2),
                "kind": "single" if single else "multi",
            }
            sos_i = i
            break

    # Chronology: an SOS that predates the most recent Spring belongs to a prior, failed
    # attempt — drop it (and any LPS), keeping the Spring as the current (early) event.
    if spring_i is not None and sos_i is not None and spring_i > sos_i:
        out["sos"] = None
        sos_i = None

    # LPS — first low-volume pullback after a valid SOS that holds above range mid
    if sos_i is not None:
        sos_vol = vol[sos_i]
        for j in range(sos_i + 1, n):
            if close[j] > mid and sos_vol > 0 and vol[j] < LPS_VOL_X * sos_vol and close[j] < high[sos_i]:
                out["lps"] = {
                    "date": str(idx[j]),
                    "close": round(float(close[j]), 2),
                    "vol_x_sos": round(float(vol[j] / sos_vol), 2),
                }
                lps_i = j
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
    """A *confirmed* entry needs strength (SOS) or the entry pullback (LPS). A lone Spring
    is early-stage accumulation (watch for SOS), not a confirmed markup entry — so it does
    NOT clear the hard entry gate on its own."""
    return bool(events.get("sos") or events.get("lps"))


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
    print(f"{ticker} ({td.name}) — {days}d  →  event_score={score}  has_entry_event={has_entry_event(ev)}")
    print(f"labels: {labels}")
    print(json.dumps(ev, indent=2))
