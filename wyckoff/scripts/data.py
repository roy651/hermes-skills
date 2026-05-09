from __future__ import annotations
import yfinance as yf
import pandas as pd


def fetch_ohlcv(ticker: str, days: int = 120) -> pd.DataFrame:
    t = yf.Ticker(ticker)
    df = t.history(period=f"{days}d", interval="1d", auto_adjust=True)
    if df.empty:
        raise ValueError(f"No data returned for {ticker}")
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.index = df.index.tz_localize(None)
    df.index.name = "Date"
    df.columns = ["open", "high", "low", "close", "volume"]
    return df.round(4)


def current_price(ticker: str) -> float:
    df = fetch_ohlcv(ticker, days=5)
    return float(df["close"].iloc[-1])
