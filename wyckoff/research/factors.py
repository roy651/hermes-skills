#!/usr/bin/env python3
"""Classic fundamental factors, computed point-in-time and tested like everything else here.

Why this before any LLM reading of filings: these are the factors a competent analyst actually
reasons about, they are computable deterministically from data we already hold, and — unlike
anything LLM-generated on history — they are **contamination-free and properly backtestable**.
If none of them works on our panel, an LLM applying the same reasoning is unlikely to do better,
and we will have learned that cheaply.

Each has real literature behind it:
  gross_profitability   Novy-Marx (2013) — gross profit / assets, "the other side of value"
  op_profitability      Fama-French RMW
  accruals              Sloan (1996) — earnings not backed by cash reverse
  asset_growth          Cooper, Gulen & Schill (2008) — firms that expand assets underperform
  investment            Fama-French CMA
  cash_conversion       OCF / net income — is profit turning into money?
  margin_trend          direction of operating margin, not its level
  leverage, roe, size-free ratios throughout

Every input is read as of the FILING DATE, so nothing uses a figure the market had not seen.

Usage:  factors.py [--horizon 126]
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import blind as B          # reuse its tested point-in-time extractor
import fundamentals as F

CACHE = Path(__file__).parent / "cache"
HORIZONS = [21, 63, 126]


FAR = pd.Timestamp("2100-01-01")


def extract_all(facts: dict) -> dict:
    """Parse a company's facts ONCE into full series; events then just slice by date.

    Calling the point-in-time extractor per event would re-scan the same JSON ~50 times per
    company — about 870,000 scans across the panel. Parsing once and filtering on `filed` gives
    an identical result for a fraction of the work.
    """
    C = B.CONCEPTS
    flow = ("revenue", "gross_profit", "operating_income", "net_income", "ocf", "capex")
    out = {}
    for k, tags in C.items():
        if k == "cost_of_revenue":
            continue
        out[k] = B._series(facts, tags, FAR, quarterly=(k in flow))
    return out


def factors_at(series: dict, as_of: pd.Timestamp) -> dict | None:
    """One row of factors as knowable on `as_of`. TTM flows, latest stocks."""

    def _q(key):
        s = series.get(key)
        return s[s.filed <= as_of] if s is not None and len(s) else s

    _p = _q

    def ttm(key):
        s = _q(key)
        return s.val.tail(4).sum() if s is not None and len(s) >= 4 else np.nan

    def ttm_prev(key):
        s = _q(key)
        return s.val.tail(8).head(4).sum() if s is not None and len(s) >= 8 else np.nan

    def latest(key):
        s = _p(key)
        return float(s.val.iloc[-1]) if s is not None and len(s) else np.nan

    def prev_year(key):
        s = _p(key)
        if s is None or len(s) < 5:
            return np.nan
        cutoff = s.end.iloc[-1] - pd.Timedelta(days=365)
        older = s[s.end <= cutoff]
        return float(older.val.iloc[-1]) if len(older) else np.nan

    rev, gp = ttm("revenue"), ttm("gross_profit")
    oi, ni, ocf = ttm("operating_income"), ttm("net_income"), ttm("ocf")
    capex = ttm("capex")
    at, eq = latest("assets"), latest("equity")
    at_prev = prev_year("assets")
    debt, cash = latest("debt"), latest("cash")
    rev_prev, oi_prev = ttm_prev("revenue"), ttm_prev("operating_income")

    if not (np.isfinite(rev) and np.isfinite(at) and at > 0 and rev > 0):
        return None
    with np.errstate(all="ignore"):
        f = {
            "gross_profitability": gp / at,
            "op_profitability": oi / eq if eq and eq > 0 else np.nan,
            "roe": ni / eq if eq and eq > 0 else np.nan,
            "accruals": (ni - ocf) / at,
            "asset_growth": at / at_prev - 1 if at_prev and at_prev > 0 else np.nan,
            "investment": capex / at,
            "cash_conversion": ocf / ni if ni and ni > 0 else np.nan,
            "op_margin": oi / rev,
            "margin_trend": (oi / rev) - (oi_prev / rev_prev)
                            if rev_prev and rev_prev > 0 else np.nan,
            "rev_growth": rev / rev_prev - 1 if rev_prev and rev_prev > 0 else np.nan,
            "leverage": debt / eq if eq and eq > 0 else np.nan,
            "cash_ratio": cash / at,
            "asset_turnover": rev / at,
        }
    return {k: (v if np.isfinite(v) else np.nan) for k, v in f.items()}


def build() -> pd.DataFrame:
    panel = pickle.load(open(CACHE / "panel.pkl", "rb"))
    ev = pd.read_pickle(CACHE / "eps_events.pkl")
    us = [t for t, d in panel.items()
          if d is not None and "." not in t and not t.startswith("^") and len(d) > 600]
    close = pd.DataFrame({t: panel[t]["close"] for t in us}).sort_index()
    bench = {h: (close.shift(-h) / close - 1).mean(axis=1) for h in HORIZONS}
    idx = close.index
    cm = F.cik_map()

    rows = []
    for n, (t, g) in enumerate(ev[ev.ticker.isin(us)].groupby("ticker", sort=False)):
        cik = cm.get(t.upper().replace(".", "-"))
        if not cik:
            continue
        facts = F.company_facts(t, cik)
        if not facts:
            continue
        try:
            series = extract_all(facts)
        except Exception:
            continue
        px = close[t].to_numpy()
        for r in g.itertuples():
            i = idx.searchsorted(r.filed, side="left")
            if i >= len(idx) or not np.isfinite(px[i]) or px[i] <= 0:
                continue
            try:
                f = factors_at(series, pd.Timestamp(r.filed))
            except Exception:
                continue
            if not f:
                continue
            rec = {"ticker": t, "filed": idx[i], **f}
            keep = False
            for h in HORIZONS:
                j, b = i + h, bench[h].to_numpy()[i]
                if j < len(idx) and np.isfinite(px[j]) and np.isfinite(b):
                    rec[f"x{h}"] = (px[j] / px[i] - 1 - b) * 100
                    keep = True
                else:
                    rec[f"x{h}"] = np.nan
            if keep:
                rows.append(rec)
        if n % 200 == 0:
            print(f"[factors] {n} tickers · {len(rows):,} rows", file=sys.stderr)
    df = pd.DataFrame(rows)
    df.to_pickle(CACHE / "factor_events.pkl")
    print(f"[factors] {len(df):,} events · {df.ticker.nunique()} tickers", file=sys.stderr)
    return df


FACTORS = ["gross_profitability", "op_profitability", "roe", "accruals", "asset_growth",
           "investment", "cash_conversion", "op_margin", "margin_trend", "rev_growth",
           "leverage", "cash_ratio", "asset_turnover"]


def report(df: pd.DataFrame) -> None:
    pd.set_option("display.width", 200)
    for h in HORIZONS:
        col = f"x{h}"
        sub = df.dropna(subset=[col])
        out = []
        for fac in FACTORS:
            s = sub.dropna(subset=[fac]).copy()
            if len(s) < 3000:
                continue
            # Decile within each month, so the sort is a same-time decision.
            s["m"] = s.filed.dt.to_period("M")
            s["d"] = s.groupby("m")[fac].transform(
                lambda x: pd.qcut(x.rank(method="first"), 10, labels=False, duplicates="drop"))
            hi = s[s.d == 9].groupby("m")[col].mean()
            lo = s[s.d == 0].groupby("m")[col].mean()
            sp = (hi - lo).dropna()
            if len(sp) < 24:
                continue
            t = sp.mean() / (sp.std(ddof=1) / np.sqrt(len(sp)))
            out.append({"factor": fac, "n": len(s), "top-bot%": sp.mean(), "t": t,
                        "months": len(sp), "pos%": (sp > 0).mean() * 100})
        r = pd.DataFrame(out).sort_values("t", key=abs, ascending=False)
        print(f"\n=== HORIZON {h} sessions — top minus bottom decile, monthly Fama-MacBeth ===")
        print(r.to_string(index=False, float_format=lambda v: f"{v:8.2f}"))


if __name__ == "__main__":
    f = CACHE / "factor_events.pkl"
    df = pd.read_pickle(f) if ("--reuse" in sys.argv and f.exists()) else build()
    report(df)
