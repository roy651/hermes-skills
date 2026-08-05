# Portfolio Review — method, lenses & findings

Companion to `README.md` (what the pipelines do) and `DESIGN.md` (why the exit engine is shaped as it
is). This one captures **how to conduct a full portfolio review conversation** — the evidence chain, the
analytical lenses, and the system-reliability lessons that make the output trustworthy.

Written after the 2026-08-05 review. **Deliberately free of positions, quantities and costs** — this repo
is public. Per-session records with actual holdings live runtime-only in `data/reviews/` (gitignored).

---

## 1. The evidence chain — gather before opining

A review that opens with opinions is worthless. Work the chain in order; each step can invalidate the next.

1. **Reconcile the portfolio first.** Import the broker export (`import_holdings.py`, dry-run → confirm →
   `--apply`). Every downstream number depends on holdings being exact.
2. **Read the conversation history.** Hermes transcripts live in `~/.hermes/state.db`, table `messages`
   (`timestamp` is a unix float, sessions prefixed `cron_` are jobs, not conversation). This is where the
   user's *intent* lives — what they meant to do, what they deferred, which positions are strategic vs
   tactical vs impulse. The engine cannot infer any of that.
3. **Read the job logs** (`logs/*.log`). They reveal whether the pipeline actually ran. A silent pipeline
   and a pipeline reporting "nothing found" look identical from the user's side.
4. **Run the jobs fresh** — `exit.py --section all --dry-run`, `entry.py --cohort N --dry-run`. Always
   `--dry-run` in a review, or you duplicate the user's scheduled Telegram digest.
5. **Per-name engine breakdown** — `explain.py TICKER` is deterministic, no LLM, no Telegram. Use it for
   any name where the digest line is ambiguous.
6. **Business & macro context** — the engine reads price/volume only. Earnings, catalysts, regulatory
   dates and rate policy come from outside and routinely *invert* a technical read.

## 2. Analytical lenses

`DESIGN.md` establishes the core lens (artifact vs signal, structural top vs bleed, still-falling vs
basing, let the stop arbitrate, zoom out to the portfolio). These extend it.

### 2.1 Artifact vs signal — the catalogue
A distribution score is only as good as the bars underneath it. Known artifact generators:

- **Ex-dividend drops** on high-yield names — a price gap with no supply behind it.
- **Index rebalances** on index-tracking funds.
- **Earnings gaps.** Check the report date *before* trusting a distribution flag. A structural top printed
  one day after a print is contaminated by definition. (2026-08-05: a trim was correctly overridden this
  way — the flags were one day old, and the company had *raised* full-year guidance.)
- **Reverse splits / share-count changes** — distort volume comparisons even when price is adjusted.
- **Money-market and T-bill ETFs** — these accrue NAV daily and drop it on a monthly distribution, giving
  a permanent sawtooth. To a Wyckoff detector that reads as recurring support breaks. Never put a
  cash-like instrument in the Wyckoff book; tag it `asset_class: cash` so `no_trailing_stop()` exempts it.

**Rule:** before acting on a structural flag, ask what happened on that bar.

### 2.2 The sector cluster outranks the single-name read
When several holdings in one sector flag together, the cluster is the signal — and it can *overturn* a
per-name artifact dismissal. A lone index-fund UTAD looks like a rebalance artifact; the same UTAD
alongside two single-name holdings in the same sector breaking down is a sector call.
**Always group holdings by sector before ruling on any one of them.**

### 2.3 Instrument character decides the tool
Extends the `README.md` bond-sleeve rule into three classes:

| Class | Example | Tool |
|---|---|---|
| Supply/demand-driven | equities, sector & commodity ETFs | full Wyckoff + trailing stop |
| Formula-driven | bond & Treasury ETFs, buffered, leveraged | thesis review; **no** trailing stop |
| Cash-like | T-bill ETFs, money-market funds | no analysis at all; exempt |

### 2.4 A perpetual bond ETF never pulls to par
The distinction that matters most for a "safe yield" allocation. An **individual bond held to maturity**
locks its yield — price swings become irrelevant. A **bond ETF perpetually rolls** its holdings, never
matures, and therefore never delivers the yield you thought you bought; it only delivers price plus
distributions at prevailing rates. **Defined-maturity funds** (iBonds, BulletShares) are the ETF-shaped
fix — they terminate on a stated date and return capital.

