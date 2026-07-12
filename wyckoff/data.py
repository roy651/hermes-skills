from __future__ import annotations
from datetime import datetime, timezone
import requests
import pandas as pd

_HEADERS = {"User-Agent": "Mozilla/5.0"}
_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


def fetch_ohlcv(ticker: str, days: int = 120) -> pd.DataFrame:
    # Use 1y range to ensure we get at least `days` trading days
    range_param = "1y" if days <= 252 else "2y"
    resp = requests.get(
        _BASE.format(ticker=ticker),
        params={"interval": "1d", "range": range_param},
        headers=_HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"]
    if not result:
        raise ValueError(f"No data returned for {ticker}")

    r = result[0]
    timestamps = r["timestamp"]
    q = r["indicators"]["quote"][0]
    adj = r["indicators"].get("adjclose", [{}])[0].get("adjclose", q["close"])

    df = pd.DataFrame({
        "Date": [datetime.fromtimestamp(ts, tz=timezone.utc).date() for ts in timestamps],
        "open": q["open"],
        "high": q["high"],
        "low": q["low"],
        "close": adj,
        "volume": q["volume"],
    }).set_index("Date").dropna()

    df = df.tail(days).round(4)
    if df.empty:
        raise ValueError(f"No data returned for {ticker}")
    return df


def current_price(ticker: str) -> float:
    df = fetch_ohlcv(ticker, days=5)
    return float(df["close"].iloc[-1])
