#!/usr/bin/env python3
"""A bank of technical entry/exit detectors, all sharing one signature.

Every detector answers a single question at one point in time: "does this pattern hold as
of bar i?" Features are precomputed once per ticker so detectors are cheap index lookups
rather than repeated rolling-window maths.

Detectors are grouped by what they claim:
  TREND      — the move is established and continuing (attack)
  PULLBACK   — a dip inside an established uptrend (attack, better entry)
  SQUEEZE    — volatility/range contraction preceding expansion (attack)
  VOLUME     — participation confirms or contradicts price
  REVERSION  — stretched to the downside, expect a bounce (attack in range markets)
  DEFENSE    — conditions that should predict WEAKNESS (exit/avoid signals)

A DEFENSE detector is "good" when forward excess return is strongly NEGATIVE.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# --- feature precomputation --------------------------------------------------------


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    f = pd.DataFrame(index=df.index)
    f["close"], f["high"], f["low"], f["volume"] = c, h, l, v

    for n in (10, 20, 50, 150, 200):
        f[f"ma{n}"] = c.rolling(n).mean()
    f["ema10"] = c.ewm(span=10, adjust=False).mean()
    f["ema20"] = c.ewm(span=20, adjust=False).mean()

    f["hi20"] = h.rolling(20).max()
    f["hi55"] = h.rolling(55).max()
    f["hi252"] = c.rolling(252).max()
    f["lo20"] = l.rolling(20).min()
    f["lo50"] = l.rolling(50).min()
    f["lo252"] = c.rolling(252).min()

    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    f["atr14"] = tr.rolling(14).mean()
    f["atr_pct"] = f["atr14"] / c
    f["atr_pct_lo60"] = f["atr_pct"].rolling(60).min()

    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    f["rsi14"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    g2 = delta.clip(lower=0).rolling(2).mean()
    l2 = (-delta.clip(upper=0)).rolling(2).mean()
    f["rsi2"] = 100 - 100 / (1 + g2 / l2.replace(0, np.nan))

    sd20 = c.rolling(20).std()
    f["bb_up"] = f["ma20"] + 2 * sd20
    f["bb_dn"] = f["ma20"] - 2 * sd20
    f["bb_width"] = (f["bb_up"] - f["bb_dn"]) / f["ma20"]
    f["bb_width_lo120"] = f["bb_width"].rolling(120).min()
    f["kc_up"] = f["ema20"] + 1.5 * f["atr14"]
    f["kc_dn"] = f["ema20"] - 1.5 * f["atr14"]

    f["vol50"] = v.rolling(50).mean()
    f["vol20"] = v.rolling(20).mean()
    f["vol_ratio"] = v / f["vol50"]
    direction = np.sign(delta).fillna(0)
    f["obv"] = (direction * v).cumsum()
    f["obv_hi60"] = f["obv"].rolling(60).max()

    f["rng"] = h - l
    f["rng_min7"] = f["rng"].rolling(7).min()
    f["close_pos"] = (c - l) / (h - l).replace(0, np.nan)   # where in the bar it closed

    f["ret21"] = c.pct_change(21)
    f["ret63"] = c.pct_change(63)
    f["ret126"] = c.pct_change(126)
    f["ret252"] = c.pct_change(252)
    f["mom_12_1"] = (c.shift(21) / c.shift(252) - 1)        # 12-month, skipping last month
    f["dd"] = c / f["hi252"] - 1                            # drawdown from 52w high
    f["ma200_slope"] = f["ma200"] / f["ma200"].shift(20) - 1
    f["ma50_slope"] = f["ma50"] / f["ma50"].shift(10) - 1

    # down-day-on-higher-volume count over 25 sessions (O'Neil distribution days)
    down_hi_vol = ((delta < 0) & (v > v.shift())).astype(int)
    f["dist_days"] = down_hi_vol.rolling(25).sum()

    return f


# --- helpers -----------------------------------------------------------------------

def _ok(*vals) -> bool:
    return all(v is not None and not (isinstance(v, float) and np.isnan(v)) for v in vals)


def _uptrend(f, i) -> bool:
    """Shared precondition: price above a rising 200-day average."""
    return (_ok(f.close[i], f.ma200[i], f.ma200_slope[i])
            and f.close[i] > f.ma200[i] and f.ma200_slope[i] > 0)


# --- TREND -------------------------------------------------------------------------

def golden_cross(f, i):
    if i < 5 or not _ok(f.ma50[i], f.ma200[i], f.ma50[i - 5], f.ma200[i - 5]):
        return False
    return f.ma50[i] > f.ma200[i] and f.ma50[i - 5] <= f.ma200[i - 5]


def above_rising_200(f, i):
    return _uptrend(f, i)


def donchian_20(f, i):
    return _ok(f.close[i], f.hi20[i]) and f.close[i] >= f.hi20[i] * 0.999


def donchian_55(f, i):
    return _ok(f.close[i], f.hi55[i]) and f.close[i] >= f.hi55[i] * 0.999


def new_52w_high(f, i):
    return _ok(f.close[i], f.hi252[i]) and f.close[i] >= f.hi252[i] * 0.999


def minervini_template(f, i):
    """The eight-point trend template: stacked averages, rising 200, well off the low,
    within striking distance of the high."""
    if not _ok(f.close[i], f.ma50[i], f.ma150[i], f.ma200[i], f.ma200_slope[i],
               f.lo252[i], f.hi252[i]):
        return False
    return (f.close[i] > f.ma150[i] and f.close[i] > f.ma200[i]
            and f.ma150[i] > f.ma200[i]
            and f.ma200_slope[i] > 0
            and f.ma50[i] > f.ma150[i] and f.ma50[i] > f.ma200[i]
            and f.close[i] > f.ma50[i]
            and f.close[i] >= f.lo252[i] * 1.30
            and f.close[i] >= f.hi252[i] * 0.75)


def mom_12_1_strong(f, i):
    return _ok(f.mom_12_1[i]) and f.mom_12_1[i] > 0.30


def pocket_pivot(f, i):
    """An up day whose volume exceeds the largest down-day volume of the prior 10 —
    demand outpacing the recent supply, before the breakout is obvious."""
    if i < 11 or not _ok(f.close[i], f.close[i - 1], f.ma50[i]):
        return False
    if f.close[i] <= f.close[i - 1] or f.close[i] < f.ma50[i]:
        return False
    down_vols = [f.volume[j] for j in range(i - 10, i)
                 if _ok(f.close[j], f.close[j - 1]) and f.close[j] < f.close[j - 1]]
    return bool(down_vols) and f.volume[i] > max(down_vols)


def gap_up_volume(f, i):
    if i < 1 or not _ok(f.low[i], f.high[i - 1], f.vol_ratio[i]):
        return False
    return f.low[i] > f.high[i - 1] * 1.005 and f.vol_ratio[i] > 1.5


# --- PULLBACK ----------------------------------------------------------------------

def pullback_ema20(f, i):
    if not _uptrend(f, i) or not _ok(f.low[i], f.ema20[i], f.close[i]):
        return False
    return f.low[i] <= f.ema20[i] and f.close[i] > f.ema20[i]


def pullback_ma50(f, i):
    if not _uptrend(f, i) or not _ok(f.low[i], f.ma50[i], f.close[i]):
        return False
    return f.low[i] <= f.ma50[i] * 1.01 and f.close[i] > f.ma50[i]


def three_day_pullback(f, i):
    if i < 3 or not _uptrend(f, i):
        return False
    return all(_ok(f.close[j], f.close[j - 1]) and f.close[j] < f.close[j - 1]
               for j in range(i - 2, i + 1))


def rsi2_oversold_uptrend(f, i):
    return _uptrend(f, i) and _ok(f.rsi2[i]) and f.rsi2[i] < 10


def rsi14_pullback_uptrend(f, i):
    return _uptrend(f, i) and _ok(f.rsi14[i]) and f.rsi14[i] < 40


def bollinger_lower_uptrend(f, i):
    return _uptrend(f, i) and _ok(f.close[i], f.bb_dn[i]) and f.close[i] <= f.bb_dn[i]


# --- SQUEEZE / CONTRACTION ---------------------------------------------------------

def vcp(f, i):
    """Volatility contraction: ATR at a 60-day low, volume drying up, price holding
    near the highs. Minervini's pattern, expressed quantitatively."""
    if not _ok(f.atr_pct[i], f.atr_pct_lo60[i], f.vol_ratio[i], f.dd[i], f.ma50[i], f.close[i]):
        return False
    return (f.atr_pct[i] <= f.atr_pct_lo60[i] * 1.10
            and f.vol_ratio[i] < 0.85
            and f.dd[i] > -0.15
            and f.close[i] > f.ma50[i])


