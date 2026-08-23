# Strategy backtest spec

Written before any backtest code, deliberately. The purpose is to fix the rules **now**, so
that what comes back is a measurement rather than a search. Everything measured so far
(`signal-validation.md`) is a *signal* study — one date, one forward return, averaged
cross-sectionally. That says a signal has an edge. It does not say a portfolio built on it
makes money after costs, and those are different claims.

Companion to `signal-validation.md` (the evidence) and `../research/README.md` (how to run
the bench). Code lands in `../research/`.

---

## 0. The binding constraints, stated up front

| Constraint | Current state | What this spec does about it |
|---|---|---|
| **37 out-of-sample dates** | `observations.py` starts 2021-01-01; the panel holds 2016-08-19 onward | Widen to 2016 → ~119 dates, ~95 out-of-sample. Free, no refetch. |
| **Survivorship** | Universe is *current* index membership | Cannot be removed without point-in-time constituents. But it can be **measured** — see §1.3. |
| **The holdout is spent** | 2025-2026 was inspected while writing §10 | Widening supplies a genuinely unseen era (2018-2020). Treated as the new validation sample. |
| **Multiple testing** | 100+ configurations are reachable | One config is **pre-registered as primary** (§4). Everything else is labelled sensitivity and reported as such. |
| **Costs never modelled** | Every number to date is uncosted | Costs are a first-class axis, not a footnote (§3.6). |

---

## 1. Sample

### 1.1 Window
`START = 2016-09-01`, `END = 2026-02-01` (six months of forward return must exist).
Month-end observation dates from the most complete US series: **~119 dates**, of which ~95 are
out-of-sample after `MIN_TRAIN_DATES = 18` and `EMBARGO_DATES = 6`.

### 1.2 Two different frequencies, and they must not be confused
- **Observation dates — the labelled data the model trains on — stay monthly.** Sampling
  semi-monthly would double the row count and add almost nothing: adjacent dates share ~95% of
  their cross-section and their forward windows overlap heavily. Under Fama-MacBeth the
  effective sample grows with **calendar span**, not with sampling rate.
- **Rebalance cadence is a separate axis and is swept** (§2.6). A portfolio can be rebalanced
  weekly using a model trained on monthly labels, because the features are computable on any
  bar and the realised return comes from the price path, not from a fixed 126-day window.

The trap in mixing them: the embargo is currently written as *6 observation dates*, which is
six months only while dates are monthly. Under weekly rebalancing the same constant would be
six **weeks**, and the backtest would train on its own future. The embargo is therefore
re-expressed in calendar time, so it is cadence-invariant.

### 1.3 Survivorship — measured, not assumed away
The panel is built from *today's* index membership, so a 2018 observation is conditioned on
having survived to 2026. Two things make this tractable:

- The benchmark is the **same-date peer mean of the same panel**, so a uniform survivor lift
  cancels in the excess return. What survives is the *differential* — the concern that today's
  momentum winners are disproportionately the survivors.
- That differential has a signature: it should be **stronger the further back you go**. So the
  test is to report the edge separately for **2018-2020** and **2021-2026**. If the older era
  looks materially better, suspect survivorship. If it looks equal or worse, the bias is not
  driving the result.

This is a diagnostic, not a fix. A real fix needs point-in-time constituents and is Phase 0 of
the alpha-lab plan.

### 1.4 Currency: the portfolio backtest is US-only
The panel is multi-currency (US, `.L`, `.DE`, `.TA`, …). Cross-sectional *signal* studies are
safe because the peer benchmark is within-region and the FX term cancels. A **portfolio** is
not: you cannot add a 10% gain in EUR to a 10% gain in USD. The portfolio backtest therefore
runs **US-only, in USD**. The multi-region panel keeps its job in signal validation.
ILS translation is a reporting lens applied afterwards, never part of the ranking.

---

## 2. The strategy, as rules

