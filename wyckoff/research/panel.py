#!/usr/bin/env python3
"""Build a clean S&P 1500 panel — replaces the insider-buying universe.

Yesterday's studies ran on "tickers that had insider buying", which skews small, cheap and
troubled (median observation sat 20.7% below its high). That was the largest caveat on every
result. The S&P 1500 is liquid, survivorship-documented and representative, so conclusions
drawn on it transfer to what we would actually trade.
"""
import pickle
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

# Cache lives beside this file and is gitignored — it holds ~1GB of prices.
CACHE = Path(__file__).parent / "cache"
OUT = CACHE
HEADERS = {"User-Agent": "Mozilla/5.0"}

WIKI = {
    "sp500": ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", 0, "Symbol"),
    "sp400": ("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", 0, "Symbol"),
    "sp600": ("https://en.wikipedia.org/wiki/List_of_S%26P_600_companies", 0, "Symbol"),
    "nasdaq100": ("https://en.wikipedia.org/wiki/Nasdaq-100", 0, "Symbol"),
}

# Non-US constituents. Yahoo needs an exchange suffix that the Wikipedia tables omit.
# Geographic diversity matters here: it supplies samples that are not all drawn from the
# same US momentum regime, which is the main threat to everything measured so far.
WIKI_INTL = {
    "ftse100":  ("https://en.wikipedia.org/wiki/FTSE_100_Index", ".L"),
    "dax":      ("https://en.wikipedia.org/wiki/DAX", ".DE"),
    "cac40":    ("https://en.wikipedia.org/wiki/CAC_40", ".PA"),
    "tsx60":    ("https://en.wikipedia.org/wiki/S%26P/TSX_60", ".TO"),
    "aex":      ("https://en.wikipedia.org/wiki/AEX_index", ".AS"),
    "ibex":     ("https://en.wikipedia.org/wiki/IBEX_35", ".MC"),
    "omx":      ("https://en.wikipedia.org/wiki/OMX_Stockholm_30", ".ST"),
    "smi":      ("https://en.wikipedia.org/wiki/Swiss_Market_Index", ".SW"),
    "ftsemib":  ("https://en.wikipedia.org/wiki/FTSE_MIB", ".MI"),
    "asx200":   ("https://en.wikipedia.org/wiki/S%26P/ASX_200", ".AX"),
}

# Tel Aviv: Yahoo carries TA-125 names but Wikipedia has no reliable constituent table,
# so the liquid core is listed explicitly. Roy is ILS-based; these carry no FX risk for him.
TASE = ["TEVA.TA", "NICE.TA", "ELAL.TA", "POLI.TA", "LUMI.TA", "DSCT.TA", "MZTF.TA",
        "FIBI.TA", "ICL.TA", "ESLT.TA", "NVMI.TA", "CAMT.TA", "ORA.TA", "PHOE.TA",
        "MGDL.TA", "CLIS.TA", "HARL.TA", "AZRG.TA", "MLSR.TA", "BIG.TA", "SAE.TA",
        "SPEN.TA", "ALHE.TA", "ENLT.TA", "NOFR.TA", "SLARL.TA", "AMOT.TA", "ARPT.TA",
        "GZT.TA", "MVNE.TA", "ONE.TA", "TASE.TA", "BEZQ.TA", "PTNR.TA", "CEL.TA",
        "SHOM.TA", "RMLI.TA", "VCTR.TA", "TSEM.TA", "ILCO.TA", "DELT.TA", "MTRX.TA"]

BENCHMARKS = ["SPY", "IWM", "RSP", "MDY", "QQQ", "IJR", "EFA", "EEM", "^TA125.TA"]


