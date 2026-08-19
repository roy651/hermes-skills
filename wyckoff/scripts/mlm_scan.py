#!/usr/bin/env python3
"""Meta-Labelled Momentum (MLM) scan — the primary entry report.

Two stages, deliberately in this order:

  PRIMARY   `mom_12_1 > 30%`. The only detector that cleared the promotion gate on its own
            (+2.58% mean 6m excess, t=4.43, positive in both market regimes).
  SECONDARY a gradient-boosting model trained on context features answers "is this a good
            MOMENT for momentum?", not "is this a good stock" — the primary settled that.
            Walk-forward evaluation put its top decile at +8.21% against a +4.68% baseline.

The Wyckoff read is reported alongside but is NOT a filter and NOT a ranking input. On the
same panel it measured NEGATIVE (-1.22%, t=-2.92 on its own) and it DEGRADED momentum when
intersected (+2.91% -> +1.10%). It is shown for continuity and human judgement, flagged, and
should be treated as evidence against a name rather than for it until that reverses.

    python mlm_scan.py                # top 20, send digest
    python mlm_scan.py --dry-run      # print instead
    python mlm_scan.py --top 40 --include TICKER,TICKER
"""
from __future__ import annotations

import argparse
import pickle
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")

import detectors as D
import events as wyk
import holdings as portfolio
import notifier

CACHE = Path(__file__).parent.parent / "research" / "cache"
TZ = ZoneInfo("Asia/Jerusalem")
FEATURES = ["dd", "atr_pct", "rsi14", "vol_ratio", "bb_width", "close_pos",
            "ret21", "ret63", "ret126", "mom_12_1", "ma200_slope", "ma50_slope",
            "dist_days", "obv", "rng"]
MOM_THRESHOLD = 0.30
MIN_PRICE_USD = 5.0        # below this, spread and impact dominate any edge we could have


def train_meta(obs: pd.DataFrame, panel: dict):
    """Train on every historical observation where the primary fired."""
    fired = obs[obs.mom_12_1_strong]
    rows = []
    for t, grp in fired.groupby("ticker", sort=False):
        df = panel.get(t)
        if df is None or len(df) < 400:
            continue
        f = D.compute_features(df)
        idx = f.index
        arrs = {c: f[c].to_numpy() for c in FEATURES if c in f.columns}
        for r in grp.itertuples():
            i = idx.searchsorted(r.date, side="right") - 1
            if i < 260:
                continue
            rec = {"date": r.date, "x6": r.x6}
            for c, a in arrs.items():
                rec[c] = a[i]
            rows.append(rec)
    tr = pd.DataFrame(rows).dropna(subset=["x6"])
    tr["xs_dispersion"] = tr.groupby("date")["ret63"].transform("std")
    tr["xs_breadth"] = tr.groupby("date")["ret63"].transform(lambda s: (s > 0).mean())
    feats = [c for c in FEATURES if c in tr.columns] + ["xs_dispersion", "xs_breadth"]
    m = HistGradientBoostingClassifier(max_depth=3, max_iter=200, learning_rate=0.05,
                                       min_samples_leaf=50, l2_regularization=1.0,
                                       random_state=0)
    m.fit(tr[feats].to_numpy(), (tr.x6 > 0).astype(int).to_numpy())
    print(f"[mlm] trained on {len(tr):,} historical signals over {tr.date.nunique()} dates",
          file=sys.stderr)
    return m, feats


def score_today(panel: dict, model, feats: list[str]) -> tuple[pd.DataFrame, dict]:
    """Feature-and-score the latest COMPLETED bar of every ticker whose primary fires.

    The scan runs at 16:00 Israel time: US markets are shut (so their last bar is complete)
    but TASE is mid-session, and a partial bar has partial volume — which every volume-ratio
    feature would read as supply drying up. Dropping any bar dated today removes that whole
    class of error rather than special-casing exchanges."""
    today = pd.Timestamp(datetime.now(tz=TZ).date())
    rows, dropped = [], {"microcap": [], "below_falling_200": []}
    for t, df in panel.items():
        if df is None or len(df) < 400 or t.startswith("^"):
            continue
        df = df[df.index < today]                      # completed bars only
        if len(df) < 400:
            continue
        try:
            f = D.compute_features(df)
        except Exception:
            continue
        i = len(f) - 1
        mom = f["mom_12_1"].iloc[i]
        if not np.isfinite(mom) or mom <= MOM_THRESHOLD:
            continue                                    # primary did not fire
        price = float(f["close"].iloc[i])
        # TASE quotes in agorot; compare on a common scale before applying a price floor.
        price_usd_ish = price / 100 if t.endswith(".TA") else price
        if price_usd_ish < MIN_PRICE_USD:
            dropped["microcap"].append(t)
            continue

        ns = SimpleNamespace(**{c: f[c].to_numpy() for c in f.columns})
        if D.ALL["below_falling_200"][1](ns, i):
            dropped["below_falling_200"].append(t)      # the one validated bearish detector
            continue

        rec = {"ticker": t, "bar_date": f.index[i], "price": price,
               "mom": float(mom) * 100, "dd_pct": float(f["dd"].iloc[i]) * 100}
        for c in FEATURES:
            rec[c] = float(f[c].iloc[i]) if c in f.columns else np.nan
        try:
            ev = wyk.detect_events(df.tail(120))
            rec["wyckoff"] = "entry-event" if wyk.has_entry_event(ev) else "—"
        except Exception:
            rec["wyckoff"] = "—"
        rows.append(rec)

    c = pd.DataFrame(rows)
    if c.empty:
        return c, dropped
    # Cross-sectional context is computed across TODAY's candidates, mirroring training.
    c["xs_dispersion"] = c["ret63"].std()
    c["xs_breadth"] = (c["ret63"] > 0).mean()
    c["meta_p"] = model.predict_proba(c[feats].to_numpy())[:, 1]
    c["meta_pct"] = c["meta_p"].rank(pct=True) * 100
    return c.sort_values("meta_p", ascending=False), dropped