### 2.1 Eligibility (all must hold at the rebalance date)
- In the panel, with ≥ 260 completed bars of history
- Close ≥ **$5**
- Bar date < today (completed bars only — the live-bar guard already in `mlm_scan.py`)
- Not `below_falling_200` (validated veto)
- Dollar-volume floor — **once §5.2 lands**; until then, unfiltered and noted as a gap

### 2.2 Primary trigger
`mom_12_1 > 30%` — the 12-month return excluding the most recent month.
The 30% threshold is itself a fitted number; it is swept at **20 / 30 / 40%** as sensitivity.

### 2.3 Ranking rules (the axis that matters most)
| ID | Rule | Why it is in the test |
|---|---|---|
| **R1** | `mom_12_1` descending — **no ML at all** | The control. If R2 does not beat it, the model is an expensive proxy for one number and should be dropped. |
| **R2** | Meta-probability descending | What ships today. |
| **R3** | Rolling 5-date mean of the R2 rank | Stability fix that may also improve the signal (§5.1). |

R1 is not a formality. The meta-model correlates **+0.304** with proximity to the high and
**+0.044** with momentum magnitude — a plausible outcome is that it adds nothing over a plain
sort, and this arm is what makes that finding available.

### 2.4 Portfolio size — N is a cap, M is a retention band
This is the mechanism that keeps the sleeve small. At every rebalance date, in this order:

1. **Rank** every eligible candidate by the active ranking rule.
2. **Exit** a held name if its rank has fallen outside **M**, or it no longer passes the
   primary gate, or (arm H3 only) it broke its stop.
3. **Fill** the free slots from the top of the ranking, skipping names already held.
4. **Short of candidates** → the remaining slots go to SPY.

So the portfolio **never holds more than N = 10**. M = 20 does *not* mean "hold 20" — it is
the retention band: a name already owned keeps its slot while it stays inside the top 20, and
is only replaced once it drops out. That is the entire turnover brake, and it is why the
answer to "do we always hold up to 10 or 20?" is **always up to 10, retained down to 20**.

M is swept at **10 / 15 / 20 / 30**, where M = 10 means no buffer at all — hold exactly today's
top ten and re-sort every period.

**The cost of the buffer, stated so it can be watched.** With M > N a held name at rank 14
blocks a fresh name at rank 11. That is deliberate — it is what stops the churn — but it means
the sleeve drifts from *the top ten* to *a good ten*. The backtest reports the **average rank
of held positions** against the ideal of 5.5, so the drift is a number rather than an
assumption.

**Concentration is knowingly uncapped for now.** Ten momentum names can easily be seven
semiconductors. Once sector lands (§5.2) a max-per-sector cap enters as a sensitivity arm;
until then the backtest reports realised sector concentration rather than controlling it.

### 2.5 Holding rules
| ID | Rule | Rationale |
|---|---|---|
| **H1** | Fixed hold of K months, no re-evaluation | Simplest possible. K ∈ {3, 6, 12}. |
| **H2** | Hold while ranked inside M (§2.4) | The default. The buffer band is the turnover brake. |
| **H3** | H2 plus the chandelier stop | Tests whether the validated stop helps *inside* a systematic sleeve. |

H1 and H2 differ in what they assume. H1 says the signal has a known half-life and you should
ride it out; H2 says the signal is refreshed continuously and you should follow it. The horizon
we validated is six months, so **H1 with K = 6 is the arm that matches the evidence** and H2 is
the one that matches how the live report already behaves.

### 2.6 Rebalance cadence
Swept at **weekly · bi-weekly · monthly · bi-monthly**. Predictions are generated on a grid of
every Friday plus every month-end, so all four cadences are subsets of one artifact and are
compared on identical predictions.

Prior expectation, recorded before the run so the result can contradict it: `mom_12_1` is a
twelve-month measure and moves slowly, so weekly rebalancing should mostly buy churn — more
trades, more cost, nearly the same holdings. If weekly wins materially, that is evidence the
edge is shorter-horizon than the signal study assumed, and it would need explaining before it
is believed.

