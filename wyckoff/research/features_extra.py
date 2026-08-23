#!/usr/bin/env python3
"""Features the detector bank doesn't compute: liquidity, risk, and market context.

GKX found the dominant predictor families to be price trends, LIQUIDITY and VOLATILITY. The
detector bank covers trends well and volatility partially; liquidity is absent entirely, which
made it the highest value-per-effort gap. Sector is here for the same reason GKX carried 74
industry dummies — momentum is substantially a sector bet, and a model that cannot see sector
cannot tell "this stock is strong" from "everything it owns is strong".

Everything here is computed from the panel we already hold, except sector, which is one
Wikipedia scrape cached to disk.
"""
from __future__ import annotations

import json
import pickle
import sys
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests

CACHE = Path(__file__).parent / "cache"
SECTOR_JSON = CACHE / "sectors.json"
HEADERS = {"User-Agent": "Mozilla/5.0"}

WIKI_SECTOR = [
    ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "Symbol", "GICS Sector"),
    ("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", "Symbol", "GICS Sector"),
    ("https://en.wikipedia.org/wiki/List_of_S%26P_600_companies", "Symbol", "GICS Sector"),
]


def sectors() -> dict[str, str]:
    if SECTOR_JSON.exists():
        return json.loads(SECTOR_JSON.read_text())

    out: dict[str, str] = {}
    for url, sym_col, sec_col in WIKI_SECTOR:
        try:
            html = requests.get(url, headers=HEADERS, timeout=30).text
            for tbl in pd.read_html(StringIO(html)):
                if sym_col in tbl.columns and sec_col in tbl.columns:
                    for s, sec in zip(tbl[sym_col], tbl[sec_col]):
                        out[str(s).replace(".", "-").strip()] = str(sec).strip()
                    break
        except Exception as e:
            print(f"[sectors] {url}: {str(e)[:70]}", file=sys.stderr)

    SECTOR_JSON.write_text(json.dumps(out, indent=0, sort_keys=True))
    print(f"[sectors] {len(out)} tickers mapped", file=sys.stderr)
    return out


def market_context(panel: dict) -> pd.DataFrame:
    """Regime features from SPY alone — the same value for every stock on a given date.

    Momentum's known failure mode is the crash out of a bear market, so what the market itself
    is doing is exactly the context a meta-model needs to see.
    """
    spy = panel["SPY"]
    c = spy["close"]
    ret = c.pct_change()
    return pd.DataFrame({
        "spy_ret": ret,
        "spy_dd": c / c.cummax() - 1,
        "spy_vol20": ret.rolling(20).std() * np.sqrt(252),
        "spy_vol60": ret.rolling(60).std() * np.sqrt(252),
        "spy_above_200": (c > c.rolling(200).mean()).astype(float),
        "spy_ret63": c / c.shift(63) - 1,
    }, index=spy.index)


def per_ticker(df: pd.DataFrame, spy_ret: pd.Series) -> pd.DataFrame:
    """Liquidity and risk, aligned to this ticker's own index."""
    close, vol = df["close"], df["volume"]
    ret = close.pct_change()

    dollar_vol = (close * vol).rolling(60).mean()
    # Amihud: price impact per dollar traded. High = illiquid. Logged — it spans orders
    # of magnitude and the raw scale would dominate any tree split on it.
    illiq = (ret.abs() / (close * vol).replace(0, np.nan)).rolling(60).mean() * 1e9

    joint = pd.concat([ret.rename("r"), spy_ret.rename("m")], axis=1).reindex(df.index)
    cov = joint["r"].rolling(126).cov(joint["m"])
    var = joint["m"].rolling(126).var()
    beta = cov / var
    idio = (joint["r"] - beta * joint["m"]).rolling(126).std() * np.sqrt(252)

    return pd.DataFrame({
        "log_dollar_vol": np.log10(dollar_vol.clip(lower=1)),
        "log_illiq": np.log10(illiq.clip(lower=1e-6)),
        "beta": beta,
        "idio_vol": idio,
        "vol_trend": vol.rolling(20).mean() / vol.rolling(120).mean(),
    }, index=df.index)


if __name__ == "__main__":
    panel = pickle.load(open(CACHE / "panel.pkl", "rb"))
    sec = sectors()
    hit = sum(1 for t in panel if t in sec)
    print(f"sector coverage: {hit}/{len(panel)} panel tickers")
    mc = market_context(panel)
    print(mc.tail(3).to_string())
    print(per_ticker(panel["AAPL"], mc["spy_ret"]).tail(3).to_string())
