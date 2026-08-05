---
name: portfolio-brainstorm
description: Conduct a deep, evidence-first portfolio review and discussion. Reads the latest Wyckoff entry/exit digests, prior brainstorm records and conversation history, then argues asset-by-asset — why hold, why ditch, and when — challenging both the engine's verdicts and the user's assumptions.
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [finance, trading, analysis, discussion]
---

# Portfolio Brainstorm

A **manually triggered** deep review. Roy runs the Wyckoff entry and exit jobs first, then invokes this
to think about what they mean. It is not a digest — the digests already exist. This is the conversation
*about* them: what to hold, what to ditch, when, and what the mechanism cannot see.

Triggered by: "let's review the portfolio", "brainstorm the portfolio", "portfolio discussion",
"בוא נעבור על התיק", "let's go over my holdings".

**Read `~/.hermes/skills/wyckoff/docs/portfolio-review-method.md` before starting.** It carries the
analytical lenses (artifact vs signal, sector clusters, instrument character, the FX lens, what the
engine structurally cannot see) and the discussion protocol. This file is the *process*; that file is
the *judgement*. Also load `README.md` and `DESIGN.md` from the wyckoff skill for mechanism detail.

---

## Step 1 — Gather the evidence (always first)

```bash
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/review_context.py --days 30 --reviews 2
```

Deterministic, no LLM, no Telegram. It prints, in reading order:

1. **The latest archived entry/exit digests** — the reports this review is about
2. **Prior brainstorm records** — so you build on the last session instead of relitigating it
3. **Recent conversation** — where intent lives: deferred decisions, strategic overrides, what was *meant*
4. **Job health** — a crashed job and a job with nothing to say look identical from outside
5. **Current holdings, watchlist, parked list**

If section 1 is empty, the jobs were run with `--dry-run` (which never reaches the notifier) or have not
run recently. **Say so and ask whether to run them** rather than reviewing stale data. A fresh run:

```bash
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/exit.py  --section all --dry-run
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/entry.py --cohort 40 --dry-run
```

Both are slow (LLM-bound, tens of minutes). Launch detached, tell Roy they're running, and work on
everything else meanwhile. Use `--dry-run` in a review — a bare run duplicates his scheduled digest.

## Step 2 — Fill the gaps

The digests are price/volume only. Before forming a view:

- `explain.py TICKER` — deterministic per-name breakdown (score, stop maths, ladder reasoning,
  structure, catalysts). Use it wherever a digest line is ambiguous or surprising. No LLM, no Telegram.
- **Business and macro context** — search for it. Earnings dates and results, regulatory catalysts,
  guidance changes, sector news, rate policy. **This routinely inverts a technical read**, and it is the
  single biggest source of value you add over the digest.
- **Ask for what the engine cannot see** — cash balance, holdings in other accounts, tax and currency
  constraints, time horizon. Every one of these has changed a conclusion before. Do not assume.

## Step 3 — Argue it asset by asset

For every holding: **hold or ditch, why, and when.** Name the reason class explicitly, because they fail
independently:

- **Technical** — what the engine sees: structure, score, stop, ladder verdict
- **Economic / business** — earnings, guidance, catalysts, sector and rate backdrop
- **Strategic** — role in the wider portfolio, horizon, currency, conviction

A technically clean position can be strategically broken (a dividend thesis where the payout exceeds
free cash flow). A technically ugly one can be an artifact (a distribution flag one day after earnings).

**Give every verdict a trigger** — a price, a date, or an event. Never "monitor closely".

Group by sector before ruling on any single name: a cluster outranks a per-name read.

## Step 4 — Challenge (this is the point of the exercise)

A review that only relays the engine is worthless — Roy can read the digest himself. Push on all four:

**Challenge the engine.** Where is the score an artifact? Check the earnings date before trusting any
distribution flag. Is the stop actually anchored, or absurdly wide? Does a HOLD reflect a ratchet already
at target rather than health? Does a "trim" resolve to selling zero shares?

**Challenge Roy's stated thesis.** If the reason to own something has quietly stopped being true, say so
even when the chart is clean. If a position is too small to matter, say that too — a deeply underwater
sub-1% holding is emotionally loud and financially trivial, while the comfortable oversized position is
the actual risk.

**Challenge your own prior session.** Prior records are context, not scripture. If new evidence overturns
a previous call, say plainly that it does. When Roy's read beats yours, say so — and record it.

**Challenge the silence.** Repeated "0 STRONG" weeks are the most misread output in the system. Diagnose
*which gate binds* (prescreen starvation vs no confirmed SOS/LPS) before concluding the market is thin or
the screen is broken. Do not "fix" a screen that is reporting the truth.

## Step 5 — Opportunities, at a high bar

Propose new ideas **only when they clear a high bar.** The default is to propose nothing. A proposal
qualifies only if it is concrete (instrument, level, invalidation), supported by evidence in hand, and
material relative to the portfolio. Volume of ideas is not value.

Legitimate sources: STRONG/MARKUP-PULLBACK picks from the entry run; watchlist names that reached their
levels; sector clusters visible in the prescreen; a structural mismatch between a goal and its instrument;
idle cash with no productive home.

State the counter-case for every proposal. If nothing clears the bar, say "nothing this week" — that is a
respectable and frequent outcome in a tape with no risk premium.

## Step 6 — Write the session record

Every review ends by writing `~/.hermes/skills/wyckoff/data/reviews/YYYY-MM-DD.md`
(**gitignored — it holds positions**). Capture:

- Regime at the time (index, rates, risk premium, breadth)
- Every decision with its **reason class**, and the numbers behind it
- **Explicit strategic intent with its horizon** — the "not now, revisit in N years" calls that no
  mechanism will otherwise remember
- What was missed vs. correctly skipped, with the reason
- Where your read was wrong and Roy's was right
- Open questions and anything deferred

The next session reads this first. Most of the value is in the deltas and in not relitigating settled
decisions.

## Guardrails

- **This repo is public. Never commit positions, quantities, costs or the security-number map.** Session
  records and reports live under `wyckoff/data/` which is gitignored. Keep it that way.
- **Do not post to Telegram.** This is a conversation, not a digest. Use `--dry-run`, and note that
  `watchlist_scan.py`, `parked_scan.py`, `stop_check.py`, `bond_review.py`, `price_alerts.py` and
  `portfolio_value.py` have **no preview flag and send on a bare run**.
- **Reconcile holdings first if a broker export is offered** — `import_holdings.py` dry-run → show the
  diff → `--apply` on confirmation. Every downstream number depends on it.
- **Distinguish a placed order from a fill.** Reconcile on the next export; never assume execution.
- **Watchlist and config edits are runtime-only** — never commit `config.yaml`.
- Mirror Roy's language; he switches between English and Hebrew.
