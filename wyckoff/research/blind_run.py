#!/usr/bin/env python3
"""Run the blinded fundamental experiment and score it.

Three arms:
  main    — the blinded fundamentals table. The thing being tested.
  shell   — identical prompt with the TABLE REMOVED. If this scores above chance the model is
            recognising something the blinding failed to hide, and `main` is discounted by it.
  shuffle — scored, not called: `main` predictions paired with permuted outcomes. Must land at
            chance; if it does not, the scoring harness itself leaks.

Anti-sycophancy measures, because a model asked "is this good?" will always find a story:
  * it never sees the ticker, the date, the price, or whether we own it — position knowledge is
    the single strongest trigger for justification-instead-of-analysis
  * the BEAR case is demanded first and separately, before anything favourable
  * the verdict is a number plus NAMED FALSIFIABLE CLAIMS, so both the reasoning and the call
    can be scored, not just the outcome
  * stated confidence is logged so calibration is measurable — whether 0.6 calls are right 60%
    of the time is the only test of whether the confidence means anything

Usage:  blind_run.py --arm main [--n 300] [--workers 4]
        blind_run.py --score
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

CACHE = Path(__file__).parent / "cache"
CASES = CACHE / "blind_cases.json"

PROMPT = """You are assessing an anonymised company from its reported fundamentals alone.

You are NOT told the company, the industry, the dates, the share price, or whether anyone holds
it. This is deliberate. Do not speculate about identity — if you name a company or a sector you
have failed the task.

{body}

Answer in this exact order.

1. BEAR CASE. What is deteriorating or fragile here? Be specific and quantitative. Write this
   FIRST and write it properly, before considering anything positive.
2. BULL CASE. What is genuinely improving or durable?
3. CLAIMS. Two to four falsifiable statements about the NEXT four quarters — each must be
   checkable against a future filing (e.g. "operating margin stays above 12%").
4. VERDICT.

End with exactly one line of JSON and nothing after it:
{{"score": <0-100, where 50 = no view and higher = expected to outperform peers over 6 months>,
 "confidence": <0.0-1.0>, "direction": "<over|under|neutral>"}}

"no view" is a correct and expected answer. A score near 50 with low confidence is much better
than a confident number you cannot justify from the data shown."""

WITH_TABLE = """Twelve quarters of reported fundamentals, oldest first. All figures are margins,
growth rates or ratios — absolute size has been removed. Q-0 is the most recent reported quarter.

{table}"""

NO_TABLE = """No fundamental data is available for this company."""


def call_claude(prompt: str, timeout: int = 180) -> str:
    try:
        r = subprocess.run(["claude", "-p"], input=prompt, capture_output=True,
                           text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception as e:
        return f"__ERROR__ {str(e)[:100]}"


def parse(txt: str) -> dict | None:
    m = re.findall(r'\{[^{}]*"score"[^{}]*\}', txt, re.S)
    if not m:
        return None
    try:
        d = json.loads(m[-1])
        return {"score": float(d["score"]), "confidence": float(d.get("confidence", np.nan)),
                "direction": str(d.get("direction", "")), "raw_len": len(txt)}
    except Exception:
        return None


def run_arm(arm: str, n: int, workers: int) -> None:
    cases = json.load(open(CASES))[:n]
    body = (lambda c: WITH_TABLE.format(table=c["table"])) if arm == "main" else (lambda c: NO_TABLE)

    def one(c):
        out = call_claude(PROMPT.format(body=body(c)))
        p = parse(out)
        rec = {"case_id": c["case_id"], "arm": arm, "fwd_excess_%": c["fwd_excess_%"]}
        rec.update(p or {"score": np.nan, "confidence": np.nan, "direction": "PARSE_FAIL"})
        return rec

    with ThreadPoolExecutor(max_workers=workers) as ex:
        res = []
        for i, r in enumerate(ex.map(one, cases), 1):
            res.append(r)
            if i % 20 == 0:
                print(f"[{arm}] {i}/{len(cases)}", file=sys.stderr)
    df = pd.DataFrame(res)
    df.to_pickle(CACHE / f"blind_{arm}.pkl")
    ok = df.score.notna().sum()
    print(f"[{arm}] {ok}/{len(df)} parsed · score mean {df.score.mean():.1f} "
          f"sd {df.score.std():.1f}", file=sys.stderr)


def score() -> None:
    pd.set_option("display.width", 200)
    print(f"\n{'arm':<10}{'n':>5}{'rank corr':>11}{'t':>8}{'p':>9}{'top3-bot3':>11}{'mean conf':>11}")
    for arm in ("main", "shell"):
        f = CACHE / f"blind_{arm}.pkl"
        if not f.exists():
            continue
        d = pd.read_pickle(f).dropna(subset=["score"])
        if len(d) < 20:
            continue
        rho, p = stats.spearmanr(d.score, d["fwd_excess_%"])
        t = rho * np.sqrt((len(d) - 2) / max(1 - rho ** 2, 1e-9))
        q = pd.qcut(d.score.rank(method="first"), 3, labels=["bot", "mid", "top"])
        spread = d[q == "top"]["fwd_excess_%"].mean() - d[q == "bot"]["fwd_excess_%"].mean()
        print(f"{arm:<10}{len(d):>5}{rho:>11.3f}{t:>8.2f}{p:>9.3f}{spread:>10.2f}pp"
              f"{d.confidence.mean():>11.2f}")

    f = CACHE / "blind_main.pkl"
    if f.exists():
        d = pd.read_pickle(f).dropna(subset=["score"])
        rng = np.random.default_rng(7)
        rhos = [stats.spearmanr(d.score, rng.permutation(d["fwd_excess_%"]))[0] for _ in range(400)]
        print(f"\nshuffle control: rank corr {np.mean(rhos):+.3f} "
              f"(95% band {np.percentile(rhos,2.5):+.3f}..{np.percentile(rhos,97.5):+.3f}) "
              f"— the real result must sit OUTSIDE this band to mean anything")
        print("\n=== calibration: are confident calls actually better? ===")
        d["hit"] = ((d.score > 50) == (d["fwd_excess_%"] > 0))
        for lo, hi in [(0, .4), (.4, .6), (.6, .8), (.8, 1.01)]:
            s = d[(d.confidence >= lo) & (d.confidence < hi)]
            if len(s) >= 15:
                print(f"  confidence {lo:.1f}-{hi:.1f}: n={len(s):<4} hit rate {s.hit.mean()*100:5.1f}%")


# ---------------------------------------------------------------- batch ranking + ollama
# The single-case arm returned "no view" for almost everything (scores 41-50, sd 4.3): the
# anti-overconfidence prompting worked, but left nothing to rank. Asking for a RELATIVE ORDER
# within a small batch forces discrimination without inviting invented conviction — and an
# ordering is what the Spearman scoring wants anyway.

import os
import urllib.request

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

BATCH_PROMPT = """You are ranking {k} anonymised companies by their fundamentals alone.

