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


def score_today(panel: dict, model, feats: list[str]) -> pd.DataFrame:
    """Feature-and-score the LATEST bar of every ticker whose primary fires."""
    rows = []
    for t, df in panel.items():
        if df is None or len(df) < 400 or t.startswith("^"):
            continue
        try:
            f = D.compute_features(df)
        except Exception:
            continue
        i = len(f) - 1
        mom = f["mom_12_1"].iloc[i]
        if not np.isfinite(mom) or mom <= MOM_THRESHOLD:
            continue                                    # primary did not fire
        rec = {"ticker": t, "bar_date": f.index[i], "price": float(f["close"].iloc[i]),
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
        return c
    # Cross-sectional context is computed across TODAY's candidates, mirroring training.
    c["xs_dispersion"] = c["ret63"].std()
    c["xs_breadth"] = (c["ret63"] > 0).mean()
    c["meta_p"] = model.predict_proba(c[feats].to_numpy())[:, 1]
    c["meta_pct"] = c["meta_p"].rank(pct=True) * 100
    return c.sort_values("meta_p", ascending=False)


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
    cand = score_today(panel, model, feats)
    if cand.empty:
        print("[mlm] no candidates", file=sys.stderr)
        return

    held = set(portfolio.load())
    asof = str(cand["bar_date"].max())[:10]
    print(f"[mlm] {len(cand)} primary candidates as of {asof}", file=sys.stderr)

    top = cand.head(args.top)
    lines = [f"📈 <b>MLM Scan</b> — meta-labelled momentum — {datetime.now(tz=TZ):%Y-%m-%d}",
             f"<i>{len(cand)} names cleared the momentum primary; ranked by the meta-model. "
             f"Bars as of {asof}.</i>", ""]
    for r in top.itertuples():
        tag = " ⭐held" if r.ticker in held else ""
        wy = "" if r.wyckoff == "—" else "  ·  wyckoff: entry-event ⚠️"
        rank = top.index.get_loc(r.Index) + 1
        lines.append(f"{rank}. <b>{r.ticker}</b>{tag} · {_fmt_price(r.ticker, r.price)} · "
                     f"meta {r.meta_p*100:.0f} · mom {r.mom:+.0f}% · "
                     f"{r.dd_pct:+.0f}% off high{wy}")

    forced = [t.strip().upper() for t in args.include.split(",") if t.strip()]
    if forced:
        lines.append("\n<b>Cross-reference</b>")
        for t in forced:
            row = cand[cand.ticker == t]
            if row.empty:
                lines.append(f"• <b>{t}</b> — primary did NOT fire (momentum below +30%)")
            else:
                r = row.iloc[0]
                pos = int((cand.meta_p > r.meta_p).sum()) + 1
                lines.append(f"• <b>{t}</b> — meta {r.meta_p*100:.0f}, ranked {pos} of "
                             f"{len(cand)}, mom {r.mom:+.0f}%")

    lines.append("\n<i>⚠️ Wyckoff is shown for continuity only. On this panel it measured "
                 "negative on its own (−1.22%, t=−2.92) and degraded momentum when combined "
                 "(+2.91% → +1.10%). Treat an entry-event as a caution, not a confirmation.</i>")
    msg = "\n".join(lines)
    print(msg) if args.dry_run else notifier.send(msg)


if __name__ == "__main__":
    main()
