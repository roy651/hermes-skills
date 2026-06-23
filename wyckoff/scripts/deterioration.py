#!/usr/bin/env python3
"""Programmatic Wyckoff distribution / deterioration detection — the exit-side mirror of events.py.

Detects the warning structures (Upthrust, SOW, LPSY, support break) and computable deterioration
criteria (rel-strength flip, MA rollover, distribution volume, off-highs), and rolls them into a
0-9 *exit score* — the symmetric analogue of the entry's 9 criteria. Pure pandas, no LLM. The score
(plus a hard stop hit from risk.py) sets the scale-out stage; the LLM later only narrates it.

Mirror map vs events.py:  Spring↔Upthrust · SOS↔SOW · LPS↔LPSY · holds-above-breakout↔support-break.
"""
from __future__ import annotations

import pandas as pd

from events import detect_range  # reuse the percentile-band trading range

UT_PIERCE = 1.01          # high pierces resistance * this, then closes back below (failed breakout up)
SOW_DROP = 0.04           # single-bar decline
SOW_VOL_X = 1.5           # on elevated volume vs 20d avg
SOW_CUM_BARS = 3          # multi-bar window
SOW_CUM_DROP = 0.06
SOW_CUM_VOL_X = 1.3
LPSY_VOL_X = 0.7          # weak (contracting-volume) rally that fails to reclaim mid
MA_LEN = 50
OFF_HIGH_GIVEBACK = 0.07  # given back >7% from the recent highest-high
SCAN = 60
MIN_MARKDOWN_LOSS = 0.08  # established-markdown floor only fires when down more than this (skip shallow basers)
THIN_VOL_FRAC = 0.30      # >=30% of recent bars near-zero volume -> the volume criteria are unreliable


def detect_upthrust(df: pd.DataFrame, rng: dict | None) -> dict | None:
    if not rng:
        return None
    res = rng["resistance"]
    high, close, idx = df["high"].values, df["close"].values, df.index
    n = len(df)
    for i in range(n - 1, max(0, n - SCAN) - 1, -1):
        if high[i] > res * UT_PIERCE and close[i] < res:
            confirmed = any(close[j] < close[i] for j in range(i + 1, min(i + 4, n)))
            return {"date": str(idx[i]), "high": round(float(high[i]), 2), "confirmed": confirmed, "i": i}
    return None


def detect_sow(df: pd.DataFrame, rng: dict | None) -> dict | None:
    """Strong decline on elevated volume, breaking below range mid/support (mirror of SOS)."""
    if not rng:
        return None
    mid, support = rng["mid"], rng["support"]
    close, low, vol, idx = df["close"].values, df["low"].values, df["volume"].values, df.index
    v20 = df["volume"].rolling(20).mean().values
    n = len(df)
    for i in range(n - 1, max(SOW_CUM_BARS, n - SCAN) - 1, -1):
        if pd.isna(v20[i]) or v20[i] <= 0 or close[i - 1] <= 0:
            continue
        single = (close[i - 1] - close[i]) / close[i - 1] > SOW_DROP and vol[i] > SOW_VOL_X * v20[i] and close[i] <= mid
        multi = False
        if i >= SOW_CUM_BARS and close[i - SOW_CUM_BARS] > 0:
            cum = (close[i - SOW_CUM_BARS] - close[i]) / close[i - SOW_CUM_BARS]
            vavg = float(vol[i - SOW_CUM_BARS + 1:i + 1].mean())
            multi = cum > SOW_CUM_DROP and close[i] < support and vavg > SOW_CUM_VOL_X * v20[i]
        if single or multi:
            drop = (close[i - 1] - close[i]) / close[i - 1] if single else (close[i - SOW_CUM_BARS] - close[i]) / close[i - SOW_CUM_BARS]
            return {"date": str(idx[i]), "drop_pct": round(float(drop * 100), 1),
                    "vol_x": round(float(vol[i] / v20[i]), 1), "low": round(float(low[i]), 2),
                    "kind": "single" if single else "multi", "i": i}
    return None


def detect_lpsy(df: pd.DataFrame, rng: dict | None, sow: dict | None) -> dict | None:
    """Weak, low-volume rally after a SOW that fails to reclaim range mid (mirror of LPS)."""
    if not rng or not sow:
        return None
    mid = rng["mid"]
    close, vol, idx = df["close"].values, df["volume"].values, df.index
    sow_i = sow["i"]
    sow_vol = vol[sow_i]
    n = len(df)
    for j in range(sow_i + 1, n):
        if close[j] < mid and sow_vol > 0 and vol[j] < LPSY_VOL_X * sow_vol and close[j] > close[sow_i]:
            return {"date": str(idx[j]), "close": round(float(close[j]), 2),
                    "vol_x_sow": round(float(vol[j] / sow_vol), 2)}
    return None