Corollary, learned the hard way: **verify a ticker is still the instrument you think it is.** When a
defined-maturity fund terminates, its ticker can be reassigned to an unrelated company later.

### 2.5 The FX lens
For an investor whose liabilities are in one currency and whose assets are in another, a nominal yield
comparison is incomplete. A "risk-free" foreign government bond carries no *credit* risk and full
*currency* risk. Compare the yield **premium** against plausible FX moves: a small annual premium is
erased by a couple of percent of currency appreciation. Where the two central banks are moving in
*opposite* directions, this dominates the duration question.

### 2.6 Two things the engine structurally cannot see
- **Cash.** `% of port` is computed from holdings only. If the account holds significant cash, every
  concentration figure is overstated. Always ask for the cash balance before calling a position oversized.
- **Other accounts.** A holding that looks small here may be large in aggregate. Ask.

Both change conclusions, and neither is inferable from the data the pipeline has.

### 2.7 Regime lens
Read the tape before reading the names: index drawdown, rate direction (policy *and* long end), equity
risk premium, market breadth. A compressed risk premium is a **condition, not a catalyst** — it argues
for selectivity and exit discipline, never for market timing.

## 3. Reading the entry funnel's silence

Repeated "0 STRONG" weeks are the pipeline's most commonly *misread* output.

**Diagnose the binding constraint before concluding anything.** STRONG requires three gates: an
actionable recommendation, criteria ≥ 7, **and** a programmatically confirmed SOS/LPS. Check which one
is binding:

- Prescreen starving (few candidates) → a *screen* problem; consider cohort size or universe sleeves.
- Plenty of candidates but no confirmed event → a *market* fact. Structure-poor names pass a quant
  screen; confirmed accumulation events are what a melt-up tape genuinely lacks.

The off-high floor is **one of five scored criteria, not a gate** — names near their highs can and do
clear the accumulation lane on the other four. Do not "fix" a screen that is reporting the truth. The
useful response to a thin tape is a *wider cohort*, which surfaces marginal-but-real setups the default
cohort truncates away.

## 4. System reliability lessons

- **Normalise LLM output at one boundary.** A model may return a declared-scalar field as an object or a
  single-item list. One malformed field killed an entire weekly run at the final gating step. Flatten
  where LLM output enters the pipeline so a bad field degrades one name, never the run.
- **Degradation detection must count cached tokens.** Picking the "real" model by uncached input tokens
  misidentifies it as soon as prompt caching warms up — the working model reports near-zero uncached
  input and loses to a tiny housekeeping call. Sum input + cache-read + cache-creation. A silent
  mislabel is worse than no label: it blinds the very banner meant to warn of real degradation.
- **A crashing scheduled job is invisible.** It writes a stack trace to a log nobody reads and sends
  nothing. Any job whose *only* output is a notification needs failure alerting.
- **Never restart shared infrastructure mid-run.** Deploy the fix, defer the restart.
- **Keep a deterministic fallback.** Under quota pressure, the no-LLM prescreen output is what survives.
  Persist it before the expensive stage, not after.

## 5. Discussion protocol

- Lead with what changes a decision; leave confirmations for later.
- Name the **reason class** for every verdict — technical / economic / strategic. They fail independently:
  a technically clean position can be strategically broken (a dividend thesis where the payout exceeds
  free cash flow), and a technically ugly one can be an artifact.
- Give every verdict a **trigger**: a price, a date, or an event — not "monitor closely".
- Distinguish **position noise from real risk.** A deeply underwater sub-1% position is emotionally loud
  and financially trivial; the oversized comfortable position is the actual risk. Say so.
- **Separate the asset from the position size.** "Good asset, wrong weight" is a different conversation
  from "bad asset".
- Respect explicit strategic overrides — external exposure, tax, currency, time horizon. When the user
  supplies context the engine cannot see, that context wins.
- When the user's read beats the engine's, say so plainly and revise.
- Flag anything that is a *placed order* rather than a *fill*; reconcile on the next export.

## 6. Session records

Each review writes a dated record to `data/reviews/YYYY-MM-DD.md` (gitignored — it contains positions).
A record should capture: decisions taken and their reason class, explicit strategic intent with its
horizon, open questions, and anything deferred. Load prior records before a new review — most of the
value is in the *deltas* and in not relitigating settled decisions.
