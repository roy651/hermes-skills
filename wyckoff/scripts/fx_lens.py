#!/usr/bin/env python3
"""Currency lens — restate USD performance in the currency the money is actually spent in.

Every other number this skill prints is in dollars. That is the wrong unit for an
ILS-based holder: a US position can gain in USD and still lose purchasing power. This
module makes the translation explicit rather than invisible.

It also reports the FX regime itself, because USD/ILS is not noise around a fixed level —
it trends, and the trend has been worth more than most position-level decisions.

CLI:
    python fx_lens.py              # FX regime + what it did to common benchmarks
    python fx_lens.py --holdings   # same, applied to the live portfolio
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import data as market_data

PAIR = "USDILS=X"


def fx_series(days: int = 400):
    return market_data.fetch_ohlcv(PAIR, days=days).df["close"]


def ils_return(usd_return_pct: float, fx_start: float, fx_end: float) -> float:
    """Translate a USD return into ILS.

    A USD asset held by a shekel spender earns (1+r_usd) * (fx_end/fx_start) - 1, where fx
    is USD/ILS. A falling pair (shekel strengthening) subtracts from every dollar return."""
    return ((1 + usd_return_pct / 100) * (fx_end / fx_start) - 1) * 100


def _period_start(series, when: str):
    """Index position of the first observation on/after `when`."""
    idx = [d for d in series.index if d >= when]
    return idx[0] if idx else series.index[0]


def report(tickers: list[str], holdings_map: dict | None = None) -> None:
    fx = fx_series()
    fx_now = fx.iloc[-1]
    ytd_start = _period_start(fx, date(date.today().year, 1, 1))
    fx_ytd = fx.loc[ytd_start]
    fx_1y = fx.iloc[0]

    hi52, lo52 = fx.tail(252).max(), fx.tail(252).min()
    pos = (fx_now - lo52) / (hi52 - lo52) * 100 if hi52 > lo52 else 50.0

    print(f"\nUSD/ILS  {fx_now:.4f}     "
          f"YTD {(fx_now/fx_ytd-1)*100:+.1f}%    1y {(fx_now/fx_1y-1)*100:+.1f}%")
    print(f"52w range {lo52:.3f} – {hi52:.3f}   position {pos:.0f}%")
    drag = (fx_now / fx_ytd - 1) * 100
    verb = "costs" if drag < 0 else "adds"
    print(f"\nHolding unhedged dollars {verb} a shekel spender {abs(drag):.1f}% "
          f"since January, before any position even moves.\n")

    print(f"{'ticker':<10}{'USD %':>10}{'ILS %':>10}{'FX drag':>10}   name")
    print("-" * 68)
    for t in tickers:
        try:
            df = market_data.fetch_ohlcv(t, days=400)
        except ValueError as e:
            print(f"{t:<10}  unavailable ({e})")
            continue
        s = df.df["close"]
        start = _period_start(s, date(date.today().year, 1, 1))
        usd = (s.iloc[-1] / s.loc[start] - 1) * 100
        ils = ils_return(usd, fx_ytd, fx_now)
        print(f"{t:<10}{usd:>10.1f}{ils:>10.1f}{ils - usd:>10.1f}   {df.name[:28]}")

    if holdings_map:
        print(f"\n{'position':<10}{'USD %':>10}{'ILS %':>10}   (vs average cost)")
        print("-" * 52)
        for t, h in sorted(holdings_map.items()):
            cost = h.get("avg_cost")
            if not cost:
                continue
            try:
                last = market_data.fetch_ohlcv(t, days=10).df["close"].iloc[-1]
            except ValueError:
                continue
            usd = (last / cost - 1) * 100
            # Approximation: uses the YTD FX move, not the FX rate at each entry date.
            # Exact attribution needs a per-position entry FX rate, which holdings.json
            # does not record — worth adding when a position is next opened.
            print(f"{t:<10}{usd:>10.1f}{ils_return(usd, fx_ytd, fx_now):>10.1f}")


BENCHMARKS = ["SPY", "SGOV", "IWM", "EFA", "DGRO", "GLD"]

if __name__ == "__main__":
    hmap = None
    if "--holdings" in sys.argv:
        import holdings
        hmap = holdings.load()
    report(BENCHMARKS, hmap)