def detect_support_break(df: pd.DataFrame, rng: dict | None) -> dict | None:
    """Close below support. With a range, that's the range floor; without one (a name already in a
    markdown, so no horizontal base) it's a fresh close below the prior 20-bar swing low on elevated
    volume — a range-less SOW the range-dependent detectors would otherwise miss."""
    cur = float(df["close"].iloc[-1])
    if rng:
        if cur < rng["support"]:
            return {"date": str(df.index[-1]), "close": round(cur, 2), "support": rng["support"], "kind": "range"}
        return None
    if len(df) < 25:
        return None
    v20 = df["volume"].rolling(20).mean().iloc[-1]
    prior_low = float(df["low"].values[-21:-1].min())
    if cur < prior_low and pd.notna(v20) and v20 > 0 and df["volume"].values[-1] > SOW_VOL_X * v20:
        return {"date": str(df.index[-1]), "close": round(cur, 2), "support": round(prior_low, 2), "kind": "swing-low"}
    return None


REL_WEAK_GAP = 0.10   # underperform SPY by >10pp over the shared lookback window = relative weakness


def _rel_weak(df: pd.DataFrame, market_ctx) -> bool:
    """Instrument materially weaker than the market: its return over the shared lookback window trails
    SPY's by more than REL_WEAK_GAP. Needs market_ctx['spy_window_return'] (SPY's same-window return)."""
    if not market_ctx or market_ctx.get("spy_window_return") is None:
        return False
    c = df["close"]
    if len(c) < 30 or float(c.iloc[0]) <= 0:
        return False
    inst = float(c.iloc[-1] / c.iloc[0] - 1)
    return (inst - float(market_ctx["spy_window_return"])) < -REL_WEAK_GAP


def _ma_rollover(df: pd.DataFrame) -> bool:
    c = df["close"]
    if len(c) < MA_LEN + 10:
        return False
    ma = c.rolling(MA_LEN).mean()
    return bool(c.iloc[-1] < ma.iloc[-1] and ma.iloc[-1] < ma.iloc[-10])


def _distribution_volume(df: pd.DataFrame) -> bool:
    """Over the last ~20 bars, down-day volume exceeds up-day volume (selling pressure)."""
    w = df.tail(20)
    if len(w) < 10:
        return False
    chg = w["close"].diff()
    up_v, dn_v = w["volume"][chg > 0].mean(), w["volume"][chg < 0].mean()
    if pd.isna(dn_v):
        return False          # no down-days
    if pd.isna(up_v):
        return True           # all down-days = pure distribution
    return bool(dn_v > up_v)


def _thin_volume(df: pd.DataFrame) -> bool:
    """Volume data is degenerate (many ~0-volume bars) -> the volume-based criteria can't be trusted.
    Common in thinly-traded ILS names."""
    v = df["volume"].tail(20)
    if len(v) < 10:
        return False
    med = float(v.median())
    if med <= 0:
        return True
    return int((v <= 0.1 * med).sum()) >= THIN_VOL_FRAC * len(v)


def _price_distribution(df: pd.DataFrame) -> bool:
    """Volume-independent distribution: recent lower highs AND lower lows (markdown structure)."""
    if len(df) < 20:
        return False
    recent, prior = df.tail(10), df.iloc[-20:-10]
    return bool(recent["high"].max() < prior["high"].max() and recent["low"].min() < prior["low"].min())


def _selling_pressure(df: pd.DataFrame) -> bool:
    """Distribution-volume normally; falls back to price structure when volume is too thin to trust."""
    return _price_distribution(df) if _thin_volume(df) else _distribution_volume(df)


def _off_highs(df: pd.DataFrame) -> bool:
    hh = float(df["high"].tail(SCAN).max())
    return bool(hh > 0 and float(df["close"].iloc[-1]) < hh * (1 - OFF_HIGH_GIVEBACK))


def _established_markdown(df: pd.DataFrame, loss_pct: float | None) -> bool:
    """A confirmed, *active* downtrend you're MATERIALLY underwater on: below the medium MA AND still
    making fresh lows (near the window low, not basing off it) AND down more than MIN_MARKDOWN_LOSS.
    Position-aware so a long-term winner in a normal pullback — or a shallow dip — isn't flagged.
    Used as a scale-out FLOOR, not a score bump."""
    if loss_pct is None or loss_pct > -MIN_MARKDOWN_LOSS:
        return False
    c = df["close"]
    if len(c) < MA_LEN + 5:
        return False
    below_ma = float(c.iloc[-1]) < float(c.rolling(MA_LEN).mean().iloc[-1])
    window_low = float(df["low"].min())
    fresh_low = window_low > 0 and float(c.iloc[-1]) <= window_low * 1.03
    return bool(below_ma and fresh_low)


