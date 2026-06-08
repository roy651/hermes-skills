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

# --- Markup-pullback lane (Option 2): a confirmed-breakout pullback, independent of detect_range.
#     A name in an established markup is no longer in a 60-bar horizontal range, so this is a
#     separate detection mode that bypasses the prescreen off-high floor for that specific case.
MP_LOOKBACK = 150         # bars to establish the prior ceiling (breakout level)
MP_RECENT = 45            # recent window in which the breakout + pullback happen
MP_BREAKOUT_MARGIN = 0.03 # price must clear the prior ceiling by this to count as a breakout
MP_PULLBACK_MIN = 0.03    # must pull back at least this from the post-breakout peak (not still extended)
MP_PULLBACK_MAX = 0.15    # but not MORE than this — a deep give-back near the breakout is a
                          # near-failed markup, not a shallow LPS "near recent highs"
MP_HOLD_TOL = 0.02        # LPS low may dip this far below the breakout level but must close above it
MP_VOL_X = 0.8            # LPS volume must be below the breakout-rally avg * this (contracting)
MP_EFFORT_X = 1.5         # reject if the rally-leg AVG volume exceeds this × the prior-base avg —
                          # a climactic/blow-off advance is a buying-climax/distribution risk, not a
                          # healthy re-accumulation pullback (review 3, empirically proven FP)


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


def detect_markup_pullback(df: pd.DataFrame) -> dict | None:
    """Second detection mode (Option 2): a confirmed breakout above a prior ceiling, followed by
    a pullback that *holds above that breakout level* on contracting volume. Independent of
    detect_range — a name in an established markup is no longer in a horizontal base. Returns
    None for: no breakout, still-extended (no pullback), or a failed breakout (fell back below)."""
    n = len(df)
    if n < 40:
        return None
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    vol = df["volume"].values
    idx = df.index

    lb_start = max(0, n - MP_LOOKBACK)
    recent_start = n - min(MP_RECENT, n - 1)
    if recent_start <= lb_start + 5:
        return None

    breakout = float(high[lb_start:recent_start].max())     # prior ceiling
    if breakout <= 0:
        return None
    peak_i = recent_start + int(high[recent_start:n].argmax())
    peak = float(high[peak_i])
    if peak < breakout * (1 + MP_BREAKOUT_MARGIN):
        return None                                          # never cleared the ceiling
    cur = float(close[-1])
    if cur >= peak * (1 - MP_PULLBACK_MIN):
        return None                                          # still extended at the highs, no pullback
    if cur < peak * (1 - MP_PULLBACK_MAX):
        return None                                          # deep give-back → near-failed markup, not an LPS
    if cur < breakout:
        return None                                          # fell back below breakout → failed breakout

    rally_vol = float(vol[recent_start:peak_i + 1].mean()) if peak_i >= recent_start else 0.0

    # Effort filter: a climactic (expanding-volume) advance into the peak is a buying-climax /
    # distribution risk, not a healthy re-accumulation pullback. Key off the rally-leg AVERAGE vs
    # the prior base so one legitimate breakout-thrust bar isn't penalized.
    prior_vol = float(vol[lb_start:recent_start].mean())
    if prior_vol > 0 and rally_vol > MP_EFFORT_X * prior_vol:
        return None

    bars_holding = sum(1 for i in range(peak_i + 1, n) if close[i] > breakout)
    for i in range(peak_i + 1, n):
        if (low[i] >= breakout * (1 - MP_HOLD_TOL) and close[i] > breakout
                and rally_vol > 0 and vol[i] < MP_VOL_X * rally_vol):
            return {
                "breakout_level": round(breakout, 2),
                "peak": round(peak, 2),
                "effort_ratio": round(rally_vol / prior_vol, 2) if prior_vol > 0 else None,
                "bars_holding": int(bars_holding),
                "lps": {"date": str(idx[i]), "close": round(float(close[i]), 2),
                        "vol_x_rally": round(float(vol[i] / rally_vol), 2)},
            }
    return None


def detect_events(df: pd.DataFrame) -> dict:
    """Return {'range','spring','sos','lps','markup_pullback'} (each a dict or None). Spring→SOS→LPS
    chronology is enforced (an SOS predating the most recent Spring is dropped with its LPS). The
    markup-pullback lane is detected independently of the range."""
    out = {"range": None, "spring": None, "sos": None, "lps": None, "markup_pullback": None}
    out["markup_pullback"] = detect_markup_pullback(df)
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
    """(score, display_labels). Range lane: 1 (range) + Spring/SOS/LPS. Markup-pullback lane: +2."""
    score, labels = 0, []
    if events.get("range"):
        score += 1
        labels.append("range")
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
    mp = events.get("markup_pullback")
    if mp:
        score += 2
        labels.append(f"Markup-pullback LPS {mp['lps']['date']} (holds >breakout {mp['breakout_level']})")
    return score, labels


def has_entry_event(events: dict) -> bool:
    """A *confirmed* entry needs strength (SOS), the range LPS, or a markup-pullback LPS above a
    breakout. A lone Spring is early-stage accumulation (watch for SOS), not a confirmed entry —
    so it does NOT clear the hard entry gate on its own."""
    return bool(events.get("sos") or events.get("lps") or events.get("markup_pullback"))


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