def nr7(f, i):
    return _ok(f.rng[i], f.rng_min7[i]) and f.rng[i] <= f.rng_min7[i]


def inside_bar_uptrend(f, i):
    if i < 1 or not _uptrend(f, i) or not _ok(f.high[i], f.low[i], f.high[i - 1], f.low[i - 1]):
        return False
    return f.high[i] < f.high[i - 1] and f.low[i] > f.low[i - 1]


def ttm_squeeze(f, i):
    """Bollinger bands inside Keltner channels — volatility compressed, expansion pending."""
    return (_ok(f.bb_up[i], f.bb_dn[i], f.kc_up[i], f.kc_dn[i])
            and f.bb_up[i] < f.kc_up[i] and f.bb_dn[i] > f.kc_dn[i])


def bb_width_low(f, i):
    return (_ok(f.bb_width[i], f.bb_width_lo120[i])
            and f.bb_width[i] <= f.bb_width_lo120[i] * 1.05)


# --- VOLUME ------------------------------------------------------------------------

def obv_new_high(f, i):
    return _ok(f.obv[i], f.obv_hi60[i]) and f.obv[i] >= f.obv_hi60[i]


def volume_dryup_near_high(f, i):
    if i < 3 or not _ok(f.dd[i]):
        return False
    quiet = all(_ok(f.vol_ratio[j]) and f.vol_ratio[j] < 0.7 for j in range(i - 2, i + 1))
    return quiet and f.dd[i] > -0.10