You are NOT told the companies, industries, dates, or share prices. Do not guess identities.

Each table shows twelve quarters, oldest first. Every figure is a margin, growth rate or ratio -
absolute size has been removed. Q-0 is the most recent quarter.

{tables}

Rank all {k} from BEST to WORST expected performance RELATIVE TO EACH OTHER over the next six
months, judged only on what the fundamentals show: trajectory, margin direction, cash conversion,
balance-sheet strength, and whether growth is being bought with capital or earned.

You must produce a strict order - no ties. Ranking them is the task even if the differences are
small; you are ordering them, not claiming any is a good investment.

Give one short line per company saying what decided its place, then end with exactly one line of
JSON and nothing after it:
{{"order": [<company letters, best first>], "confidence": <0.0-1.0>}}"""


def call_ollama(prompt: str, model: str, timeout: int = 900) -> str:
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False,
                          "options": {"temperature": 0.0, "num_ctx": 16384}}).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/generate", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()).get("response", "").strip()
    except Exception as e:
        return f"__ERROR__ {str(e)[:120]}"


def run_batches(transport: str, model: str, n: int, k: int, workers: int, seed: int = 5) -> None:
    cases = json.load(open(CASES))[:n]
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(cases))
    batches = [[cases[i] for i in order[j:j + k]] for j in range(0, len(order) - k + 1, k)]
    letters = [chr(65 + i) for i in range(k)]

    def one(args):
        bi, batch = args
        tables = "\n\n".join(f"COMPANY {letters[i]}\n{c['table']}" for i, c in enumerate(batch))
        prompt = BATCH_PROMPT.format(k=len(batch), tables=tables)
        txt = (call_ollama(prompt, model) if transport == "ollama" else call_claude(prompt, 600))
        m = re.findall(r'\{[^{}]*"order"[^{}]*\}', txt, re.S)
        if not m:
            return None
        try:
            d = json.loads(m[-1])
            got = [str(x).strip().upper()[:1] for x in d["order"]]
            conf = float(d.get("confidence", np.nan))
        except Exception:
            return None
        if sorted(got) != sorted(letters[:len(batch)]):
            return None
        return [{"case_id": batch[letters.index(L)]["case_id"], "pred_rank": r + 1,
                 "fwd_excess_%": batch[letters.index(L)]["fwd_excess_%"],
                 "confidence": conf, "batch": bi} for r, L in enumerate(got)]

    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(one, list(enumerate(batches))), 1):
            if r:
                out.extend(r)
            if i % 5 == 0:
                print(f"[batch:{transport}] {i}/{len(batches)}", file=sys.stderr)
    df = pd.DataFrame(out)
    tag = f"batch_{transport}"
    df.to_pickle(CACHE / f"blind_{tag}.pkl")
    nb = df.batch.nunique() if len(df) else 0
    print(f"[{tag}] {nb}/{len(batches)} batches usable, {len(df)} cases", file=sys.stderr)
    if len(df) > 10:
        df["actual_rank"] = df.groupby("batch")["fwd_excess_%"].rank(ascending=False)
        rho, p = stats.spearmanr(df.pred_rank, df.actual_rank)
        t = rho * np.sqrt((len(df) - 2) / max(1 - rho ** 2, 1e-9))
        top = df[df.pred_rank == 1]["fwd_excess_%"].mean()
        bot = df[df.pred_rank == df.groupby("batch")["pred_rank"].transform("max")]["fwd_excess_%"].mean()
        print(f"[{tag}] rank corr {rho:+.3f}  t={t:+.2f}  p={p:.3f}", file=sys.stderr)
        print(f"[{tag}] picked-best {top:+.2f}%  picked-worst {bot:+.2f}%  "
              f"spread {top-bot:+.2f}pp", file=sys.stderr)


def _argv(flag, default):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


if __name__ == "__main__":
    if "--batch" in sys.argv:
        run_batches(transport=_argv("--transport", "ollama"),
                    model=_argv("--model", "qwen3.6:27b"),
                    n=int(_argv("--n", "60")), k=int(_argv("--batch", "5")),
                    workers=int(_argv("--workers", "2")))
    elif "--score" in sys.argv:
        score()
    else:
        arm = sys.argv[sys.argv.index("--arm") + 1] if "--arm" in sys.argv else "main"
        n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 300
        w = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 4
        run_arm(arm, n, w)
