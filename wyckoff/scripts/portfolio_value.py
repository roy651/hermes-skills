#!/usr/bin/env python3
"""Daily portfolio valuation — total value, P&L from start, day/week/month."""
from __future__ import annotations
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")

import data as market_data
import holdings as portfolio
import israeli_fund
import fx_rate
import notifier

TZ = ZoneInfo("Asia/Jerusalem")
# Fallback now lives in fx_rate, which persists the last good rate.


def _period_price(df, ref_date: date, period: str) -> float | None:
    """Return the closing price at the start of the given period ('day', 'week', 'month')."""
    if period == "day":
        if len(df) < 2:
            return None
        return float(df["close"].iloc[-2])
    elif period == "week":
        monday = ref_date - timedelta(days=ref_date.weekday())
        subset = df[df.index >= monday]
        if subset.empty:
            return None
        return float(subset["close"].iloc[0])
    elif period == "month":
        month_start = ref_date.replace(day=1)
        subset = df[df.index >= month_start]
        if subset.empty:
            return None
        return float(subset["close"].iloc[0])
    return None


def run(as_section: bool = False):
    holdings_map = portfolio.load()
    if not holdings_map:
        fail = "📊 <b>Portfolio Value</b>\n\n<i>No holdings found.</i>"
        if as_section:
            return fail
        notifier.send(fail)
        return

    today = datetime.now(tz=TZ).date()
    date_str = datetime.now(tz=TZ).strftime("%Y-%m-%d")

    # Fetch all tickers + USD/ILS rate in parallel
    tickers = list(holdings_map.keys())
    # Israeli mutual funds (kaspit / money-market) are not on Yahoo at all. A holding that declares a
    # fund_id / globes_id is priced from its published NAV instead — otherwise a cash-like sleeve
    # silently vanishes from the total and every concentration figure is overstated.
    fund_tickers = {t for t, h in holdings_map.items() if h.get("fund_id") or h.get("globes_id")}
    all_fetch = [t for t in tickers if t not in fund_tickers] + ["USDILS=X"]
    raw: dict[str, market_data.TickerData] = {}

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(market_data.fetch_ohlcv, t, 120): t for t in all_fetch}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                raw[t] = fut.result()
            except Exception as e:
                print(f"[portfolio_value] skip {t}: {e}", file=sys.stderr)

    for t in fund_tickers:
        h = holdings_map[t]
        try:
            raw[t] = israeli_fund.as_ticker_data(h.get("fund_id"), h.get("globes_id"), name=t)
        except Exception as e:
            print(f"[portfolio_value] fund NAV lookup failed for {t}: {e}", file=sys.stderr)

    usdils_rate: float
    if "USDILS=X" in raw:
        usdils_rate = float(raw["USDILS=X"].df["close"].iloc[-1])
    else:
        usdils_rate = fx_rate.latest()

    rows = []
    total_value_usd = 0.0
    total_cost_usd = 0.0

    for ticker, h in holdings_map.items():
        qty = h["qty"]
        avg_cost = h["avg_cost"]
        open_date = None
        if h.get("open_date"):
            try:
                open_date = date.fromisoformat(h["open_date"])
            except (ValueError, TypeError):
                print(f"[portfolio_value] bad open_date for {ticker}: {h.get('open_date')!r}", file=sys.stderr)
        td = raw.get(ticker)
        if td is None:
            print(f"[portfolio_value] no data for {ticker}, skipping", file=sys.stderr)
            continue

        df = td.df
        curr_price = float(df["close"].iloc[-1])
        to_usd = (1.0 / usdils_rate) if td.currency == "ILS" else 1.0

        # avg_cost for ILS stocks is stored in agorot (ILA); data.py already normalises
        # market prices to ILS (÷100), so align avg_cost to the same unit.
        avg_cost_local = avg_cost / 100 if td.currency == "ILS" else avg_cost

        curr_value_usd = qty * curr_price * to_usd
        cost_basis_usd = qty * avg_cost_local * to_usd
        pnl_start_usd = curr_value_usd - cost_basis_usd
        pnl_start_pct = pnl_start_usd / cost_basis_usd if cost_basis_usd else 0.0

        def _pnl(period: str) -> float:
            # If the position was opened within this period, measure P/L from the
            # actual buy price (avg_cost) — you didn't own it at the period start,
            # so the market price then isn't your baseline. This corrects the
            # inflated day/week/month move on same-day (or same-week/month) buys.
            ref = None
            if open_date is not None:
                if period == "day":
                    prior_date = df.index[-2] if len(df) >= 2 else None
                    if prior_date is not None and open_date > prior_date:
                        ref = avg_cost_local
                elif period == "week":
                    monday = today - timedelta(days=today.weekday())
                    if open_date >= monday:
                        ref = avg_cost_local
                elif period == "month":
                    if open_date >= today.replace(day=1):
                        ref = avg_cost_local
            if ref is None:
                ref = _period_price(df, today, period)
            return qty * (curr_price - ref) * to_usd if ref is not None else 0.0

        rows.append({
            "ticker": ticker,
            "name": td.name,
            "qty": qty,
            "price": curr_price,
            "currency": td.currency,
            "value_usd": curr_value_usd,
            "pnl_start_usd": pnl_start_usd,
            "pnl_start_pct": pnl_start_pct,
            "pnl_day_usd": _pnl("day"),
            "pnl_week_usd": _pnl("week"),
            "pnl_month_usd": _pnl("month"),
        })
        total_value_usd += curr_value_usd
        total_cost_usd += cost_basis_usd

    if not rows:
        fail = "📊 <b>Portfolio Value</b>\n\n<i>Could not fetch data for any holding.</i>"
        if as_section:
            return fail
        notifier.send(fail)
        return

    rows.sort(key=lambda r: r["pnl_start_usd"], reverse=True)

    total_pnl_start = total_value_usd - total_cost_usd
    total_pnl_pct = total_pnl_start / total_cost_usd if total_cost_usd else 0.0
    total_pnl_day = sum(r["pnl_day_usd"] for r in rows)
    total_pnl_week = sum(r["pnl_week_usd"] for r in rows)
    total_pnl_month = sum(r["pnl_month_usd"] for r in rows)

    def _fmt(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return f"{sign}${v:,.0f}"

    def _pct(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return f"({sign}{v * 100:.1f}%)"

    lines = [
        f"📊 <b>Portfolio Value — {date_str}</b>",
        f"<b>Total: ${total_value_usd:,.0f}</b>  (cost ${total_cost_usd:,.0f})",
        f"Since start: <b>{_fmt(total_pnl_start)}</b> {_pct(total_pnl_pct)}",
        f"Day: {_fmt(total_pnl_day)}   Week: {_fmt(total_pnl_week)}   Month: {_fmt(total_pnl_month)}",
        "",
    ]

    # Compact digest: totals header (above) + the day's key movers only (≤3 up / ≤3 down).
    def _mover(r) -> str:
        prior = r["value_usd"] - r["pnl_day_usd"]
        pct = (r["pnl_day_usd"] / prior * 100) if prior else 0.0
        return f"{r['ticker']} {_fmt(r['pnl_day_usd'])} ({'+' if pct >= 0 else ''}{pct:.1f}%)"

    movers = [r for r in rows if abs(r["pnl_day_usd"]) >= 1]
    ups = sorted((r for r in movers if r["pnl_day_usd"] > 0), key=lambda r: r["pnl_day_usd"], reverse=True)[:3]
    downs = sorted((r for r in movers if r["pnl_day_usd"] < 0), key=lambda r: r["pnl_day_usd"])[:3]
    if ups:
        lines.append("📈 " + "  ·  ".join(_mover(r) for r in ups))
    if downs:
        lines.append("📉 " + "  ·  ".join(_mover(r) for r in downs))
    if not ups and not downs:
        lines.append("<i>No notable movers today.</i>")

    msg = "\n".join(lines)
    if as_section:
        return msg
    notifier.send(msg)
    print(f"[portfolio_value] sent report for {len(rows)} holdings", file=sys.stderr)


if __name__ == "__main__":
    run()
