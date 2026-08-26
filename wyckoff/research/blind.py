#!/usr/bin/env python3
"""Blinded fundamental analysis: can a model rank companies it cannot recognise?

The problem this solves. Asking a model to analyse a 2019 filing is contaminated — it may
remember what happened. But that memory is UNEVEN: salient names and events are recalled well,
a mid-cap's 2019 quarter is not. So rather than assume contamination and abandon the test, this
strips the identity out of the filing and measures what is left.

Anonymisation is the whole experiment, so it is deliberately aggressive:
  * no ticker, no company name, no sector
  * no absolute dates — periods are labelled Q-11 .. Q0
  * NO ABSOLUTE MONETARY FIGURES. Everything is expressed as a margin, a growth rate or a
    ratio. Revenue of $11.8bn identifies Coca-Cola; a 60% gross margin does not.

The scoring target is forward EXCESS return over the cross-section, so "the market went up" is
never the answer.

Two control arms make the result interpretable:
  shell   — the same prompt with the fundamentals REMOVED. Scores above chance here mean the
            model is recognising something despite the blinding, and the main arm is discounted
            by exactly that much.
  shuffle — real fundamentals paired with the WRONG company's forward return. This must score
            at chance; if it does not, the harness itself is leaking.

Usage:  blind.py --build [--n 300]      # construct blinded cases + held-out outcomes
"""
from __future__ import annotations

import json
import pickle
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import fundamentals as F

CACHE = Path(__file__).parent / "cache"
QUARTERS = 12                 # history shown per case
FWD_DAYS = 126                # six months — long enough for fundamentals to matter
MIN_PRICE = 5.0

