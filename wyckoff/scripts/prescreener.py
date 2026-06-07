#!/usr/bin/env python3
"""Weekly Wyckoff prescreener — pure quantitative filters, no LLM.

Fetches S&P 500 + NASDAQ 100 + sector ETFs, scores each on 5 accumulation
criteria, and sends the top ~30 candidates to Telegram for approval.
"""
from __future__ import annotations
import html
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import requests
import yaml

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")

import data as market_data
import notifier
import events

TZ = ZoneInfo("Asia/Jerusalem")

SECTOR_ETFS = [
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLC", "XLY", "XLP", "XLU", "XLB", "XLRE",
    "IWM", "MDY", "IJR", "EFA", "EEM", "GLD", "SLV", "TLT", "HYG", "LQD",
]

CANDIDATES_FILE = Path(__file__).parent.parent / "data" / "watchlist_candidates.json"
FACTOR_TAGS_FILE = Path(__file__).parent.parent / "data" / "factor_tags.yaml"
TOP_N = 30
MIN_SCORE = 3
# The funnel targets BOTH accumulation AND markup-pullback (e.g. a leader basing on an LPS).
# The regime-aware off-high floor already requires the name to have pulled back, so the
# rel-perf CAP only needs to exclude *parabolic* momentum still ripping at the highs — hence
# 30pp, not 15pp. Tightening it back to 15pp drops legitimate strong-RS markup pullbacks (M5).
REL_PERF_CAP = 0.30    # disqualify only if outperforming SPY/sector by >30pp (parabolic momentum)
REL_PERF_FLOOR = 0.30  # disqualify if underperforming SPY by >30pp over 6m (falling knife)
MIN_ADV = 20_000_000   # min 20-day average dollar volume (liquidity floor)

# GICS sector (from the S&P 500 Wikipedia table) → SPDR sector ETF, for sector-relative strength
_GICS_TO_ETF = {
    "Energy": "XLE", "Financials": "XLF", "Information Technology": "XLK",
    "Health Care": "XLV", "Industrials": "XLI", "Communication Services": "XLC",
    "Consumer Discretionary": "XLY", "Consumer Staples": "XLP",
    "Utilities": "XLU", "Materials": "XLB", "Real Estate": "XLRE",
}


_WIKI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def _wiki_tables(url: str) -> list:
    resp = requests.get(url, headers=_WIKI_HEADERS, timeout=30)
    resp.raise_for_status()
    return pd.read_html(io.StringIO(resp.text))


