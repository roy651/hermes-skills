from __future__ import annotations
from datetime import datetime, timezone
from typing import NamedTuple
import requests
import pandas as pd

_HEADERS = {"User-Agent": "Mozilla/5.0"}
_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


class TickerData(NamedTuple):
    df: pd.DataFrame
    name: str      # e.g. "SPDR S&P 500 ETF Trust"
    currency: str  # e.g. "USD" or "ILS"


def fetch_ohlcv(ticker: str, days: int = 120, start: str | None = None, end: str | None = None) -> TickerData:
    # Explicit ISO date range (start/end) → use period1/period2 for historical windows (fixtures);
    # otherwise the trailing range. Date parsing is only reached when start/end are passed.
    if start and end:
        p1 = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
        p2 = int(datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp())
        params = {"interval": "1d", "period1": p1, "period2": p2}
    else:
        params = {"interval": "1d", "range": "1y" if days <= 252 else "2y"}
    resp = requests.get(
        _BASE.format(ticker=ticker),
        params=params,
        headers=_HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"]
    if not result:
        raise ValueError(f"No data returned for {ticker}")

    r = result[0]
    meta = r["meta"]
    name = meta.get("shortName") or meta.get("longName") or ticker
    currency = meta.get("currency", "USD")

    timestamps = r["timestamp"]
    q = r["indicators"]["quote"][0]
    adj = r["indicators"].get("adjclose", [{}])[0].get("adjclose", q["close"])

    # Yahoo Finance returns TASE prices in agorot (ILA = 1/100 ILS); normalize to ILS
    scale = 0.01 if currency == "ILA" else 1.0
    display_currency = "ILS" if currency == "ILA" else currency

    df = pd.DataFrame({
        "Date": [datetime.fromtimestamp(ts, tz=timezone.utc).date() for ts in timestamps],
        "open": [v * scale if v is not None else None for v in q["open"]],
        "high": [v * scale if v is not None else None for v in q["high"]],
        "low": [v * scale if v is not None else None for v in q["low"]],
        "close": [v * scale if v is not None else None for v in adj],
        "volume": q["volume"],
    }).set_index("Date").dropna()

    df = (df if (start and end) else df.tail(days)).round(4)
    if df.empty:
        raise ValueError(f"No data returned for {ticker}")
    return TickerData(df=df, name=name, currency=display_currency)
