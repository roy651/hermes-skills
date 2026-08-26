#!/usr/bin/env python3
"""Fetch periodic filings and reduce each to the vectors two studies need.

Serves both in ONE pass, because the download is the expensive part:

  * "Lazy Prices" (Cohen, Malloy & Nguyen, JF 2020) — year-on-year CHANGE in filing language
    predicts returns; firms that rewrite their filings subsequently underperform. Needs a term
    vector per filing so consecutive same-quarter filings can be compared.
  * Loughran-McDonald — finance-specific sentiment. The general-English lexicons are wrong here:
    "liability", "tax" and "cost" are not negative words in a 10-Q, and LM was built to fix that.

Raw HTML is ~270KB per filing and we want tens of thousands, so nothing is stored: each document
is fetched, stripped, counted, and discarded. What lands on disk is a term-count dict plus the
sentiment tallies — a few KB per filing instead of a quarter of a megabyte.

Usage:  filings.py --build [--companies 400] [--quarters 32]
"""
from __future__ import annotations

import json
import pickle
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))
import edgar
import fundamentals as F

CACHE = Path(__file__).parent / "cache"
OUT = CACHE / "filing_vectors.pkl"
TOP_TERMS = 800                     # enough for a stable cosine, small enough to store

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_TOK = re.compile(r"[a-z]{3,}")
# Boilerplate that dominates every filing and swamps the signal in a cosine comparison.
STOP = set("""the and for that with this which are was were has have had been from will not you
our its their they them there here also may can such other than then upon into under over more
most any all each per its his her been being about above below after before during under while
company companies inc corp corporation form quarterly annual report period ended december march
june september three six nine months year years quarter quarters ended""".split())


def lm_lexicon() -> dict[str, set]:
    """Loughran-McDonald word lists, from the master dictionary bundled with pysentiment2.

    The general-English sentiment lexicons are actively wrong on filings: "liability", "tax",
    "cost" and "capital" are neutral accounting terms, not negativity. LM was built specifically
    to fix that, and every credible filing-sentiment study uses it.

    Every public mirror of the CSV was dead (four 404s), so it comes from the installed package
    rather than a URL. In the master dictionary a nonzero value in a category column is the YEAR
    the word entered that list, so nonzero means membership.
    """
    f = CACHE / "lm_lexicon.json"
    if f.exists():
        return {k: set(v) for k, v in json.loads(f.read_text()).items()}
    import csv
    import importlib.util
    spec = importlib.util.find_spec("pysentiment2")
    if not spec:
        print("[filings] pysentiment2 missing — sentiment disabled", file=sys.stderr)
        return {}
    path = Path(spec.origin).parent / "static" / "LM.csv"
    cats = ["Negative", "Positive", "Uncertainty", "Litigious", "Constraining"]
    lex: dict[str, set] = {c.lower(): set() for c in cats}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            w = row["Word"].strip().lower()
            for c in cats:
                try:
                    if float(row.get(c) or 0) != 0:
                        lex[c.lower()].add(w)
                except ValueError:
                    pass
    f.write_text(json.dumps({k: sorted(v) for k, v in lex.items()}))
    print(f"[filings] LM lexicon: { {k: len(v) for k, v in lex.items()} }", file=sys.stderr)
    return lex


def filing_index(cik: str) -> list[dict]:
    subs = json.loads(edgar._get(f"https://data.sec.gov/submissions/CIK{cik}.json"))
    r = subs["filings"]["recent"]
    out = []
    for i in range(len(r["form"])):
        if r["form"][i] not in ("10-Q", "10-K") or not r["primaryDocument"][i]:
            continue
        out.append({"form": r["form"][i], "filed": r["filingDate"][i],
                    "acc": r["accessionNumber"][i].replace("-", ""),
                    "doc": r["primaryDocument"][i]})
    return out


def vectorise(cik: str, f: dict, lex: dict) -> dict | None:
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{f['acc']}/{f['doc']}"
    try:
        html = edgar._get(url)
    except Exception:
        return None
    text = _WS.sub(" ", _TAG.sub(" ", html)).lower()
    toks = [t for t in _TOK.findall(text) if t not in STOP]
    if len(toks) < 2000:                       # a stub or an exhibit, not a real filing
        return None
    c = Counter(toks)
    rec = {"form": f["form"], "filed": f["filed"], "n_words": len(toks),
           "terms": dict(c.most_common(TOP_TERMS))}
    for tag, words in lex.items():
        rec[f"lm_{tag}"] = sum(v for w, v in c.items() if w in words) / len(toks) * 100
    return rec


def build(n_companies: int, n_quarters: int) -> None:
    panel = pickle.load(open(CACHE / "panel.pkl", "rb"))
    us = sorted(t for t, d in panel.items()
                if d is not None and "." not in t and not t.startswith("^") and len(d) > 900)
    cm = F.cik_map()
    lex = lm_lexicon()
    store = json.loads(OUT.with_suffix(".progress.json").read_text()) if \
        OUT.with_suffix(".progress.json").exists() else {}

    done = 0
    for t in us[:n_companies]:
        if t in store:
            continue
        cik = cm.get(t.upper().replace(".", "-"))
        if not cik:
            continue
        try:
            idx = filing_index(cik)[:n_quarters]
        except Exception:
            continue
        recs = [v for v in (vectorise(cik, f, lex) for f in idx) if v]
        if len(recs) >= 8:
            store[t] = recs
        done += 1
        if done % 10 == 0:
            OUT.with_suffix(".progress.json").write_text(json.dumps(store))
            tot = sum(len(v) for v in store.values())
            print(f"[filings] {done} companies · {len(store)} stored · {tot:,} filings",
                  file=sys.stderr)
    pickle.dump(store, open(OUT, "wb"))
    tot = sum(len(v) for v in store.values())
    print(f"[filings] DONE — {len(store)} companies, {tot:,} filings", file=sys.stderr)


if __name__ == "__main__":
    a = lambda f, d: int(sys.argv[sys.argv.index(f) + 1]) if f in sys.argv else d
    build(a("--companies", 400), a("--quarters", 32))