# Concept -> our label. Several are alternates for the same idea; first hit wins.
CONCEPTS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "SalesRevenueNet"],
    # Filers differ on which concept they tag. PCAR, for one, reports neither GrossProfit nor
    # OperatingIncomeLoss under the plain names, which left four of fourteen columns empty.
    "gross_profit": ["GrossProfit", "GrossProfitLoss"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "operating_income": ["OperatingIncomeLoss",
                         "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                         "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "net_income": ["NetIncomeLoss"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "assets": ["Assets"],
    "equity": ["StockholdersEquity"],
    "debt": ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations",
             "DebtLongtermAndShorttermCombinedAmount"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "inventory": ["InventoryNet"],
    "receivables": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent",
                    "AccountsAndOtherReceivablesNetCurrent",
                    "AccountsReceivableGrossCurrent"],
}


def _series(facts: dict, tags: list[str], as_of: pd.Timestamp, quarterly: bool) -> pd.DataFrame:
    """Point-in-time series for the first tag that yields data: only facts FILED by as_of."""
    units = facts.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        rows = []
        for entries in units.get(tag, {}).get("units", {}).values():
            for e in entries:
                if not e.get("filed") or not e.get("end"):
                    continue
                filed = pd.Timestamp(e["filed"])
                if filed > as_of:
                    continue                       # not knowable yet — the whole point
                end = pd.Timestamp(e["end"])
                if quarterly:
                    st = e.get("start")
                    if not st:
                        continue
                    span = (end - pd.Timestamp(st)).days
                    if not (60 <= span <= 120):
                        continue
                rows.append({"end": end, "val": float(e["val"]), "filed": filed})
        if rows:
            df = pd.DataFrame(rows).sort_values(["end", "filed"])
            return df.groupby("end", as_index=False).last()      # latest known as of as_of
    return pd.DataFrame(columns=["end", "val", "filed"])


def snapshot(facts: dict, as_of: pd.Timestamp) -> pd.DataFrame | None:
    """A scale-free, identity-free quarterly table as it stood on `as_of`."""
    flow = {k: _series(facts, t, as_of, quarterly=True)
            for k, t in CONCEPTS.items()
            if k in ("revenue", "gross_profit", "cost_of_revenue", "operating_income",
                     "net_income", "ocf", "capex")}
    stock = {k: _series(facts, t, as_of, quarterly=False)
             for k, t in CONCEPTS.items()
             if k in ("assets", "equity", "debt", "cash", "inventory", "receivables")}
    if flow["revenue"].empty or len(flow["revenue"]) < QUARTERS + 4:
        return None

    rev = flow["revenue"].set_index("end").val
    idx = rev.index[-QUARTERS:]
    out = pd.DataFrame(index=idx)

    def align(df):
        return df.set_index("end").val.reindex(idx, method="ffill") if not df.empty else pd.Series(np.nan, index=idx)

    r = rev.reindex(idx)
    out["rev_yoy_%"] = (r / rev.shift(4).reindex(idx) - 1) * 100
    out["rev_qoq_%"] = (r / rev.shift(1).reindex(idx) - 1) * 100
    # Derive gross profit from cost of revenue when the filer tags only the latter.
    gp = align(flow["gross_profit"])
    if gp.isna().all() and not flow["cost_of_revenue"].empty:
        flow["gross_profit"] = flow["revenue"].merge(
            flow["cost_of_revenue"], on="end", suffixes=("_r", "_c"))
        flow["gross_profit"]["val"] = flow["gross_profit"].val_r - flow["gross_profit"].val_c
        flow["gross_profit"]["filed"] = flow["gross_profit"].filed_r
    for k, lab in (("gross_profit", "gross_margin_%"), ("operating_income", "op_margin_%"),
                   ("net_income", "net_margin_%"), ("ocf", "ocf_margin_%")):
        out[lab] = align(flow[k]) / r * 100
    out["capex_of_rev_%"] = align(flow["capex"]) / r * 100
    out["fcf_margin_%"] = out["ocf_margin_%"] - out["capex_of_rev_%"]
    eq, at = align(stock["equity"]), align(stock["assets"])
    out["roe_%"] = align(flow["net_income"]) / eq * 100
    out["debt_to_equity"] = align(stock["debt"]) / eq
    out["cash_of_assets_%"] = align(stock["cash"]) / at * 100
    out["inv_of_rev_%"] = align(stock["inventory"]) / r * 100
    out["recv_of_rev_%"] = align(stock["receivables"]) / r * 100
    out["asset_turns"] = r * 4 / at
    out.index = [f"Q-{len(idx)-1-i}" for i in range(len(idx))]     # identity-free labels
    return out.replace([np.inf, -np.inf], np.nan).round(2)


def build(n_cases: int, seed: int = 11) -> None:
    panel = pickle.load(open(CACHE / "panel.pkl", "rb"))
    ev = pd.read_pickle(CACHE / "eps_events.pkl")
    us = {t for t, d in panel.items() if d is not None and "." not in t and len(d) > 600}
    close = pd.DataFrame({t: panel[t]["close"] for t in us if t in panel}).sort_index()
    bench = (close.shift(-FWD_DAYS) / close - 1).mean(axis=1)

    cands = ev[ev.ticker.isin(us)].copy()
    cands = cands[(cands.filed >= "2017-01-01") & (cands.filed <= close.index[-1] - pd.Timedelta(days=FWD_DAYS + 10))]
    rng = random.Random(seed)
    picks = rng.sample(range(len(cands)), min(len(cands), n_cases * 6))

    cases, cm = [], F.cik_map()
    for pi in picks:
        if len(cases) >= n_cases:
            break
        row = cands.iloc[pi]
        t, as_of = row.ticker, pd.Timestamp(row.filed)
        i = close.index.searchsorted(as_of, side="left")
        j = i + FWD_DAYS
        if j >= len(close.index):
            continue
        px = close[t].to_numpy()
        if not np.isfinite(px[i]) or px[i] < MIN_PRICE or not np.isfinite(px[j]):
            continue
        cik = cm.get(t.upper().replace(".", "-"))
        if not cik:
            continue
        facts = F.company_facts(t, cik)
        if not facts:
            continue
        try:
            snap = snapshot(facts, as_of)
        except Exception:
            continue
        if snap is None or snap.isna().mean().mean() > 0.35:
            continue
        b = bench.to_numpy()[i]
        if not np.isfinite(b):
            continue
        cases.append({"case_id": f"C{len(cases):04d}", "ticker": t,
                      "as_of": as_of.date().isoformat(),
                      "table": snap.to_string(),
                      "fwd_excess_%": round((px[j] / px[i] - 1 - b) * 100, 3)})
    out = CACHE / "blind_cases.json"
    out.write_text(json.dumps(cases, indent=1))
    d = pd.DataFrame(cases)
    print(f"[blind] {len(cases)} cases · {d.ticker.nunique()} tickers · "
          f"{d.as_of.min()}..{d.as_of.max()}", file=sys.stderr)
    print(f"[blind] forward excess: mean {d['fwd_excess_%'].mean():+.2f}%  "
          f"median {d['fwd_excess_%'].median():+.2f}%  sd {d['fwd_excess_%'].std():.2f}",
          file=sys.stderr)
    print(f"[blind] wrote {out}", file=sys.stderr)





# ------------------------------------------------------------------ sector/date-matched cases
# The first design asked a model to rank five companies from DIFFERENT sectors on DIFFERENT
# dates. That is close to unanswerable: a 60% gross margin is excellent for a retailer and
# mediocre for software, and comparing a bank to a biotech on "margin direction" is meaningless.
# The null it produced may have been a badly posed question rather than an absent ability.
#
# The fix keeps the blinding intact by putting the context into BATCH CONSTRUCTION rather than
# the prompt. All five companies share a sector and a calendar quarter, so both confounds are
# differenced away without either ever being named. On top of that each metric carries its
# PERCENTILE within that peer group — which is the "how is the segment doing" information,
# expressed relatively so it reveals neither the sector nor the date.

import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import features_extra as FX


def _pct_table(snap: pd.DataFrame, peers: list[pd.DataFrame]) -> pd.DataFrame:
    """Annotate the latest quarter's metrics with their percentile among peer companies."""
    out = snap.copy()
    latest = {c: snap[c].iloc[-1] for c in snap.columns}
    pcts = {}
    for c in snap.columns:
        vals = [p[c].iloc[-1] for p in peers if c in p.columns and pd.notna(p[c].iloc[-1])]
        v = latest[c]
        pcts[c] = (np.nan if (pd.isna(v) or len(vals) < 3)
                   else round(100 * sum(1 for x in vals if x < v) / len(vals)))
    out.loc["PCTILE"] = [pcts[c] for c in snap.columns]
    return out


def build_matched(n_batches: int, k: int = 5, seed: int = 21) -> None:
    panel = pickle.load(open(CACHE / "panel.pkl", "rb"))
    ev = pd.read_pickle(CACHE / "eps_events.pkl")
    sec = FX.sectors()
    us = {t for t, d in panel.items() if d is not None and "." not in t and len(d) > 600}
    close = pd.DataFrame({t: panel[t]["close"] for t in us if t in panel}).sort_index()
    cm = F.cik_map()

    ev = ev[ev.ticker.isin(us) & ev.ticker.isin(sec)].copy()
    ev["sector"] = ev.ticker.map(sec)
    ev["q"] = pd.to_datetime(ev.filed).dt.to_period("Q")
    ev = ev[(ev.filed >= "2017-01-01") &
            (ev.filed <= close.index[-1] - pd.Timedelta(days=FWD_DAYS + 10))]

    groups = [g for _, g in ev.groupby(["sector", "q"]) if g.ticker.nunique() >= k + 2]
    rng = random.Random(seed)
    rng.shuffle(groups)
    print(f"[matched] {len(groups)} sector-quarter groups with >= {k+2} companies",
          file=_sys.stderr)

    cases, done = [], 0
    for g in groups:
        if done >= n_batches:
            break
        g = g.drop_duplicates("ticker")
        picks = rng.sample(list(g.itertuples()), min(k + 3, len(g)))
        snaps, keep = [], []
        for r in picks:
            cik = cm.get(r.ticker.upper().replace(".", "-"))
            if not cik:
                continue
            facts = F.company_facts(r.ticker, cik)
            if not facts:
                continue
            try:
                s = snapshot(facts, pd.Timestamp(r.filed))
            except Exception:
                continue
            if s is None or s.isna().mean().mean() > 0.35:
                continue
            i = close.index.searchsorted(pd.Timestamp(r.filed), side="left")
            j = i + FWD_DAYS
            px = close[r.ticker].to_numpy()
            if j >= len(close.index) or not np.isfinite(px[i]) or px[i] < MIN_PRICE \
               or not np.isfinite(px[j]):
                continue
            snaps.append(s)
            keep.append((r, px[j] / px[i] - 1))
            if len(keep) == k:
                break
        if len(keep) < k:
            continue
        # Forward return relative to THIS peer group, not the whole market: sector and quarter
        # are shared, so what remains is company-specific performance.
        grp_mean = float(np.mean([x for _, x in keep]))
        for (r, ret), s in zip(keep, snaps):
            cases.append({"case_id": f"M{len(cases):04d}", "batch_id": done,
                          "ticker": r.ticker, "as_of": str(pd.Timestamp(r.filed).date()),
                          "table": _pct_table(s, snaps).to_string(),
                          "fwd_excess_%": round((ret - grp_mean) * 100, 3)})
        done += 1
        if done % 10 == 0:
            print(f"[matched] {done}/{n_batches} batches", file=_sys.stderr)

    out = CACHE / "blind_cases_matched.json"
    out.write_text(json.dumps(cases, indent=1))
    d = pd.DataFrame(cases)
    print(f"[matched] {len(cases)} cases in {d.batch_id.nunique()} batches · "
          f"{d.ticker.nunique()} tickers", file=_sys.stderr)
    print(f"[matched] peer-relative excess sd {d['fwd_excess_%'].std():.2f} "
          f"(unmatched design was ~28)", file=_sys.stderr)


if __name__ == "__main__":
    _n = lambda f, d: int(sys.argv[sys.argv.index(f) + 1]) if f in sys.argv else d
    if "--matched" in sys.argv:
        build_matched(_n("--batches", 40))
    else:
        build(_n("--n", 300))