def _get_universe() -> tuple[list[str], dict[str, str]]:
    """Return (tickers, sector_map) where sector_map maps an S&P ticker to its sector ETF."""
    tickers: list[str] = []
    sector_map: dict[str, str] = {}

    try:
        tables = _wiki_tables("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        df0 = tables[0]
        sp500 = df0["Symbol"].tolist()
        tickers.extend(sp500)
        if "GICS Sector" in df0.columns:
            for sym, sec in zip(df0["Symbol"], df0["GICS Sector"]):
                etf = _GICS_TO_ETF.get(str(sec).strip())
                if etf:
                    sector_map[str(sym).replace(".", "-")] = etf
        print(f"[prescreener] S&P 500: {len(sp500)} tickers, {len(sector_map)} sector-mapped", file=sys.stderr)
    except Exception as e:
        print(f"[prescreener] S&P 500 fetch failed: {e}", file=sys.stderr)

    try:
        tables = _wiki_tables("https://en.wikipedia.org/wiki/Nasdaq-100")
        ndx: list[str] = []
        for t in tables:
            if "Ticker" in t.columns and len(t) > 50:
                ndx = t["Ticker"].dropna().tolist()
                break
        if ndx:
            tickers.extend(ndx)
            print(f"[prescreener] NASDAQ 100: {len(ndx)} tickers", file=sys.stderr)
    except Exception as e:
        print(f"[prescreener] NASDAQ 100 fetch failed: {e}", file=sys.stderr)

    tickers.extend(SECTOR_ETFS)

    cleaned = [t.replace(".", "-") for t in tickers]
    return list(dict.fromkeys(cleaned)), sector_map


def _get_sector_context(etfs: set[str]) -> dict[str, float]:
    """6-month return per sector ETF, for sector-relative strength filtering."""
    out: dict[str, float] = {}
    for etf in etfs:
        try:
            td = market_data.fetch_ohlcv(etf, days=252)
            close = td.df["close"]
            base = float(close.iloc[-126]) if len(close) >= 126 else float(close.iloc[0])
            out[etf] = (float(close.iloc[-1]) - base) / base
        except Exception as e:
            print(f"[prescreener] sector ctx {etf} failed: {e}", file=sys.stderr)
    return out


def _load_factor_tags() -> dict[str, list[str]]:
    if not FACTOR_TAGS_FILE.exists():
        return {}
    return yaml.safe_load(FACTOR_TAGS_FILE.read_text()) or {}


def _get_spy_context() -> dict:
    """Fetch SPY and return regime metrics needed for filtering."""
    td = market_data.fetch_ohlcv("SPY", days=252)
    close = td.df["close"]
    price = float(close.iloc[-1])
    hi_52 = float(td.df["high"].tail(252).max())
    spy_pct_off_high = (hi_52 - price) / hi_52

    # Sliding floor: at ATH → 25% off required; at -20%+ → 15% off required
    required_pct_off_high = 0.15 + 0.5 * max(0.0, 0.20 - spy_pct_off_high)

    base_6m = float(close.iloc[-126]) if len(close) >= 126 else float(close.iloc[0])
    base_12m = float(close.iloc[0])
    ret_6m = (price - base_6m) / base_6m
    ret_12m = (price - base_12m) / base_12m

    print(
        f"[prescreener] SPY: {price:.0f}  {spy_pct_off_high*100:.1f}% off 52w high  "
        f"6m={ret_6m*100:+.1f}%  12m={ret_12m*100:+.1f}%  "
        f"required_off_high={required_pct_off_high*100:.0f}%",
        file=sys.stderr,
    )
    return {
        "spy_pct_off_high": spy_pct_off_high,
        "required_pct_off_high": required_pct_off_high,
        "spy_ret_6m": ret_6m,
        "spy_ret_12m": ret_12m,
    }


def _score(
    df: pd.DataFrame,
    required_pct_off_high: float,
) -> tuple[int, dict[str, int]]:
    """Return (total_score, breakdown) — each criterion is 0 or 1."""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    price = float(close.iloc[-1])

    scores: dict[str, int] = {}

    # 1. Price off 52w high within regime-adjusted range
    hi_52 = float(high.tail(252).max())
    pct_off = (hi_52 - price) / hi_52
    scores["off_high"] = 1 if required_pct_off_high <= pct_off <= 0.65 else 0

    # 2. Not in deep markdown: price ≥ 90% of 200-day MA
    if len(close) >= 200:
        ma200 = float(close.tail(200).mean())
        scores["above_ma200"] = 1 if price >= 0.90 * ma200 else 0
    else:
        scores["above_ma200"] = 0

    # 3. ATR contraction vs its own 90-day median
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    atr_pct = atr14 / close
    valid = atr_pct.dropna()
    if len(valid) >= 60:
        scores["atr_contraction"] = 1 if float(valid.iloc[-1]) < float(valid.tail(90).median()) else 0
    else:
        scores["atr_contraction"] = 0

    # 4. Volume contraction: 20-day avg < 50-day avg
    if len(volume) >= 50:
        scores["vol_contraction"] = 1 if float(volume.tail(20).mean()) < float(volume.tail(50).mean()) else 0
    else:
        scores["vol_contraction"] = 0

    # 5. Bollinger Band squeeze: current BB width < 60th percentile of last 90 days
    sma20 = close.rolling(20).mean()
    bb_width = (2 * close.rolling(20).std()) / sma20
    bb_valid = bb_width.dropna()
    if len(bb_valid) >= 60:
        scores["bb_squeeze"] = 1 if float(bb_valid.iloc[-1]) < float(bb_valid.tail(90).quantile(0.60)) else 0
    else:
        scores["bb_squeeze"] = 0

    return sum(scores.values()), scores


def _fetch_and_score(
    ticker: str,
    required_pct_off_high: float,
    spy_ret_6m: float,
    spy_ret_12m: float,
    sector_etf: str | None = None,
    sector_ret_6m: float | None = None,
) -> dict | None:
    try:
        td = market_data.fetch_ohlcv(ticker, days=252)
        close = td.df["close"]
        volume = td.df["volume"]
        price = float(close.iloc[-1])

        # Liquidity floor — 20-day average dollar volume (always applies)
        adv = float((close.tail(20) * volume.tail(20)).mean())
        if adv < MIN_ADV:
            return None

        base_6m = float(close.iloc[-126]) if len(close) >= 126 else float(close.iloc[0])
        base_12m = float(close.iloc[0])
        ret_6m = (price - base_6m) / base_6m
        ret_12m = (price - base_12m) / base_12m
        rel_6m = ret_6m - spy_ret_6m
        rel_12m = ret_12m - spy_ret_12m

        # Markup-pullback lane (Option 2): a confirmed-breakout pullback is legitimately near its
        # highs and outperforming, so it bypasses the off-high floor and rel-perf cap. Still drop
        # genuine falling knives. Accumulation lane keeps the full rel-perf/sector disqualifiers.
        mp = events.detect_markup_pullback(td.df)
        if mp is None:
            if rel_6m > REL_PERF_CAP or rel_12m > REL_PERF_CAP:
                return None                  # outperforming SPY → markup, not accumulation
            if rel_6m < -REL_PERF_FLOOR:
                return None                  # >30pp underperformance → falling knife
            if sector_ret_6m is not None and (ret_6m - sector_ret_6m) > REL_PERF_CAP:
                return None                  # leading a (possibly weak) sector → markup vs peers
        elif rel_6m < -REL_PERF_FLOOR:
            return None                      # markup lane, but still avoid a collapsing name

        total, breakdown = _score(td.df, required_pct_off_high)
        hi_52 = float(td.df["high"].tail(252).max())
        pct_off = (hi_52 - price) / hi_52 * 100
        return {
            "ticker": ticker,
            "name": td.name,
            "price": round(price, 2),
            "pct_off_52w_high": round(pct_off, 1),
            "rel_6m": round(rel_6m * 100, 1),
            "rel_12m": round(rel_12m * 100, 1),
            "rel_sector_6m": round((ret_6m - sector_ret_6m) * 100, 1) if sector_ret_6m is not None else None,
            "sector": sector_etf,
            "adv_musd": round(adv / 1e6, 1),
            "score": total,
            "breakdown": breakdown,
            "lane": "markup_pullback" if mp else "accumulation",
        }
    except Exception as e:
        print(f"[prescreener] skip {ticker}: {e}", file=sys.stderr)
        return None


def _factor_warnings(candidates: list[dict], tags: dict[str, list[str]]) -> list[str]:
    """Return warning lines if ≥3 candidates share a factor tag."""
    from collections import defaultdict
    tag_to_tickers: dict[str, list[str]] = defaultdict(list)
    for c in candidates:
        for tag in tags.get(c["ticker"], []):
            tag_to_tickers[tag].append(c["ticker"])
    warnings = []
    for tag, tickers in tag_to_tickers.items():
        if len(tickers) >= 3:
            warnings.append(f"⚠️ Factor concentration: <b>{tag}</b> — {', '.join(tickers)}")
    return warnings


def screen_universe() -> tuple[list[dict], dict]:
    """Scan the full universe and return (top candidates, spy_ctx). Saves to CANDIDATES_FILE."""
    spy_ctx = _get_spy_context()
    universe, sector_map = _get_universe()
    sector_ctx = _get_sector_context(set(sector_map.values()))
    print(f"[prescreener] scanning {len(universe)} tickers…", file=sys.stderr)

    def fetch_one(ticker: str) -> dict | None:
        se = sector_map.get(ticker)
        return _fetch_and_score(
            ticker,
            spy_ctx["required_pct_off_high"],
            spy_ctx["spy_ret_6m"],
            spy_ctx["spy_ret_12m"],
            sector_etf=se,
            sector_ret_6m=sector_ctx.get(se) if se else None,
        )

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(fetch_one, t): t for t in universe}
        for i, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            if r:
                results.append(r)
            if i % 100 == 0:
                print(f"[prescreener] {i}/{len(universe)} fetched", file=sys.stderr)

    results.sort(key=lambda x: (-x["score"], x["pct_off_52w_high"]))
    # Markup-pullback candidates bypass the MIN_SCORE accumulation-shape gate and get priority
    # (they are confirmed-breakout setups); accumulation candidates fill the remaining slots.
    mp_cands = [r for r in results if r.get("lane") == "markup_pullback"]
    acc_cands = [r for r in results if r.get("lane") != "markup_pullback" and r["score"] >= MIN_SCORE]
    top = (mp_cands + acc_cands)[:TOP_N]
    if mp_cands:
        print(f"[prescreener] {len(mp_cands)} markup-pullback candidate(s): "
              f"{', '.join(r['ticker'] for r in mp_cands[:10])}", file=sys.stderr)

    CANDIDATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES_FILE.write_text(json.dumps({
        "generated": datetime.now(tz=TZ).isoformat(),
        "spy_context": spy_ctx,
        "total_scanned": len(results),
        "candidates": top,
    }, indent=2))

    print(f"[prescreener] {len(top)} candidates from {len(results)} scanned", file=sys.stderr)
    return top, spy_ctx