def universe() -> list[str]:
    from io import StringIO
    tickers = set()
    for name, (url, _table_idx, _col) in WIKI.items():
        try:
            html = requests.get(url, headers=HEADERS, timeout=30).text
            tables = pd.read_html(StringIO(html))
            # Column is "Symbol" on some pages and "Ticker symbol" on others; take the
            # first table that has either and looks like a constituent list.
            for tbl in tables:
                col = next((c for c in tbl.columns
                            if str(c).strip().lower() in ("symbol", "ticker symbol", "ticker")), None)
                if col is not None and len(tbl) > 50:
                    got = [str(s).strip().upper() for s in tbl[col].tolist()]
                    tickers.update(t for t in got if t and t != "NAN")
                    print(f"[universe] {name}: +{len(got)}", file=sys.stderr)
                    break
            else:
                print(f"[universe] {name}: no constituent table found", file=sys.stderr)
        except Exception as e:
            print(f"[universe] {name} FAILED: {str(e)[:120]}", file=sys.stderr)
    clean = {t.replace(".", "-") for t in tickers if len(t) <= 6 and t.isascii()}

    for name, (url, suffix) in WIKI_INTL.items():
        try:
            html = requests.get(url, headers=HEADERS, timeout=30).text
            tables = pd.read_html(StringIO(html))
            for tbl in tables:
                col = next((c for c in tbl.columns
                            if str(c).strip().lower() in ("ticker", "symbol", "epic",
                                                          "ticker symbol", "code")), None)
                if col is not None and len(tbl) >= 20:
                    got = [str(s).strip().upper() for s in tbl[col].tolist()]
                    got = [g.split(":")[-1] for g in got if g and g != "NAN"]
                    clean.update(f"{g}{suffix}" for g in got if len(g) <= 6 and g.isascii())
                    print(f"[universe] {name}: +{len(got)}{suffix}", file=sys.stderr)
                    break
            else:
                print(f"[universe] {name}: no table", file=sys.stderr)
        except Exception as e:
            print(f"[universe] {name} FAILED: {str(e)[:80]}", file=sys.stderr)

    clean.update(TASE)
    return sorted(clean)


def fetch(ticker: str):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    for attempt in range(4):
        try:
            r = requests.get(url, params={"interval": "1d", "range": "10y"},
                             headers=HEADERS, timeout=30)
            if r.status_code == 429 or "Too Many Requests" in r.text[:300]:
                raise requests.HTTPError("rl")
            res = r.json().get("chart", {}).get("result")
            if not res:
                return None
            q = res[0]["indicators"]["quote"][0]
            adj = res[0]["indicators"].get("adjclose", [{}])[0].get("adjclose") or q["close"]
            # Adjust the WHOLE bar by the same factor. Yahoo only dividend-adjusts adjclose,
            # so mixing it with raw high/low puts two price scales in one bar and corrupts
            # every intrabar comparison — badly, and only for dividend payers.
            fac = [(a / c if (a is not None and c) else 1.0) for a, c in zip(adj, q["close"])]
            scaled = lambda arr: [v * f if v is not None else None for v, f in zip(arr, fac)]
            df = pd.DataFrame({
                "open": scaled(q["open"]), "high": scaled(q["high"]),
                "low": scaled(q["low"]), "close": adj, "volume": q["volume"],
            }, index=pd.to_datetime(res[0]["timestamp"], unit="s").normalize()).dropna()
            df = df[~df.index.duplicated(keep="last")]
            return df if len(df) > 300 else None
        except Exception:
            if attempt < 3:
                time.sleep(1.5 * (attempt + 1))
    return None


def main():
    OUT.mkdir(exist_ok=True)
    tickers = universe() + BENCHMARKS
    print(f"[universe] total {len(tickers)} symbols", file=sys.stderr)

    path = OUT / "panel.pkl"
    cache = pickle.load(open(path, "rb")) if path.exists() else {}
    todo = [t for t in tickers if t not in cache]
    print(f"[fetch] cached={len(cache)} todo={len(todo)}", file=sys.stderr)

    done = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for t, df in zip(todo, pool.map(fetch, todo)):
            cache[t] = df
            done += 1
            if done % 200 == 0:
                pickle.dump(cache, open(path, "wb"))
                print(f"[fetch] {done}/{len(todo)}", file=sys.stderr)
    pickle.dump(cache, open(path, "wb"))

    ok = sum(1 for v in cache.values() if v is not None)
    print(f"[fetch] done: {ok}/{len(cache)} with data", file=sys.stderr)


if __name__ == "__main__":
    main()
