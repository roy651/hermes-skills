#!/usr/bin/env python3
"""Ground-truth calibration harness for events.py (Tier 1: synthetic boundary pairs).

For each decision threshold in events.py we hand-build a *matched pair* of OHLCV fixtures
that straddle the boundary (one just inside, one just outside) and label the expected
`range/spring/sos/lps`, `event_score`, and `has_entry_event`. The harness asserts every
label and prints a confusion matrix for `has_entry_event` (the FP/FN we actually care about).

Pass bar:
  * every fixture matches its label exactly, and
  * ZERO false positives on the `dist` (distribution / failed-breakout) class.

Deterministic + offline (no network, no randomness).
Run:  .venv/bin/python tests/validate_events.py

Tier 2 (curated real historical windows) is added separately as committed CSV fixtures.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import pandas as pd
import events


# ── bar builders ──────────────────────────────────────────────────────────────

def _bar(o, h, l, c, v):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _hover(rows, level, n, spread=0.5, vol=1_000_000):
    for _ in range(n):
        rows.append(_bar(level, level + spread, level - spread, level, vol))


def _ramp(rows, a, b, n, vol=1_000_000):
    for k in range(n):
        p = a + (b - a) * ((k + 1) / n)
        rows.append(_bar(p, p + 0.4, p - 0.4, p, vol))


def _last(rows):
    return rows[-1]["close"]


def _lift(rows, target, n=3, vol=1_000_000):
    _ramp(rows, _last(rows), target, n, vol)


def _df(rows):
    return pd.DataFrame(rows, index=pd.date_range("2026-01-01", periods=len(rows), freq="D"))


def _base_range(rows):
    """Downtrend into a clean 3-wave range ~80–92 (support q10≈80, resistance q90≈92, mid≈86),
    with 3 time-separated touches of each band and no Spring (lows never pierce support)."""
    for i in range(30):
        p = 100 - 18 * (i / 29)
        rows.append(_bar(p, p + 0.4, p - 0.4, p, 1_000_000))
    for lo, hi in [(80.5, 91.5), (80.7, 91.8), (80.6, 91.6)]:
        _hover(rows, lo, 4)
        _ramp(rows, lo, hi, 4)
        _hover(rows, hi, 4)
        _ramp(rows, hi, lo, 4)


# ── fixture builders (each returns a DataFrame) ────────────────────────────────

# SOS single-bar gain threshold (SOS_GAIN=0.04), vol fixed at 1.6x
def f_sos_gain_over():
    r = []; _base_range(r); _lift(r, 84, 3)
    r.append(_bar(84, 88, 84, 84 * 1.042, 1_600_000))      # +4.2% > 4%, vol 1.6x, close 87.5 ≥ mid
    return _df(r)


def f_sos_gain_under():
    r = []; _base_range(r); _lift(r, 84, 3)
    r.append(_bar(84, 88, 84, 84 * 1.038, 1_600_000))      # +3.8% < 4% (and no multi: < resistance)
    return _df(r)


# SOS single-bar volume threshold (SOS_VOL_X=1.5), gain fixed at +4.5%
def f_sos_vol_over():
    r = []; _base_range(r); _lift(r, 84, 3)
    r.append(_bar(84, 88, 84, 84 * 1.045, 1_600_000))      # 1.6x > 1.5x
    return _df(r)


def f_sos_vol_under():
    r = []; _base_range(r); _lift(r, 84, 3)
    r.append(_bar(84, 88, 84, 84 * 1.045, 1_400_000))      # 1.4x < 1.5x
    return _df(r)


# SOS multi-bar (SOS_CUM_GAIN=0.06 / clears resistance / SOS_CUM_VOL_X=1.3), no single +4% bar
def f_sos_cum_over():
    r = []; _base_range(r); _lift(r, 86, 3)
    r.append(_bar(86, 88.5, 86, 88.0, 1_400_000))          # +2.3%
    r.append(_bar(88, 91.0, 88, 90.5, 1_400_000))          # +2.8%
    r.append(_bar(90.5, 93.5, 90.5, 93.0, 1_400_000))      # +2.8%; 3-bar +8.1% clears resistance 92
    return _df(r)


def f_sos_cum_under():
    r = []; _base_range(r); _lift(r, 86, 3)
    r.append(_bar(86, 87.5, 86, 87.3, 1_400_000))          # 3-bar cum ≈ +5.7% (< 6%) and < resistance
    r.append(_bar(87.3, 88.5, 87.3, 88.2, 1_400_000))
    r.append(_bar(88.2, 91.5, 88.2, 90.9, 1_400_000))
    return _df(r)


# Touch clusters (MIN_TOUCH_CLUSTERS=2 / TOUCH_CLUSTER_GAP=5) — range present vs not
def f_clusters_over():
    r = []
    _hover(r, 80.5, 4); _ramp(r, 80.5, 92, 4); _hover(r, 92, 4); _ramp(r, 92, 80.5, 4)
    _hover(r, 80.5, 4); _ramp(r, 80.5, 92, 4); _hover(r, 92, 4)   # 2 visits each, ~8 bars apart
    return _df(r)


def f_clusters_under():
    r = []
    _hover(r, 92, 8); _ramp(r, 92, 80.5, 4); _hover(r, 80.5, 6); _ramp(r, 80.5, 92, 4); _hover(r, 92, 8)
    return _df(r)   # support touched once (1 cluster) → range rejected


# Range width (RANGE_MAX_WIDTH=0.20)
def _two_wave(support, resistance):
    r = []
    _hover(r, support, 4); _ramp(r, support, resistance, 4); _hover(r, resistance, 4)
    _ramp(r, resistance, support, 4); _hover(r, support, 4); _ramp(r, support, resistance, 4)
    _hover(r, resistance, 4)
    return r


def f_width_over():
    return _df(_two_wave(80.0, 80.0 * 1.18))   # 18% < 20% → range


def f_width_under():
    return _df(_two_wave(80.0, 80.0 * 1.22))   # 22% > 20% → no range


# Spring pierce (SPRING_PIERCE=0.99) — support q10 ≈ 80, so support*0.99 ≈ 79.2
def f_spring_over():
    r = []; _base_range(r)
    r.append(_bar(81, 82, 80.0 * 0.985, 82.0, 1_300_000))   # low 78.8 < 79.2, closes above support
    r.append(_bar(82, 83.5, 81.5, 83.0, 1_100_000))
    return _df(r)


def f_spring_under():
    r = []; _base_range(r)
    r.append(_bar(81, 82, 80.0 * 0.995, 81.5, 1_300_000))   # low 79.6 > 79.2 → no spring
    r.append(_bar(81.5, 82.5, 81.0, 82.0, 1_100_000))
    return _df(r)


# LPS volume (LPS_VOL_X=0.7) — needs a valid SOS first (sos_vol = 1.6M)
def _range_then_sos(r):
    _base_range(r); _lift(r, 84, 3)
    r.append(_bar(84, 88.5, 84, 88.0, 1_600_000))          # single SOS, high 88.5, vol 1.6M


def f_lps_over():
    r = []; _range_then_sos(r)
    r.append(_bar(88, 88.3, 86.5, 87.0, 1_040_000))        # 0.65x SOS vol → LPS (holds > mid, < SOS high)
    return _df(r)


def f_lps_under():
    r = []; _range_then_sos(r)
    r.append(_bar(88, 88.3, 86.5, 87.0, 1_200_000))        # 0.75x SOS vol → no LPS (SOS still present)
    return _df(r)


# Chronology (spring_i vs sos_i)
def f_chrono_valid():
    r = []; _base_range(r)
    r.append(_bar(81, 82, 78.8, 82.0, 1_300_000))          # Spring
    r.append(_bar(82, 84, 81.5, 83.5, 1_100_000)); r.append(_bar(83.5, 85, 83, 84.0, 1_000_000))
    r.append(_bar(84, 88.5, 84, 88.0, 1_600_000))          # SOS after Spring → valid
    return _df(r)


def f_chrono_invalid():
    r = []; _base_range(r); _lift(r, 84, 3)
    r.append(_bar(84, 88.5, 84, 88.0, 1_600_000))          # SOS first
    _ramp(r, 88, 81, 6); _hover(r, 81, 3)
    r.append(_bar(81, 82, 78.8, 82.0, 1_300_000))          # Spring LATER → SOS must be dropped
    r.append(_bar(82, 83.5, 81.5, 83.0, 1_100_000))
    return _df(r)


def f_uptrend():
    return _df([_bar(p, p + 1, p - 1, p, 1_000_000) for p in (50 + 50 * (i / 119) for i in range(120))])


# Markup-pullback lane (Option 2). Large breakouts isolate it from the range lane (tail-60
# width > 20% → no range), so only `markup_pullback` should fire.
def _markup_base(rows):
    for _ in range(4):
        _hover(rows, 90, 6); _ramp(rows, 90, 99.5, 4); _hover(rows, 99.5, 4); _ramp(rows, 99.5, 90, 4)


def f_mp_over():
    r = []; _markup_base(r)
    _ramp(r, 90, 120, 6, vol=1_800_000)          # breakout well above ceiling 100, peak ~120
    _ramp(r, 120, 106, 4, vol=700_000)           # pullback holds above breakout 100, contracting vol
    return _df(r)


def f_mp_extended():
    r = []; _markup_base(r)
    _ramp(r, 90, 120, 6, vol=1_800_000)
    _hover(r, 119, 6, vol=900_000)               # stays at the highs, no pullback (isolates: no range)
    return _df(r)


def f_mp_failed():
    r = []; _markup_base(r)
    _ramp(r, 90, 120, 6, vol=1_800_000)
    _ramp(r, 120, 95, 6, vol=1_500_000)          # fell back below breakout 100 → failed breakout
    return _df(r)


def f_mp_vol_under():
    r = []; _markup_base(r)
    _ramp(r, 90, 120, 6, vol=1_800_000)
    _ramp(r, 120, 106, 4, vol=1_700_000)         # pullback but volume NOT contracting (~0.9x rally)
    return _df(r)


def f_mp_deep():
    r = []; _markup_base(r)
    _ramp(r, 90, 130, 6, vol=1_800_000)          # big breakout, peak ~130
    _ramp(r, 130, 105, 5, vol=700_000)           # deep give-back ~19% off peak (> MAX) though still > breakout
    return _df(r)


# ── corpus: (name, builder, expected, class) ───────────────────────────────────
# expected may be partial — only the keys present are asserted.
def _exp(rng, sp, so, lp, score, has, mp=False):
    return {"range": rng, "spring": sp, "sos": so, "lps": lp, "score": score, "has": has, "mp": mp}


CORPUS = [
    ("sos_gain_over",  f_sos_gain_over,  _exp(True, False, True, False, 2, True),  "pos"),
    ("sos_gain_under", f_sos_gain_under, _exp(True, False, False, False, 1, False), "neg"),
    ("sos_vol_over",   f_sos_vol_over,   _exp(True, False, True, False, 2, True),  "pos"),
    ("sos_vol_under",  f_sos_vol_under,  _exp(True, False, False, False, 1, False), "neg"),
    ("sos_cum_over",   f_sos_cum_over,   _exp(True, False, True, False, 2, True),  "pos"),
    ("sos_cum_under",  f_sos_cum_under,  _exp(True, False, False, False, 1, False), "neg"),
    ("clusters_over",  f_clusters_over,  _exp(True, False, False, False, 1, False), "neg"),
    ("clusters_under", f_clusters_under, _exp(False, False, False, False, 0, False), "neg"),
    ("width_over",     f_width_over,     _exp(True, False, False, False, 1, False), "neg"),
    ("width_under",    f_width_under,    _exp(False, False, False, False, 0, False), "neg"),
    ("spring_over",    f_spring_over,    _exp(True, True, False, False, 2, False), "neg"),
    ("spring_under",   f_spring_under,   _exp(True, False, False, False, 1, False), "neg"),
    ("lps_over",       f_lps_over,       _exp(True, False, True, True, 3, True),  "pos"),
    ("lps_under",      f_lps_under,      _exp(True, False, True, False, 2, True),  "pos"),
    ("chrono_valid",   f_chrono_valid,   _exp(True, True, True, False, 3, True),  "pos"),
    ("chrono_invalid", f_chrono_invalid, _exp(True, True, False, False, 2, False), "dist"),
    ("uptrend",        f_uptrend,        _exp(False, False, False, False, 0, False), "neg"),
    # Markup-pullback lane (partial expectations: range isolated out)
    ("mp_over",        f_mp_over,        {"range": False, "mp": True,  "has": True},  "pos"),
    ("mp_extended",    f_mp_extended,    {"range": False, "mp": False, "has": False}, "neg"),
    ("mp_failed",      f_mp_failed,      {"range": False, "mp": False, "has": False}, "dist"),
    ("mp_vol_under",   f_mp_vol_under,   {"range": False, "mp": False, "has": False}, "neg"),
    ("mp_deep",        f_mp_deep,        {"range": False, "mp": False, "has": False}, "neg"),
]


def main() -> int:
    rows_out, failures = [], []
    cm = {"TP": 0, "FP": 0, "FN": 0, "TN": 0}
    dist_fp = 0

    for name, build, exp, cls in CORPUS:
        ev = events.detect_events(build())
        got = {
            "range": ev["range"] is not None,
            "spring": ev["spring"] is not None,
            "sos": ev["sos"] is not None,
            "lps": ev["lps"] is not None,
            "score": events.event_summary(ev)[0],
            "has": events.has_entry_event(ev),
            "mp": ev.get("markup_pullback") is not None,
        }
        ok = all(got[k] == exp[k] for k in exp)
        if not ok:
            diffs = {k: (exp[k], got[k]) for k in exp if got[k] != exp[k]}
            failures.append((name, diffs))

        lt, lp = exp["has"], got["has"]   # confusion matrix on has_entry_event
        cm["TP" if (lt and lp) else "FN" if lt else "FP" if lp else "TN"] += 1
        if cls == "dist" and lp:
            dist_fp += 1
        rows_out.append((name, cls, "ok" if ok else "FAIL"))

    print("Tier 1 boundary-pair corpus:")
    for name, cls, status in rows_out:
        print(f"  [{status:4}] {name:16} ({cls})")

    print("\nhas_entry_event confusion matrix:")
    print(f"  TP={cm['TP']}  FP={cm['FP']}  FN={cm['FN']}  TN={cm['TN']}")
    tp, fp, fn = cm["TP"], cm["FP"], cm["FN"]
    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = tp / (tp + fn) if (tp + fn) else 1.0
    print(f"  precision={prec:.2f}  recall={rec:.2f}")
    print(f"  distribution-class false positives: {dist_fp} (pass bar: 0)")

    passed = not failures and dist_fp == 0
    if failures:
        print("\nMislabeled fixtures:")
        for name, diffs in failures:
            print(f"  {name}: {diffs}   (expected, got)")
    print("\nRESULT:", "ALL PASS ✅" if passed else "FAILURES — recalibrate ❌")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