### 2.7 Position sizing and re-entry
- **N = 10, equal weight.** Earlier work put the optimum near ten and showed N = 1 is
  statistically noise (t = 1.36).
- Meta-probability weighting is a sensitivity arm only. With probabilities in a 55-65 band it
  will barely differ from equal weight — expect a non-result, report it anyway.
- **Re-entry** into a just-exited name is permitted with no cooldown. If it re-ranks in, the
  rule says buy; a cooldown changes turnover and nothing else, so it is a sensitivity arm.

### 2.8 When fewer than N names qualify — the cash question
Unfilled slots go into **SPY**, not cash.

Two reasons, and this is a load-bearing choice. First, the earlier stop study proved the point
concretely: assuming cash after an exit made a neutral trailing stop look catastrophic
(−7.5pp), and crediting redeployment turned it to +0.15pp. Second, a momentum filter that
drops to cash in a drawdown is silently making a market-timing bet that has never been
validated here. The fraction of time this constraint binds is reported.

A cash arm is available as sensitivity — it is the honest way to see the timing bet priced.

## 3. Evaluation

### 3.1 Benchmarks
Both, always: **SPY** (what he would otherwise hold) and the **equal-weight US panel** (what
the signal study benchmarked against). A strategy that beats SPY only by being equal-weighted
has found the size premium, not an edge.

### 3.2 Metrics
CAGR · annualised vol · Sharpe · Sortino · max drawdown · annual one-way turnover · average
holding period · per-position hit rate · Fama-MacBeth *t* on monthly excess return vs SPY.

### 3.3 Per-year table
Mandatory. A strategy carried by one year is not a strategy. Six-of-ten positive years is the
same bar `promote.py` applies to detectors.

### 3.4 Era split
2018-2020 and 2021-2026 reported separately, per §1.3.

### 3.5 Regime split
Reuse `research/regime.py`. Momentum is regime-sensitive by construction and a strategy that
only works in one regime needs to say so on the tin.

### 3.6 Transaction costs
Base case **10bp round trip**; swept at **0 / 5 / 10 / 20 / 30bp**. At ~30% month-to-month
overlap the strategy trades roughly **84 times a year**, so costs are not a rounding error.
Modelling cost as a function of average dollar volume is a refinement for after §5.2 — flat
bps first, with the caveat that small caps and TASE names are worse than 10bp in reality.

### 3.7 Pre-registration
The primary configuration is declared in §4 **before the first run**. Every other cell is
reported as sensitivity, with the count of configurations examined stated explicitly, so the
best cell is never presented as if it were the only one tried.

---

## 4. The pre-registered primary configuration

> **R2** (meta rank) · **H2** (buffer band, M = 2N) · **N = 10** · equal weight · monthly
> rebalance · unfilled slots to SPY · **10bp** round trip · US-only.

It must clear all four to be considered live-worthy:

1. Beats SPY after 10bp costs, on Sharpe, over the full window
2. Beats **R1** (the no-ML control) after costs — otherwise ship R1 and delete the model
3. Positive in ≥ 6 of 10 calendar years
4. Positive in **both** eras (2018-2020 and 2021-2026)

Failing 2 is a good outcome, not a bad one: it produces a simpler strategy.

### 4.1 OUTCOME — recorded 2026-08-20, after the run

**The pre-registered configuration failed.** Results and full numbers in
`signal-validation.md` §11.

| criterion | result |
|---|---|
| 1. Beats SPY after costs on Sharpe | passed as tested (1.14 vs 0.84) — **fails** once the moonshot tail and 2020 are removed (0.30 vs 0.92) |
| 2. Beats R1, the no-ML control | **FAILED** — 1.14 against R1's 1.34 |
| 3. Positive in ≥ 6 of 10 years | not assessable — the portfolio test only spans 2019-2026 |
| 4. Positive in both eras | not assessable for the same reason |