def _fmt_price(ticker: str, px: float) -> str:
    """TASE bars arrive in agorot (1/100 ILS) — build_universe keeps them raw, unlike data.py.
    Scale-invariant for returns, wrong for a human reading a price."""
    if ticker.endswith(".TA"):
        return f"₪{px / 100:,.2f}"
    if ticker.endswith((".L", ".AX", ".TO", ".SW")):
        return f"{px:,.2f} (local)"
    return f"${px:,.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include", default="", help="comma-separated tickers to force-report")
    args = ap.parse_args()

    panel = pickle.load(open(CACHE / "panel.pkl", "rb"))
    obs = pd.read_pickle(CACHE / "observations.pkl")
    model, feats = train_meta(obs, panel)
    cand, dropped = score_today(panel, model, feats)

    # Regime gate. Every trend detector we tested FLIPS SIGN when SPY is below its 200-day,
    # so in risk-off this list is not merely weaker — it points the wrong way. Say so loudly
    # rather than printing the same ranking with no warning.
    spy = panel.get("SPY")
    risk_on = True
    if spy is not None:
        c200 = spy["close"].rolling(200).mean()
        risk_on = bool(spy["close"].iloc[-1] > c200.iloc[-1])
    if cand.empty:
        print("[mlm] no candidates", file=sys.stderr)
        return

    held = set(portfolio.load())
    asof = str(cand["bar_date"].max())[:10]
    print(f"[mlm] {len(cand)} primary candidates as of {asof}", file=sys.stderr)

    top = cand.head(args.top)
    regime_line = ("" if risk_on else
                   "\n🚨 <b>RISK-OFF</b> — SPY is below its 200-day. Every trend detector we "
                   "tested reverses sign in this regime. Treat this entire list as suspect.\n")
    lines = [f"📈 <b>MLM Scan</b> — meta-labelled momentum — {datetime.now(tz=TZ):%Y-%m-%d}",
             f"<i>{len(cand)} names cleared the momentum primary; ranked by the meta-model. "
             f"Bars as of {asof} (completed sessions only).</i>",
             regime_line, ""]
    # Telegram renders regular messages in a proportional font, so an aligned table only
    # survives inside a monospace block. Kept to ~40 chars so it does not wrap on a phone.
    tbl = [" #  TICKER     PRICE  META   MOM  OFF-HI",
           "----------------------------------------"]
    for n, r in enumerate(top.itertuples(), 1):
        mark = "*" if r.ticker in held else ("!" if r.wyckoff != "—" else " ")
        px = r.price / 100 if r.ticker.endswith(".TA") else r.price
        px_s = f"{px:,.0f}" if px >= 1000 else f"{px:,.2f}"
        tbl.append(f"{n:>2}{mark} {r.ticker[:9]:<9} {px_s:>7} {r.meta_p*100:>5.0f} "
                   f"{r.mom:>+5.0f}% {r.dd_pct:>+5.0f}%")
    lines.append("<pre>" + "\n".join(tbl) + "</pre>")
    lines.append("<i>* = already held   ! = wyckoff entry-event (treat as caution)</i>")

    forced = [t.strip().upper() for t in args.include.split(",") if t.strip()]
    if forced:
        lines.append("\n<b>Cross-reference</b>")
        for t in forced:
            row = cand[cand.ticker == t]
            if row.empty:
                # Absent from the panel is NOT the same as "signal did not fire" — one is a
                # coverage gap, the other is information. Conflating them makes a silent
                # data failure read as a verdict.
                in_panel = panel.get(t) is not None and len(panel.get(t, [])) > 400
                reason = ("momentum below +30%" if in_panel
                          else "NOT IN PANEL — outside the S&P1500 + intl universe, no verdict")
                lines.append(f"• <b>{t}</b> — {reason}")
            else:
                r = row.iloc[0]
                pos = int((cand.meta_p > r.meta_p).sum()) + 1
                lines.append(f"• <b>{t}</b> — meta {r.meta_p*100:.0f}, ranked {pos} of "
                             f"{len(cand)}, mom {r.mom:+.0f}%")

    # Disclose what was filtered out. Silent suppression turns a ranked list into a black box.
    if dropped["microcap"] or dropped["below_falling_200"]:
        lines.append(f"\n<i>Filtered before ranking: {len(dropped['microcap'])} below "
                     f"${MIN_PRICE_USD:.0f} (spread/impact), "
                     f"{len(dropped['below_falling_200'])} firing below-falling-200dma "
                     f"(the one validated bearish detector).</i>")

    lines.append("\n<i>⚠️ Wyckoff is shown for continuity only. On this panel it measured "
                 "negative on its own (−1.22%, t=−2.92) and degraded momentum when combined "
                 "(+2.91% → +1.10%). Treat an entry-event as a caution, not a confirmation.</i>")
    msg = "\n".join(lines)
    print(msg) if args.dry_run else notifier.send(msg)


if __name__ == "__main__":
    main()