_FLAG_LABELS = {
    "off_high": "range",
    "above_ma200": "MA200✓",
    "atr_contraction": "ATR↓",
    "vol_contraction": "vol↓",
    "bb_squeeze": "squeeze",
}


def format_header(spy_ctx: dict, n: int, date_str: str) -> list[str]:
    """Candidate-message header lines. Shared by prescreener.run() and weekly.py."""
    spy_off = spy_ctx["spy_pct_off_high"] * 100
    req_off = spy_ctx["required_pct_off_high"] * 100
    return [
        f"📋 <b>Wyckoff Watchlist Candidates — {date_str}</b>",
        f"<i>SPY {spy_off:.1f}% off 52w high → min {req_off:.0f}% off required</i>",
        f"<i>{n} candidates (≥{MIN_SCORE}/5 criteria, rel perf filtered)</i>",
        "",
    ]


def format_candidate_line(r: dict) -> str:
    """One candidate row. Shared by prescreener.run() and weekly.py."""
    flags = [label for key, label in _FLAG_LABELS.items() if r["breakdown"].get(key)]
    name_part = f" ({html.escape(str(r['name']))})" if r["name"] != r["ticker"] else ""
    rel = f"6m={r['rel_6m']:+.0f}pp 12m={r['rel_12m']:+.0f}pp vs SPY"
    adv = r.get("adv_musd")
    liq = " ⚠️low-liq" if adv is not None and adv < 50 else ""
    return (
        f"<b>{r['ticker']}</b>{name_part} · ${r['price']} "
        f"· {r['pct_off_52w_high']:.0f}% off hi · {r['score']}/5 [{', '.join(flags)}] · <i>{rel}{liq}</i>"
    )


def build_candidates_message(
    top: list[dict], spy_ctx: dict, factor_tags: dict, date_str: str
) -> str:
    """Full candidate Telegram message. Single source of truth for both schedulers."""
    lines = format_header(spy_ctx, len(top), date_str)
    for r in top:
        lines.append(format_candidate_line(r))
    warnings = _factor_warnings(top, factor_tags)
    if warnings:
        lines.append("")
        lines.extend(warnings)
    lines.append("")
    lines.append("<i>Add approved tickers via: manage.py watchlist-add TICKER</i>")
    return "\n".join(lines)


def run():
    factor_tags = _load_factor_tags()
    top, spy_ctx = screen_universe()
    date_str = datetime.now(tz=TZ).strftime("%Y-%m-%d")
    notifier.send(build_candidates_message(top, spy_ctx, factor_tags, date_str))
    print(f"[prescreener] sent {len(top)} candidates to Telegram", file=sys.stderr)


if __name__ == "__main__":
    run()