Criterion 2 failing is the outcome §2.3 anticipated, though not for the anticipated reason: R1
wins by loading harder on the extreme-momentum tail, which is a bet we do not want, not because
the model is redundant.

**Post-hoc, and labelled as such:** R3 (smoothed rank) with an entry band was the best arm found
(Sharpe 1.06 banded). It was identified *after* seeing results and is therefore a hypothesis,
not evidence. If it is ever pre-registered and retested on fresh data, that is when it counts.

Criteria 3 and 4 being unassessable is itself the finding that matters: walk-forward training
consumes the early years, so a 113-date observation panel yields only a 7-year portfolio test.
**Extending the panel is now the binding constraint on everything downstream.**

---

## 5. Prerequisite work

### 5.1 Stable ranking
Today the model retrains on every run and probabilities cluster in a narrow band, so the order
shuffles on identical data. Three fixes, cheapest first:

1. **Persist the model** — retrain weekly, not per run. Removes retrain noise outright.
2. **Seed-ensemble** — average over 5 seeds. Reduces variance directly.
3. **Smoothed rank** — rolling 5-date mean rank (this is R3).

Measure first: the **day-over-day overlap of the top-10** is currently unknown. Take that
baseline before applying any fix, or there is nothing to compare against.

### 5.2 The missing free features
GKX's dominant predictors were **price trends, liquidity, volatility**. Two of three are
present. In order of value per unit of effort:

| Feature | Source | Effort |
|---|---|---|
| Dollar volume / turnover | our own panel | trivial — highest value per effort available |
| Amihud illiquidity | our own panel | trivial |
| Idiosyncratic vol, beta vs SPY | our own panel | trivial |
| GICS sector | the Wikipedia tables `panel.py` already fetches | small |
| Market cap | needs shares outstanding, external | small |

**Fundamentals cannot be backtested yet, and this must not be fudged.** The archives under
`data/fundamentals_history/` began 2026-08-19 and are forward-only; the vendor figures are
*restated*, so using today's numbers to predict a 2018 return is lookahead dressed as a
feature. Historical point-in-time fundamentals mean SEC EDGAR company-facts keyed on filing
date — real work, and out of scope here.

---

## 6. Code shape

- **`research/strategy.py`** — the rules of §2, as one implementation. The live scan and the
  backtest must import the *same* code; a backtest that re-implements the rules is testing a
  different strategy than the one that trades.
- **`research/backtest.py`** — walks the observation dates, applies `strategy.py`, produces the
  §3 metrics for each arm.
- `mlm_scan.py` refactored to call `strategy.py` rather than hold its own copy of the rules.

---

## 7. Pipeline hooks

Nothing here is allowed to end as a document on a shelf.

1. **`strategy-health` — monthly cron.** Re-runs the chosen configuration on the latest data
   and reports realised rolling-12m return, turnover and hit rate against what the backtest
   predicted. This is the drift monitor; it is how we find out the edge has decayed without
   waiting to notice it in the account.
2. **The daily MLM digest gains a portfolio-action line.** Given current holdings and today's
   ranks: what the rule would do at the next rebalance. That turns a watchlist into an
   instruction, which is the whole point.

---

## 8. Sequencing, with a gate on each phase

| # | Work | Gate before proceeding |
|---|---|---|
| 1 | Widen `START` to 2016; rerun observations + metalabel | Does `mom_12_1` survive ~95 dates? Does the meta-model still add? |
| 2 | Rank-stability baseline, then the three fixes (§5.1) | Top-10 overlap measurably improves |
| 3 | Liquidity / volatility / sector features (§5.2) | They add over the current feature set, or they are dropped |
| 4 | `strategy.py` + `backtest.py`; run the arms | The four criteria in §4 |
| 5 | Wire the two hooks (§7) | Both running on the box |

Phase 1 is cheap and decides a great deal. If the edge does not survive tripling the sample,
phases 2-5 do not need to happen.
