#!/usr/bin/env python3
"""Daily portfolio valuation — total value, P&L from start, day/week/month.

P/L is unrealized (open holdings vs cost) plus realized (sells in trade_log.jsonl vs their
recorded cost). Without the realized half a closed position's gain vanished from every line the
moment it left holdings.json. A realized sell counts in a period at sale price minus the price
at the period's start, or minus cost when the position was opened inside the period — the same
rule open positions use. The total includes the broker cash balance from cash.py.
"""
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
import cash
import trade_log

TZ = ZoneInfo("Asia/Jerusalem")
# Fallback now lives in fx_rate, which persists the last good rate.


def _boundary(ref_date: date, period: str, session: date) -> date:
    """First date inside the period. 'day' is the latest trading session."""
    if period == "day":
        return session
    if period == "week":
        return ref_date - timedelta(days=ref_date.weekday())
    return ref_date.replace(day=1)


def _close_before(df, boundary: date) -> float | None:
    """Last close strictly before the boundary — the price the period started from. Taking the
    first close INSIDE the period instead made Week read +$0 every Monday."""
    before = df[df.index < boundary]
    if not before.empty:
        return float(before["close"].iloc[-1])
    inside = df[df.index >= boundary]
    return float(inside["close"].iloc[0]) if not inside.empty else None


def _period_price(df, ref_date: date, period: str) -> float | None:
    """Return the closing price at the start of the given period ('day', 'week', 'month')."""
    if period == "day":
        return float(df["close"].iloc[-2]) if len(df) >= 2 else None
    return _close_before(df, _boundary(ref_date, period, ref_date))


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
    sells, _ = trade_log.replay()
    month_start = today.replace(day=1)
    # Tickers sold this month need a price history for the period figures; older sells only
    # need their recorded cost.
    sold_recent = {s["ticker"] for s in sells if s["date"] >= month_start.isoformat()} - set(tickers)
    all_fetch = [t for t in tickers if t not in fund_tickers] + sorted(sold_recent) + ["USDILS=X"]
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
                elif open_date >= _boundary(today, period, today):
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

    # ── Realized P/L from the trade ledger ────────────────────────────────────────────
    us_sessions = [td.df.index[-1] for t, td in raw.items() if td.currency == "USD" and len(td.df)]
    session = max(us_sessions) if us_sessions else today
    realized = {"start": 0.0, "day": 0.0, "week": 0.0, "month": 0.0}
    closed_cost_usd = 0.0
    week_closes: dict[str, list[float]] = {}          # ticker -> [pnl_usd, cost_usd]
    uncosted = set()
    for s in sells:
        if s["cost"] is None:
            uncosted.add(s["ticker"])
            continue
        to_usd = (1.0 / usdils_rate) if trade_log.currency(s["ticker"]) == "ILS" else 1.0
        q, px, cost = float(s["qty"]), float(s["price"]), float(s["cost"])
        realized["start"] += q * (px - cost) * to_usd
        closed_cost_usd += q * cost * to_usd
        sdate = date.fromisoformat(s["date"])
        td = raw.get(s["ticker"])
        for period in ("day", "week", "month"):
            b = _boundary(today, period, session)
            if sdate < b:
                continue
            opened = s["cycle_open"] and date.fromisoformat(s["cycle_open"]) >= b
            ref = cost if (opened or td is None) else _close_before(td.df, b)
            if ref is None:
                ref = cost
            realized[period] += q * (px - ref) * to_usd
        if sdate >= _boundary(today, "week", session):
            acc = week_closes.setdefault(s["ticker"], [0.0, 0.0])
            acc[0] += q * (px - cost) * to_usd
            acc[1] += q * cost * to_usd
    if uncosted:
        print(f"[portfolio_value] sells without a cost basis, not counted: {sorted(uncosted)}", file=sys.stderr)

    cash_ils = cash.balance_ils()
    cash_usd = cash_ils / usdils_rate if cash_ils is not None else 0.0

    unrealized = total_value_usd - total_cost_usd
    total_pnl_start = unrealized + realized["start"]
    deployed = total_cost_usd + closed_cost_usd
    total_pnl_pct = total_pnl_start / deployed if deployed else 0.0
    total_pnl_day = sum(r["pnl_day_usd"] for r in rows) + realized["day"]
    total_pnl_week = sum(r["pnl_week_usd"] for r in rows) + realized["week"]
    total_pnl_month = sum(r["pnl_month_usd"] for r in rows) + realized["month"]

    def _fmt(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return f"{sign}${v:,.0f}"

    def _pct(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return f"({sign}{v * 100:.1f}%)"

    if cash_ils is not None:
        total_line = (f"<b>Total: ${total_value_usd + cash_usd:,.0f}</b>  "
                      f"(holdings ${total_value_usd:,.0f}, cost ${total_cost_usd:,.0f} · "
                      f"cash ${cash_usd:,.0f} = ₪{cash_ils:,.0f})")
    else:
        total_line = (f"<b>Total: ${total_value_usd:,.0f}</b>  (cost ${total_cost_usd:,.0f}) "
                      f"<i>· cash not set</i>")
    lines = [
        f"📊 <b>Portfolio Value — {date_str}</b>",
        total_line,
        f"Since start: <b>{_fmt(total_pnl_start)}</b> {_pct(total_pnl_pct)}  "
        f"<i>(open {_fmt(unrealized)} · realized {_fmt(realized['start'])})</i>",
        f"Day: {_fmt(total_pnl_day)}   Week: {_fmt(total_pnl_week)}   Month: {_fmt(total_pnl_month)}",
        "",
    ]
    # A sell's day share is measured from the prior close, not from cost, so it can differ a lot
    # from the "Realized this week" figure below. Show it, or the gap reads as an error.
    if abs(realized["day"]) >= 0.5:
        lines[3] += (f"  <i>(day: open {_fmt(total_pnl_day - realized['day'])} · "
                     f"sold {_fmt(realized['day'])} vs prior close)</i>")
    if week_closes:
        parts = []
        for t, (pnl, cost_usd) in sorted(week_closes.items(), key=lambda kv: -kv[1][0]):
            pct = f" ({'+' if pnl >= 0 else ''}{pnl / cost_usd * 100:.1f}%)" if cost_usd else ""
            state = "closed" if t not in holdings_map else "trimmed"
            parts.append(f"{t} {state} {_fmt(pnl)}{pct}")
        lines.append("✅ Realized this week (vs cost): " + "  ·  ".join(parts))

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