def accumulation_day(f, i):
    return (_ok(f.close_pos[i], f.vol_ratio[i])
            and f.close_pos[i] > 0.75 and f.vol_ratio[i] > 1.25)


# --- REVERSION ---------------------------------------------------------------------

def rsi14_oversold(f, i):
    return _ok(f.rsi14[i]) and f.rsi14[i] < 30


def bounce_off_200(f, i):
    if not _ok(f.low[i], f.ma200[i], f.close[i], f.ma200_slope[i]):
        return False
    return (f.ma200_slope[i] > 0 and f.low[i] <= f.ma200[i] * 1.02
            and f.close[i] > f.ma200[i])


def deep_oversold_downtrend(f, i):
    return (_ok(f.rsi14[i], f.close[i], f.ma200[i])
            and f.rsi14[i] < 25 and f.close[i] < f.ma200[i])


# --- DEFENSE (should predict weakness) ---------------------------------------------

def death_cross(f, i):
    if i < 5 or not _ok(f.ma50[i], f.ma200[i], f.ma50[i - 5], f.ma200[i - 5]):
        return False
    return f.ma50[i] < f.ma200[i] and f.ma50[i - 5] >= f.ma200[i - 5]


def below_falling_200(f, i):
    return (_ok(f.close[i], f.ma200[i], f.ma200_slope[i])
            and f.close[i] < f.ma200[i] and f.ma200_slope[i] < 0)


def new_52w_low(f, i):
    return _ok(f.close[i], f.lo252[i]) and f.close[i] <= f.lo252[i] * 1.001


def distribution_cluster(f, i):
    return _ok(f.dist_days[i]) and f.dist_days[i] >= 8


def breakdown_50day_low(f, i):
    return _ok(f.close[i], f.lo50[i]) and f.close[i] <= f.lo50[i] * 1.001


def volatility_expansion(f, i):
    if i < 20 or not _ok(f.atr_pct[i], f.atr_pct[i - 20]):
        return False
    return f.atr_pct[i] > f.atr_pct[i - 20] * 1.75


def failed_breakout(f, i):
    """Made a 52-week high within 15 bars, now back below the pre-breakout ceiling —
    the bull trap. Classically the most reliable bearish structure."""
    if i < 30 or not _ok(f.close[i]):
        return False
    recent_high_bar = None
    for j in range(i - 15, i):
        if _ok(f.close[j], f.hi252[j]) and f.close[j] >= f.hi252[j] * 0.999:
            recent_high_bar = j
    if recent_high_bar is None:
        return False
    prior_ceiling = f.hi252[recent_high_bar - 1]
    return _ok(prior_ceiling) and f.close[i] < prior_ceiling * 0.97


REGISTRY = {
    "TREND": [golden_cross, above_rising_200, donchian_20, donchian_55, new_52w_high,
              minervini_template, mom_12_1_strong, pocket_pivot, gap_up_volume],
    "PULLBACK": [pullback_ema20, pullback_ma50, three_day_pullback, rsi2_oversold_uptrend,
                 rsi14_pullback_uptrend, bollinger_lower_uptrend],
    "SQUEEZE": [vcp, nr7, inside_bar_uptrend, ttm_squeeze, bb_width_low],
    "VOLUME": [obv_new_high, volume_dryup_near_high, accumulation_day],
    "REVERSION": [rsi14_oversold, bounce_off_200, deep_oversold_downtrend],
    "DEFENSE": [death_cross, below_falling_200, new_52w_low, distribution_cluster,
                breakdown_50day_low, volatility_expansion, failed_breakout],
}

ALL = {fn.__name__: (group, fn) for group, fns in REGISTRY.items() for fn in fns}
