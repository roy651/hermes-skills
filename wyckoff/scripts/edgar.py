#!/usr/bin/env python3
"""SEC EDGAR client — insider transactions (Form 4) and institutional holdings (13F).

Why this exists: every other source in this skill reads the *price series*. EDGAR reads
*ownership* — who is buying, with their own money, by name. A Wyckoff accumulation range
says "someone is absorbing supply"; a Form 4 cluster says who. Two independent readings of
one hypothesis.

EDGAR is free and needs no key, but it does require a descriptive User-Agent and caps
callers at 10 requests/second. Both are handled here.

CLI:
    python edgar.py form4 AAPL              # recent insider transactions for one ticker
    python edgar.py harvest 2026-07-01 2026-08-05   # bulk harvest across the universe
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

# SEC requires a real contact address in the User-Agent; anonymous clients get 403'd.
_UA = os.environ.get("SEC_USER_AGENT", "Roy Abitbol roy.abitbol.research@gmail.com")
_HEADERS = {"User-Agent": _UA, "Accept-Encoding": "gzip, deflate"}
_RATE_LIMIT_SEC = 0.11          # SEC allows 10 req/s; stay just under
_CACHE = Path(__file__).parent.parent / "data" / "edgar_cache"

_last_call = 0.0


def _get(url: str, cache_key: str | None = None) -> str:
    """Throttled GET. A cache_key makes the response permanent on disk — filings are
    immutable, so re-running a harvest costs nothing after the first pass."""
    if cache_key:
        cached = _CACHE / cache_key
        if cached.exists():
            return cached.read_text()

    global _last_call
    wait = _RATE_LIMIT_SEC - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()

    resp = requests.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    text = resp.text

    if cache_key:
        cached = _CACHE / cache_key
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(text)
    return text


# --- CIK ↔ ticker ------------------------------------------------------------------

def cik_to_ticker() -> dict[int, str]:
    """SEC's official mapping, ~10k listed companies. Used to scope a harvest to a
    universe *before* fetching filings — the whole feed is ~1,900 Form 4s per day."""
    raw = _get("https://www.sec.gov/files/company_tickers.json", cache_key="company_tickers.json")
    return {int(row["cik_str"]): row["ticker"] for row in json.loads(raw).values()}


# --- Form 4 ------------------------------------------------------------------------

# Transaction codes that mean "bought on the open market with own money".
# Everything else is compensation, tax withholding, or a gift — none of which carry signal.
OPEN_MARKET_BUY = "P"
OPEN_MARKET_SELL = "S"


@dataclass
class InsiderTrade:
    ticker: str
    issuer: str
    issuer_cik: int
    owner: str
    owner_cik: str
    is_director: bool
    is_officer: bool
    is_ten_pct: bool
    officer_title: str
    code: str
    trade_date: str
    shares: float
    price: float
    filed: str
    accession: str

    @property
    def value(self) -> float:
        return self.shares * self.price


def _tag(xml: str, name: str) -> str | None:
    m = re.search(rf"<{name}>\s*(?:<value>)?\s*([^<]*)", xml)
    return m.group(1).strip() if m else None


def parse_form4(raw: str, accession: str = "") -> list[InsiderTrade]:
    """Pull the non-derivative transactions out of one Form 4 submission.

    Only the non-derivative table is read: derivative rows are option grants and exercises,
    which look like purchases but are compensation, not conviction."""
    xml_match = re.search(r"<XML>(.*?)</XML>", raw, re.S)
    xml = xml_match.group(1) if xml_match else raw

    ticker = _tag(xml, "issuerTradingSymbol") or ""
    issuer = _tag(xml, "issuerName") or ""
    issuer_cik = int(_tag(xml, "issuerCik") or 0)
    owner = _tag(xml, "rptOwnerName") or ""
    owner_cik = _tag(xml, "rptOwnerCik") or ""
    filed = _tag(xml, "periodOfReport") or ""

    relationship = re.search(r"<reportingOwnerRelationship>(.*?)</reportingOwnerRelationship>", xml, re.S)
    rel = relationship.group(1) if relationship else ""
    is_director = "<isDirector>1" in rel or "<isDirector>true" in rel
    is_officer = "<isOfficer>1" in rel or "<isOfficer>true" in rel
    is_ten_pct = "<isTenPercentOwner>1" in rel or "<isTenPercentOwner>true" in rel
    officer_title = _tag(rel, "officerTitle") or ""

    # The non-derivative table ends where the derivative table begins.
    body = xml.split("<derivativeTable>")[0]

    trades = []
    for block in re.findall(r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>", body, re.S):
        code = _tag(block, "transactionCode") or ""
        shares = float(_tag(block, "transactionShares") or 0)
        price = float(_tag(block, "transactionPricePerShare") or 0)
        trade_date = _tag(block, "transactionDate") or ""
        trades.append(InsiderTrade(
            ticker=ticker, issuer=issuer, issuer_cik=issuer_cik,
            owner=owner, owner_cik=owner_cik,
            is_director=is_director, is_officer=is_officer, is_ten_pct=is_ten_pct,
            officer_title=officer_title,
            code=code, trade_date=trade_date, shares=shares, price=price,
            filed=filed, accession=accession,
        ))
    return trades


def daily_index(day: date) -> list[tuple[int, str, str]]:
    """(cik, company, path) for every Form 4 filed on `day`. Weekends/holidays return []."""
    qtr = (day.month - 1) // 3 + 1
    url = (f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{qtr}/"
           f"form.{day:%Y%m%d}.idx")
    try:
        raw = _get(url, cache_key=f"idx/{day:%Y%m%d}.idx")
    except requests.HTTPError:
        return []                      # no filings that day

    out = []
    for line in raw.splitlines():
        if not line.startswith("4 "):
            continue
        # Fixed-width-ish: form, company (spaces!), cik, date, path. Split from the right.
        parts = line.rsplit(None, 3)
        if len(parts) != 4:
            continue
        head, cik, _filed, path = parts
        company = head[len("4"):].strip()
        if cik.isdigit():
            out.append((int(cik), company, path))
    return out


def harvest(start: date, end: date, universe: set[str] | None = None,
            progress: bool = True) -> list[InsiderTrade]:
    """Every Form 4 non-derivative transaction filed in [start, end].

    `universe` (tickers) scopes the work *before* fetching: the full feed is ~1,900 filings
    a day, so an unscoped multi-month harvest is hundreds of thousands of requests. Scoping
    to a ~600-name universe cuts that by roughly 90%."""
    ticker_map = cik_to_ticker()
    wanted_ciks = None
    if universe:
        upper = {t.upper() for t in universe}
        wanted_ciks = {cik for cik, tkr in ticker_map.items() if tkr.upper() in upper}

    trades: list[InsiderTrade] = []
    day = start
    while day <= end:
        filings = daily_index(day)
        if wanted_ciks is not None:
            filings = [f for f in filings if f[0] in wanted_ciks]
        for cik, _company, path in filings:
            try:
                raw = _get(f"https://www.sec.gov/Archives/{path}",
                           cache_key=f"f4/{path.rsplit('/', 1)[-1]}")
                trades.extend(parse_form4(raw, accession=path))
            except (requests.HTTPError, ValueError) as e:
                print(f"[edgar] {path}: {e}", file=sys.stderr)
        if progress:
            print(f"[edgar] {day:%Y-%m-%d}  filings={len(filings):4d}  trades={len(trades)}",
                  file=sys.stderr)
        day += timedelta(days=1)
    return trades


# --- Cluster detection -------------------------------------------------------------

@dataclass
class Cluster:
    ticker: str
    issuer: str
    buyers: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    first_date: str = ""
    last_date: str = ""
    total_value: float = 0.0
    total_shares: float = 0.0

    @property
    def n_buyers(self) -> int:
        return len(set(self.buyers))


def find_clusters(trades: list[InsiderTrade], window_days: int = 45,
                  min_buyers: int = 2, min_value: float = 25_000,
                  exclude_ten_pct: bool = True) -> list[Cluster]:
    """Group open-market purchases into per-issuer clusters.

    A cluster = `min_buyers` distinct insiders buying the same issuer within a rolling
    `window_days`. The thresholds are the tunable part of the hypothesis; defaults follow
    the convention in the anomaly literature (2+ insiders, ~1-2 months, non-trivial size).

    10% owners are excluded by default: their buying is often a fund building a stake for
    reasons unrelated to their read of the business, and it swamps officer signal by size."""
    buys = [t for t in trades
            if t.code == OPEN_MARKET_BUY and t.price > 0 and t.shares > 0
            and (t.is_director or t.is_officer or t.is_ten_pct)
            and not (exclude_ten_pct and t.is_ten_pct and not (t.is_officer or t.is_director))]

    by_issuer: dict[str, list[InsiderTrade]] = {}
    for t in buys:
        if t.ticker:
            by_issuer.setdefault(t.ticker, []).append(t)

    clusters = []
    for ticker, group in by_issuer.items():
        group.sort(key=lambda t: t.trade_date)
        for i, anchor in enumerate(group):
            window_end = (datetime.fromisoformat(anchor.trade_date)
                          + timedelta(days=window_days)).date().isoformat()
            window = [t for t in group[i:] if t.trade_date <= window_end]
            distinct = {t.owner_cik or t.owner for t in window}
            value = sum(t.value for t in window)
            if len(distinct) >= min_buyers and value >= min_value:
                clusters.append(Cluster(
                    ticker=ticker, issuer=anchor.issuer,
                    buyers=[t.owner for t in window],
                    titles=[t.officer_title or ("Director" if t.is_director else "") for t in window],
                    first_date=window[0].trade_date, last_date=window[-1].trade_date,
                    total_value=value, total_shares=sum(t.shares for t in window),
                ))
                break        # one cluster per issuer per harvest — the earliest is the signal
    return sorted(clusters, key=lambda c: -c.total_value)


# --- CLI ---------------------------------------------------------------------------

def _company_form4(ticker: str, limit: int = 40) -> list[InsiderTrade]:
    """Recent Form 4s for a single ticker, via the company submissions API."""
    cik = next((c for c, t in cik_to_ticker().items() if t.upper() == ticker.upper()), None)
    if cik is None:
        raise SystemExit(f"unknown ticker {ticker}")
    subs = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
    recent = subs["filings"]["recent"]
    out = []
    for form, acc, doc_date in zip(recent["form"], recent["accessionNumber"], recent["filingDate"]):
        if form != "4" or len(out) >= limit:
            continue
        acc_plain = acc.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_plain}/{acc}.txt"
        try:
            out.extend(parse_form4(_get(url, cache_key=f"f4/{acc}.txt"), accession=acc))
        except requests.HTTPError as e:
            print(f"[edgar] {acc}: {e}", file=sys.stderr)
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)

    if sys.argv[1] == "form4":
        for t in _company_form4(sys.argv[2]):
            flag = "BUY " if t.code == OPEN_MARKET_BUY else ("SELL" if t.code == OPEN_MARKET_SELL else t.code + "   ")
            print(f"{t.trade_date}  {flag}  {t.shares:>12,.0f} @ {t.price:>9,.2f} "
                  f"= {t.value:>14,.0f}  {t.owner[:32]:<32} {t.officer_title[:28]}")

    elif sys.argv[1] == "harvest":
        s = date.fromisoformat(sys.argv[2])
        e = date.fromisoformat(sys.argv[3])
        trades = harvest(s, e)
        for c in find_clusters(trades):
            print(f"{c.ticker:<8} {c.n_buyers} buyers  ${c.total_value:>13,.0f}  "
                  f"{c.first_date}..{c.last_date}  {c.issuer[:40]}")