def deterioration_score(df: pd.DataFrame, market_ctx: dict | None = None, loss_pct: float | None = None) -> dict:
    rng = detect_range(df)
    ut = detect_upthrust(df, rng)
    sow = detect_sow(df, rng)
    lpsy = detect_lpsy(df, rng, sow)
    brk = detect_support_break(df, rng)
    thin = _thin_volume(df)
    crit = {
        "upthrust": bool(ut),
        "utad": bool(ut and ut.get("confirmed") and rng and rng.get("resistance_clusters", 0) >= 2),
        "sow": bool(sow),
        "lpsy": bool(lpsy),
        "support_break": bool(brk),
        "rel_weak": _rel_weak(df, market_ctx),
        "ma_rollover": _ma_rollover(df),
        "distribution_volume": _selling_pressure(df),       # price-structure fallback when volume is thin
        "off_highs": _off_highs(df),
    }
    signals = [k for k, v in crit.items() if v]
    established = _established_markdown(df, loss_pct)
    if established:
        signals.append("established_markdown")
    if thin:
        signals.append("thin_volume")
    return {
        "score": sum(1 for v in crit.values() if v),
        "criteria": crit,
        "signals": signals,
        "established_markdown": established,                 # scale-out FLOOR (not counted in score)
        "has_structural": bool(ut or sow or lpsy or brk),   # a real distribution top -> uncaps the ladder
        "thin_volume": thin,
        "events": {"upthrust": ut, "sow": sow, "lpsy": lpsy, "support_break": brk, "range": rng},
    }


def score_to_stage(score: int, stop_hit: bool = False) -> tuple[int, int]:
    """Exit score (+ hard stop) -> (stage, target % of baseline qty). Ratchet down only, caller-enforced."""
    if stop_hit or score >= 7:
        return 3, 0
    if score >= 5:
        return 2, 50
    if score >= 3:
        return 1, 75
    return 0, 100


if __name__ == "__main__":  # self-test: synthetic data
    import numpy as np

    # 1) clean uptrend -> low score, no scale-out
    up = pd.Series(100 + np.arange(60) * 0.5)
    df_up = pd.DataFrame({"high": up + 0.8, "low": up - 0.8, "close": up, "volume": pd.Series([1000] * 60)})
    r_up = deterioration_score(df_up)
    assert r_up["score"] <= 1, f"healthy uptrend should score low, got {r_up['score']}"
    assert score_to_stage(r_up["score"])[1] == 100

    # 2) top then rollover with heavier down-volume -> elevated score, scale-out
    rise = np.linspace(100, 120, 30)
    fall = np.linspace(120, 90, 70)
    c = pd.Series(np.concatenate([rise, fall]))
    vol = pd.Series([1000] * 30 + [1800] * 70)            # distribution: heavier volume on the decline
    df_dn = pd.DataFrame({"high": c + 0.8, "low": c - 0.8, "close": c, "volume": vol})
    r_dn = deterioration_score(df_dn)
    assert r_dn["score"] >= 3, f"rollover should score >=3, got {r_dn['score']} ({r_dn['signals']})"
    stage, target = score_to_stage(r_dn["score"])
    assert stage >= 1 and target <= 75

    # 2b) materially underwater in that active downtrend -> established-markdown floor
    assert deterioration_score(df_dn, loss_pct=-0.20)["established_markdown"], "deep downtrend at a loss = markdown"
    assert not deterioration_score(df_dn, loss_pct=-0.03)["established_markdown"], "shallow loss must NOT trip the floor"
    assert not deterioration_score(df_up, loss_pct=-0.20)["established_markdown"], "uptrend is not a markdown"

    # 2c) thin volume -> flagged, and the volume criterion falls back to price-structure (no crash)
    thin_df = df_dn.copy()
    thin_df.iloc[-12:, thin_df.columns.get_loc("volume")] = 0
    assert deterioration_score(thin_df, loss_pct=-0.20)["thin_volume"], "many 0-volume bars -> thin_volume"

    # 2d) range-less high-volume breakdown -> support_break fires WITHOUT a range (the MBLY gap)
    bd_c = np.concatenate([np.linspace(100, 105, 45), [99.0]])
    bd = pd.DataFrame({"high": bd_c + 0.5, "low": bd_c - 0.5, "close": bd_c,
                       "volume": np.array([1000.0] * 45 + [3000.0])})
    sb = detect_support_break(bd, None)
    assert sb is not None and sb["kind"] == "swing-low", f"range-less breakdown should fire, got {sb}"

    # 2e) relative weakness: instrument lagging SPY by >10pp over the shared window
    assert deterioration_score(df_dn, market_ctx={"spy_window_return": 0.05})["criteria"]["rel_weak"], "lagging SPY -> rel_weak"
    assert not deterioration_score(df_up, market_ctx={"spy_window_return": 0.05})["criteria"]["rel_weak"], "leading SPY -> not rel_weak"

    # 3) stage mapping + hard-stop override
    assert score_to_stage(2) == (0, 100) and score_to_stage(4) == (1, 75)
    assert score_to_stage(5) == (2, 50) and score_to_stage(7) == (3, 0)
    assert score_to_stage(0, stop_hit=True) == (3, 0)

    print("uptrend:", r_up["score"], r_up["signals"])
    print("rollover:", r_dn["score"], r_dn["signals"], "-> stage", stage, f"({target}%)")
    print("[self-test OK]")
