#!/usr/bin/env python3
"""Sync data/holdings.json from a broker "online balances" (יתרות מקוונות) .xlsx export.

Deterministic: each row is matched by its STABLE Israeli security-number (מספר נייר) to the system
ticker, then quantity + average cost are read off fixed columns. The LLM never parses the sheet — it
only orchestrates: run a dry-run, show the diff, and re-run with --apply on the user's OK.

    python import_holdings.py PORTFOLIO.xlsx            # dry-run: print the diff, write nothing
    python import_holdings.py PORTFOLIO.xlsx --apply    # back up + write holdings.json

Notes:
- .TA names carry price/cost in agorot, which is exactly how holdings.json already stores them — so
  the cost is copied through verbatim (no ×100). Currency is derived from the .TA suffix elsewhere.
- A holding present in holdings.json but absent from the file is KEPT (never silently deleted) and
  reported — a sold-out position is removed by hand, not by a missing row.
- The risk-state baseline (scale-out ratchet) lives in a separate file and is deliberately untouched,
  so a trimmed position keeps showing as partially scaled-out.
"""
from __future__ import annotations
import argparse
import json
import shutil
import sys
from pathlib import Path

import openpyxl

HOLDINGS = Path(__file__).parent.parent / "data" / "holdings.json"

# Stable map: Israeli security-number (מספר נייר, never changes) -> system ticker. This reveals which
# securities are held, so it's PII — it lives in data/secnum_map.json (gitignored), NOT in this public
# file. See data/secnum_map.example.json for the format; add a line per newly-bought security. A missing
# map → every row is "unmapped" and reported (and --apply is refused), never silently mis-imported.
_SECNUM_MAP_PATH = Path(__file__).parent.parent / "data" / "secnum_map.json"
try:
    SECNUM_TO_TICKER = json.loads(_SECNUM_MAP_PATH.read_text())
except FileNotFoundError:
    SECNUM_TO_TICKER = {}

# Broker export column order (0-based), under a header row containing HEADER_KEY.
COL_NAME, COL_SECNUM, COL_QTY, COL_COST = 0, 1, 3, 8
HEADER_KEY = "מספר נייר"
COST_EPS = 0.5          # only surface cost changes above this (sub-rounding drift is noise)


def _secnum(v) -> str:
    return str(int(v)) if isinstance(v, float) else str(v).strip()


def _qty(v):
    return int(v) if isinstance(v, (int, float)) and float(v).is_integer() else v


def parse_xlsx(path: str) -> tuple[dict, list]:
    """Return ({ticker: {qty, avg_cost}}, [(secnum, name), ...] unmapped) from the broker sheet."""
    wb = openpyxl.load_workbook(path, data_only=True)
    rows = list(wb.worksheets[0].iter_rows(values_only=True))
    header_i = next((i for i, r in enumerate(rows)
                     if any(c and HEADER_KEY in str(c) for c in r)), None)
    if header_i is None:
        sys.exit(f"error: header row with '{HEADER_KEY}' not found — is this the balances export?")

    parsed, unmapped, closed = {}, [], []
    for r in rows[header_i + 1:]:
        if r[COL_SECNUM] is None or r[COL_NAME] is None:
            continue
        secnum = _secnum(r[COL_SECNUM])
        ticker = SECNUM_TO_TICKER.get(secnum)
        if not ticker:
            unmapped.append((secnum, str(r[COL_NAME])))
            continue
        qty = _qty(r[COL_QTY])
        # The broker keeps listing a position it sold today, at qty 0 and avg_cost 0. Importing
        # that as a holding gives every downstream job a position we do not own — stop_check would
        # compute a stop for it and could alert on it — and the 0 cost would wipe the real basis.
        if not qty:
            closed.append(ticker)
            continue
        parsed[ticker] = {"qty": qty, "avg_cost": round(float(r[COL_COST]), 2)}
    return parsed, unmapped, closed


def diff_lines(current: dict, new: dict) -> list[str]:
    out = []
    for t in sorted(set(current) | set(new)):
        c, n = current.get(t), new.get(t)
        if n is None:
            out.append(f"  {t}: in holdings, NOT in file — qty {c['qty']} (sold out? kept as-is)")
        elif c is None:
            out.append(f"  {t}: NEW — qty {n['qty']} @ {n['avg_cost']}")
        elif c["qty"] != n["qty"] or abs(c["avg_cost"] - n["avg_cost"]) >= COST_EPS:
            out.append(f"  {t}: qty {c['qty']}->{n['qty']}  cost {c['avg_cost']}->{n['avg_cost']}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--apply", action="store_true", help="write holdings.json (default: dry-run)")
    args = ap.parse_args()

    current = json.loads(HOLDINGS.read_text())
    parsed, unmapped, closed = parse_xlsx(args.xlsx)

    # Keep holdings absent from the file; for those present, update qty/avg_cost from the sheet but
    # PRESERVE any hand-set metadata (e.g. "asset_class": "bond", "no_trailing_stop") — the broker
    # export carries only qty+cost, so a blind ``update`` would wipe those flags on every re-import
    # (that silently un-exempted XFIV from the trailing stop and mis-fired a breach).
    merged = dict(current)
    for ticker, fields in parsed.items():
        merged[ticker] = {**current.get(ticker, {}), **fields}

    changes = diff_lines(current, merged)
    print(f"[import] {len(parsed)} positions parsed · {len(changes)} change(s):")
    print("\n".join(changes) if changes else "  (none — already in sync)")
    if closed:
        still_held = [t for t in closed if t in current]
        print(f"[import] {len(closed)} zero-quantity row(s) skipped: {', '.join(sorted(closed))}")
        if still_held:
            # Deleting is deliberately left to a person, same as the absent-from-file case.
            print(f"[import] ⚠️  sold out at the broker but STILL in holdings.json: "
                  f"{', '.join(sorted(still_held))} — remove by hand")
    if unmapped:
        print("[import] UNMAPPED rows (add to SECNUM_TO_TICKER, then re-run):")
        for s, n in unmapped:
            print(f"  secnum {s}  {n}")

    if not args.apply:
        print("[import] dry-run — nothing written. Re-run with --apply to save.")
        return
    if unmapped:
        sys.exit("[import] refusing to --apply with unmapped rows — map them first.")

    backup = HOLDINGS.with_suffix(".json.bak-import")
    shutil.copy(HOLDINGS, backup)
    HOLDINGS.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
    print(f"[import] applied → {HOLDINGS}  (backup: {backup.name})")


if __name__ == "__main__":
    main()
