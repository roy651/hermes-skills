#!/usr/bin/env python3
"""Portfolio backtest — turns a ranked signal into an equity curve, after costs.

Everything measured up to now is a SIGNAL study: one date, one forward return, averaged
cross-sectionally. That establishes an edge exists. It does not say a portfolio built on it
makes money, because a portfolio has a fixed number of slots, pays to trade, and holds names
past the horizon the signal was measured over. Those are different claims and this measures
the second one.

Rules implemented (spec: docs/strategy-spec.md §2):
  · N is a HARD CAP on positions held. M is a RETENTION band, not a size — a held name ranked
    inside M keeps its slot, which is what stops the portfolio churning on rank noise.
  · Unfilled slots go to SPY, never cash. A momentum filter that sits in cash during a
    drawdown is making a market-timing bet nothing here has validated.
  · Costs are charged as a round-trip figure, half on each side, on traded notional.

Usage:  backtest.py [--arms] [--cadences] [--costs]
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import detectors as D

CACHE = Path(__file__).parent / "cache"
START_EQUITY = 100.0
TRADING_DAYS = 252


# ----------------------------------------------------------------- price matrices

def matrices(tickers: set[str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """close / high / ATR14 aligned on one calendar, forward-filled across holidays."""
    panel = pickle.load(open(CACHE / "panel.pkl", "rb"))
    keep = [t for t in tickers if panel.get(t) is not None]
    close = pd.DataFrame({t: panel[t]["close"] for t in keep})
    high = pd.DataFrame({t: panel[t]["high"] for t in keep})
    atr = pd.DataFrame({t: D.compute_features(panel[t])["atr14"] for t in keep})
    idx = close.index.sort_values()
    return close.reindex(idx).ffill(), high.reindex(idx).ffill(), atr.reindex(idx).ffill()


def cadence_dates(all_dates: list[pd.Timestamp], cadence: str) -> list[pd.Timestamp]:
    d = pd.DatetimeIndex(sorted(all_dates))
    if cadence == "weekly":
        return list(d)
    if cadence == "biweekly":
        return list(d[::2])
    month_ends = pd.Series(d, index=d).groupby([d.year, d.month]).max().tolist()
    if cadence == "monthly":
        return month_ends
    if cadence == "bimonthly":
        return month_ends[::2]
    raise ValueError(cadence)


# ----------------------------------------------------------------- the simulation

def run(grid: pd.DataFrame, close: pd.DataFrame, high: pd.DataFrame, atr: pd.DataFrame,
        rebal: list[pd.Timestamp], rank_col="rank_p", n=10, m=20, cost_bps=10.0,
        hold_months=None, use_stop=False, fill_with="SPY", entry_max_mom=None) -> dict:

    by_date = {d: g.set_index("ticker") for d, g in grid.groupby("date")}
    side_cost = cost_bps / 2 / 10_000

    shares: dict[str, float] = {}
    entry: dict[str, pd.Timestamp] = {}
    peak: dict[str, float] = {}
    cash = START_EQUITY
    equity, trades, holding_spans = [], 0, []
    rebal_set = set(rebal)

    days = [d for d in close.index if d >= rebal[0]]
    for d in days:
        px = close.loc[d]
        mtm = cash + sum(q * px.get(t, np.nan) for t, q in shares.items())
        if not np.isfinite(mtm):
            mtm = equity[-1][1] if equity else START_EQUITY
        equity.append((d, mtm))

        for t in list(shares):                       # keep the trailing peak current daily
            h = high.at[d, t] if t in high.columns else np.nan
            if np.isfinite(h):
                peak[t] = max(peak.get(t, h), h)

        if d not in rebal_set:
            continue

        ranked = by_date.get(d)

        def rank_of(t):
            if ranked is None or t not in ranked.index:
                return np.inf                        # primary no longer fires
            return float(ranked.at[t, rank_col])

        # ---- EXIT
        for t in list(shares):
            if t == fill_with:
                continue
            reason = None
            if hold_months is not None:
                if (d - entry[t]).days >= hold_months * 30:
                    reason = "held out"
            elif rank_of(t) > m:
                reason = "rank drop"
            if reason is None and use_stop:
                a = atr.at[d, t] if t in atr.columns else np.nan
                if np.isfinite(a) and np.isfinite(px.get(t, np.nan)) \
                   and px[t] < peak.get(t, np.inf) - 3 * a:
                    reason = "stop"
            if reason:
                cash += shares[t] * px[t] * (1 - side_cost)
                holding_spans.append((d - entry[t]).days)
                trades += 1
                del shares[t], entry[t]
                peak.pop(t, None)

        # ---- FILL. N is a hard cap; candidates are taken in rank order, skipping holds.
        held = {t for t in shares if t != fill_with}
        free = n - len(held)
        if free > 0 and ranked is not None:
            pool = ranked
            # An ENTRY-only band. Capping momentum at purchase says "do not chase a name that
            # has already tripled"; applying the same cap to holdings would force a sale every
            # time a winner ran, which is the opposite of what a momentum sleeve should do.
            if entry_max_mom is not None:
                pool = pool[pool.mom_12_1 < entry_max_mom]
            cands = [t for t in pool.sort_values(rank_col).index
                     if t not in held and t in close.columns
                     and np.isfinite(px.get(t, np.nan))][:free]
        else:
            cands = []

        # Sell the SPY placeholder so its capital is available to the new names.
        if fill_with in shares and (cands or free <= 0):
            cash += shares[fill_with] * px[fill_with] * (1 - side_cost)
            del shares[fill_with]

        equity_now = cash + sum(q * px.get(t, np.nan) for t, q in shares.items())
        slot = equity_now / n
        for t in cands:
            spend = min(slot, cash)
            if spend <= 0:
                break
            shares[t] = spend * (1 - side_cost) / px[t]
            entry[t] = d
            peak[t] = high.at[d, t] if t in high.columns else px[t]
            cash -= spend
            trades += 1

        unfilled = n - len([t for t in shares if t != fill_with])
        if unfilled > 0 and fill_with and cash > 0 and np.isfinite(px.get(fill_with, np.nan)):
            spend = min(cash, slot * unfilled)
            shares[fill_with] = shares.get(fill_with, 0) + spend * (1 - side_cost) / px[fill_with]
            cash -= spend

    eq = pd.Series(dict(equity)).sort_index()
    return {"equity": eq, "trades": trades, "n_rebal": len(rebal),
            "avg_hold_days": float(np.mean(holding_spans)) if holding_spans else np.nan}


# ----------------------------------------------------------------- scoring

def stats(eq: pd.Series, bench: pd.Series, trades=0, n=10, avg_hold=np.nan) -> dict:
    r = eq.pct_change().dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    vol = r.std() * np.sqrt(TRADING_DAYS)
    dn = r[r < 0].std() * np.sqrt(TRADING_DAYS)
    dd = (eq / eq.cummax() - 1).min()

    b = bench.reindex(eq.index).ffill()
    bcagr = (b.iloc[-1] / b.iloc[0]) ** (1 / yrs) - 1
    monthly_x = (r - b.pct_change().reindex(r.index).fillna(0)).resample("ME").sum()
    t = monthly_x.mean() / (monthly_x.std(ddof=1) / np.sqrt(len(monthly_x))) if len(monthly_x) > 8 else np.nan

    return {"CAGR%": cagr * 100, "vol%": vol * 100, "Sharpe": cagr / vol if vol else np.nan,
            "Sortino": cagr / dn if dn else np.nan, "maxDD%": dd * 100,
            "vsSPY%": (cagr - bcagr) * 100, "t_exc": t,
            "trades/yr": trades / yrs, "hold_d": avg_hold}


def per_year(eq: pd.Series, bench: pd.Series) -> pd.DataFrame:
    a = eq.resample("YE").last().pct_change().dropna() * 100
    b = bench.reindex(eq.index).ffill().resample("YE").last().pct_change().dropna() * 100
    out = pd.DataFrame({"strategy%": a, "SPY%": b})
    out["excess%"] = out["strategy%"] - out["SPY%"]
    out.index = out.index.year
    return out


# ----------------------------------------------------------------- experiment runner

def smoothed_rank(grid: pd.DataFrame, window=5) -> pd.DataFrame:
    """Rolling mean of a name's rank over its last `window` appearances.

    The point is not cosmetic. A single fit reshuffles near-tied probabilities, so an unsmoothed
    top-10 churns on noise rather than on information. Averaging is a noise filter, and the
    dominant feature moves slowly, so this can improve the signal rather than merely steady it.
    """
    g = grid.sort_values(["ticker", "date"]).copy()
    g["rank_smooth"] = (g.groupby("ticker")["rank_p"]
                         .transform(lambda s: s.rolling(window, min_periods=1).mean()))
    # Re-rank within each date so the smoothed score is still a same-day ordering.
    g["rank_smooth"] = g.groupby("date")["rank_smooth"].rank(method="first")
    return g.sort_values(["date", "rank_smooth"])


def bench_curves(close: pd.DataFrame, grid: pd.DataFrame, days) -> dict[str, pd.Series]:
    spy = close["SPY"].reindex(days).ffill()
    names = sorted(set(grid.ticker))
    sub = close[[c for c in names if c in close.columns]].reindex(days).ffill()
    ew = (1 + sub.pct_change().mean(axis=1).fillna(0)).cumprod() * START_EQUITY
    return {"SPY": spy / spy.iloc[0] * START_EQUITY, "EW candidates": ew}


def main():
    pd.set_option("display.width", 220)
    grid = pd.read_pickle(CACHE / "pred_grid.pkl")
    grid = smoothed_rank(grid)
    tick = set(grid.ticker) | {"SPY"}
    close, high, atr = matrices(tick)
    all_dates = sorted(grid.date.unique())
    print(f"[bt] {len(tick)} tickers · {len(all_dates)} grid dates "
          f"{all_dates[0].date()}..{all_dates[-1].date()}", file=sys.stderr)

    monthly = cadence_dates(all_dates, "monthly")
    spy = close["SPY"]

    def go(label, **kw):
        rebal = kw.pop("rebal", monthly)
        r = run(grid, close, high, atr, rebal, **kw)
        s = stats(r["equity"], spy, r["trades"], avg_hold=r["avg_hold_days"])
        return {"arm": label, **s}, r["equity"]

    results, curves = [], {}

    if "--arms" in sys.argv or len(sys.argv) == 1:
        print("\n### RANKING ARMS — monthly, N=10, M=20, 10bp round trip")
        for lab, col in [("R1 momentum (no ML)", "rank_mom"),
                         ("R2 meta-probability", "rank_p"),
                         ("R3 smoothed meta rank", "rank_smooth")]:
            row, eq = go(lab, rank_col=col)
            results.append(row); curves[lab] = eq
        b = bench_curves(close, grid, results and curves[next(iter(curves))].index)
        for lab, eq in b.items():
            results.append({"arm": f"[bench] {lab}", **stats(eq, spy)})
        print(pd.DataFrame(results).to_string(index=False, float_format=lambda v: f"{v:8.2f}"))
        print("\n### PER YEAR — R2 meta-probability vs SPY")
        print(per_year(curves["R2 meta-probability"], spy).to_string(float_format=lambda v: f"{v:8.2f}"))

    if "--cadences" in sys.argv:
        print("\n### CADENCE — R2, N=10, M=20, 10bp")
        rows = []
        for cad in ("weekly", "biweekly", "monthly", "bimonthly"):
            row, _ = go(cad, rank_col="rank_p", rebal=cadence_dates(all_dates, cad))
            rows.append(row)
        print(pd.DataFrame(rows).to_string(index=False, float_format=lambda v: f"{v:8.2f}"))

    if "--holding" in sys.argv:
        print("\n### HOLDING RULE — R2, monthly, N=10, 10bp")
        rows = []
        for m_ in (10, 15, 20, 30):
            rows.append(go(f"H2 buffer M={m_}", rank_col="rank_p", m=m_)[0])
        for k in (3, 6, 12):
            rows.append(go(f"H1 fixed {k}m", rank_col="rank_p", hold_months=k)[0])
        rows.append(go("H3 buffer M=20 + stop", rank_col="rank_p", m=20, use_stop=True)[0])
        print(pd.DataFrame(rows).to_string(index=False, float_format=lambda v: f"{v:8.2f}"))

    if "--costs" in sys.argv:
        print("\n### COSTS — R2, monthly, N=10, M=20")
        rows = [go(f"{c}bp round trip", rank_col="rank_p", cost_bps=c)[0]
                for c in (0, 5, 10, 20, 30)]
        rows.append(go("cash instead of SPY", rank_col="rank_p", fill_with=None)[0])
        print(pd.DataFrame(rows).to_string(index=False, float_format=lambda v: f"{v:8.2f}"))


if __name__ == "__main__":
    main()
