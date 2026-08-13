# Signal validation — what is measured, and what failed

Empirical validation of the entry engine and candidate alpha sources, run 2026-08-06/07.
Deliberately PII-free: no positions, quantities or costs. Reproducible from the scripts named
at the bottom.

Read this before proposing a new signal. Most hypotheses tested here were falsified, including
several that sounded obviously correct.

**To operate the bench that produced this, see `../research/README.md`.**

---

## Current state of knowledge — read this first

Everything below is detail. This is the standing summary as of 2026-08-07.

| Question | Answer | Where |
|---|---|---|
| Is there a robust single signal? | **Yes — 12-1 momentum, and only that.** +2.58%, t=4.43; holdout +4.43%, t=4.05; works in both regimes | §5 |
| What is the best structure? | **Momentum filters, contraction triggers.** `mom_12_1 + nr7`: +3.54%, holdout +5.25% | §6 |
| Does the Wyckoff event layer earn its place? | **No.** The gate is net negative (−0.36% vs +0.02% for no signal). Spring positive in **0 of 6 years**; SOS and LPS significantly negative in holdout | §8 |
| Do we need a regime switch? | **Yes, and it's one condition.** Every trend detector flips sign when SPY is below its 200-day | §7 |
| Is insider buying useful for entries? | **No.** No edge vs a size-matched control; more conviction predicted worse outcomes | §3 |
| Should we buy deep drawdowns? | **No.** Monotonically punished, every year, worsening | §2 |
| Biggest hazard found | **A production price-scale bug** that made three of four headline findings artifacts | §0 |
| Does the deterioration score work? | **No — flat from score 0 to 8** at 6m. ⚠️ weekly horizon untested | §9.1 |
| Does the trailing stop work? | **Yes — return-neutral, cuts both tails ~⅔.** Keep it | §9.2 |
| Who wins when deterioration and events contradict? | **Events.** The contradiction bucket has the best win rate | §9.3 |

**The meta-lesson, which matters more than any single number:** this work produced five
"significant" findings and fixing one data bug killed three of them. Treat anything here that
is not momentum as provisional until it survives an independent check. The gate in
`research/promote.py` exists to make that scepticism automatic.

---

> ⚠️ **Sections 1–3 are HISTORY.** They were computed before the §0 bug was found, on a
> biased universe (insider-buying names) and against an index benchmark. Their *direction*
> held up on re-testing but their numbers did not. §8 is the corrected replacement for §1.
> §0 and §5–§8 use corrected data on the clean global panel.

---

## 0. A production bug: mixed price scales in `data.py`

**Found 2026-08-07 while validating a detector that looked too good.** Yahoo dividend-adjusts
only `adjclose`; `open`/`high`/`low` come back raw. `fetch_ohlcv` took the close from `adjclose`
and the rest raw, putting **two price scales inside one bar**.

Share of bars where the close fell outside its own `[low, high]`, before the fix:

| PG | KO | JNJ | XOM | GOOGL | NVDA |
|---|---|---|---|---|---|
| 86.2% | 79.8% | 69.2% | 68.2% | 7.5% | 2.8% |

The gap equals cumulative dividends, so **corruption is proportional to dividend yield** — near
zero for growth names, severe for income names.

What it broke:
- Every **intrabar** test in `events.py`: the Spring's pierce below support, the LPS, and
  `markup_pullback`'s central question — did the pullback *hold above* the breakout level. On a
  dividend payer the adjusted close sits below the raw breakout high, so the engine concludes
  the level **failed when it held**. The engine has been systematically misreading income stocks.
- Any research detector comparing close to high/low. Three of four headline findings from the
  first pass were destroyed by the fix (see §5).

**Fix:** apply the same per-bar adjustment factor (`adjclose / close`) to open/high/low so the
whole bar shares one scale. Verified at 0.0% impossible bars, TASE agorot conversion preserved.

**Rule going forward:** a detector that mixes an adjusted series with a raw series is measuring
dividend yield. Detectors built only from closing prices were unaffected.

---

## 1. The entry gate works — but only one of its three detectors

`has_entry_event()` fires on `sos OR lps OR markup_pullback`. Tested across 28,223
month-end observations on an 800-ticker sample, 2023-01 to 2026-02, measuring forward
excess return against IWM.

| State | n | median 3m | win 3m | median 6m | win 6m |
|---|---|---|---|---|---|
| gate fired | 2,420 | −1.80% | 45.4% | −3.61% | 45.1% |
| no event | 25,803 | −3.49% | 42.2% | −6.30% | 39.9% |
| **edge** | | **+1.69pp** | +3.2pp | **+2.69pp** | +5.3pp |

Positive every year: +1.2 (2023), +2.0 (2024), +1.2 (2025), +3.2 (2026). Fires on 8.6% of
observations — **the selectivity is the mechanism, not a bug.**

### Decomposed, the edge belongs entirely to markup pullback

| Detector | fires | median 6m | win 6m | median drawdown at signal |
|---|---|---|---|---|
| markup_pullback | 4.6% | **−0.36%** | **49.6%** | −4.6% |
| sos | 4.1% | −5.77% | 40.1% | −14.4% |
| lps | 3.3% | −6.48% | 39.2% | −14.8% |
| spring | 5.4% | −6.08% | 39.9% | −17.4% |
| *no event (baseline)* | 91.4% | −6.30% | 39.9% | −22.4% |

**LPS is worse than no signal at six months. SOS and Spring are inside the noise band.**
The classic accumulation chain — Spring → SOS → LPS — contributed nothing measurable in this
sample. Excluding Spring from the hard gate was correct; the same evidence argues SOS and LPS
do not deserve to be in it either.

Edge over baseline at 6 months, within drawdown bands:

| Drawdown band | baseline | SOS | LPS | markup pullback |
|---|---|---|---|---|
| 0 to −10% | −2.38% | +0.86 | +0.64 | **+3.27** |
| −10 to −25% | −4.99% | −3.12 | −4.35 | −1.77 |
| worse than −25% | −12.85% | +2.49 | +2.39 | +1.59 |

The **−10% to −25% band is a dead zone**: every detector loses to doing nothing. Worth
declining to trade outright.

**Implication for the funnel:** weight markup-pullback continuation setups near the highs
above accumulation-base hunting. This inverts the skill's historical emphasis.

---

## 2. Deep drawdowns were punished in every year — do not hunt on cheapness

Panel study, 109,357 monthly observations across 3,101 tickers, forward excess return vs IWM
bucketed by distance from the 52-week high.

| Drawdown | median 3m | win 3m | median 6m |
|---|---|---|---|
| 0 to −5% | −1.30% | 45.6% | −1.66% |
| −10 to −20% | −2.76% | 42.8% | −5.02% |
| −20 to −35% | −3.66% | 42.0% | −8.01% |
| −35 to −50% | −6.19% | 40.0% | −11.93% |
| −50 to −70% | −7.33% | 40.7% | −13.66% |
| worse than −70% | −16.97% | 34.9% | −32.98% |

Monotonic, and stable in all four years — the worse-than−50% bucket returned −8.97 (2023),
−13.23 (2024), −10.30 (2025), −13.57 (2026). **The penalty is steepening, not decaying.**

The worse-than−70% bucket has a *mean* of +38.3% against a median of −17.0%: a handful of
lottery tickets with the typical name destroyed. Capturing that tail needs dozens of
positions and tolerance for the drawdown — it is not a concentrated-portfolio strategy.

Note every bucket is negative in absolute terms because the median stock lags a
cap-weighted benchmark. **Only the gradient carries information, never the level.**

---

## 3. Insider-cluster buying: falsified

SEC bulk Form 3/4/5, 2022Q4–2026Q2. 108,129 qualifying open-market purchases (code `P`,
acquired), 6,244 clusters (≥2 distinct insiders within 45 days, ≥$25k), 10,030 single-buyer
controls. Entry on the **filing** date — using the trade date is lookahead.

| Group | n | median 3m vs SPY | median 6m vs IWM | win 6m |
|---|---|---|---|---|
| clusters ≥2 buyers | 4,239 | −3.83% | −5.00% | 42.8% |
| control, 1 buyer | 7,120 | −3.68% | −7.19% | 37.9% |
| 5+ buyers | 1,085 | −4.25% | −8.11% | 38.5% |
| commitment >$1M | 1,502 | −5.58% | −10.17% | 34.5% |
| best case: quiet base, above 200dma | 787 | −1.34% | **+0.26%** | 50.8% |

Clusters were indistinguishable from single buyers. **More conviction — more buyers, more
dollars — predicted worse outcomes.** The best-conditioned bucket is a 50.8% coin flip, and
its qualifying conditions are a momentum filter requiring no SEC data at all.

The published anomaly is largely pre-2010 evidence. Form 4 has been machine-parsable for two
decades. Assume it is arbitraged.

**Do not build an insider-signal entry input.** The EDGAR client is still worth keeping for
13F positioning, M&A detection and buyback announcements.

---

## 4. 13F positioning — the merger trap

Ranking by change in distinct reporting holders is a sound way to see institutional
accumulation (price cannot contaminate a holder count). But the raw "biggest distribution"
list is almost entirely **completed acquisitions** — the holder count collapses because the
security stopped existing. Mergers also corrupt the buy side, where the acquirer absorbs the
target's share base.

**Any mechanised 13F screen must filter deal completions first**, or it reads every merger as
a stampede.

---

## Method notes that matter

- **Entry on the filing date, never the event date.** The market cannot act on unpublished
  information.
- **Benchmark to the right universe.** Measuring small-cap-heavy signals against SPY loads
  the test with a size factor; the insider study only became interpretable against IWM.
- **Always run a control group.** Single-buyer purchases were what proved clusters add
  nothing — a cluster-vs-zero test would have looked like a success.
- **Report price coverage.** 84% here; the missing names are disproportionately delisted, so
  every result is an upper bound.
- **Sample skew.** The panel universe is tickers with insider activity, tilting small-cap and
  troubled (median observation 20.7% below its high). Broad, but not the market.
- **One regime.** 2023–2026 was strong-momentum and narrow-leadership. The cleanest
  falsification of the headline finding is re-running §1 on 2007–2012; if markup pullback
  loses to SOS/LPS there, this is regime evidence and the answer is a regime switch, not a
  permanent re-weighting.

## Reproducing

`scripts/edgar.py` — EDGAR client (Form 4 parsing, daily index, cluster detection).
Bulk datasets are the cheap path: SEC publishes quarterly Form 3/4/5 and 13F TSVs, which
turn a multi-hour throttled crawl into a few downloads.

Analysis scripts (`build_clusters.py`, `backtest_insiders.py`, `bt_context.py`,
`drawdown_panel.py`, `validate_wyckoff.py`, `decompose_gate.py`) were run from the session
scratchpad against `~/edgar_bulk/` on the mini-PC.

---

## 5. The detector bank — 33 patterns on a global panel (2026-08-07)

Panel: **1,907 tickers** across S&P 1500, LSE, ASX, TSX, SIX and TASE. 61 month-end dates,
2021-01 to 2026-01, **112,759 observations**. Benchmark is the equal-weighted mean of
same-region peers on the same date, so 0 means "no better than a coin toss among peers".
t-stats are Fama-MacBeth across dates (~60 d.f.), not across correlated observations.
Holdout: 2025-2026 untouched during selection.

### What the price fix killed

| Detector | fire% before → after | 6m excess before → after |
|---|---|---|
| `breakdown_50day_low` | 25.8% → **0.55%** | −3.01 (t=−5.02) → +9.83 |
| `accumulation_day` | 2.8% → 4.6% | +2.91 (t=2.66) → +0.14 |
| `donchian_20` | 0.28% → 0.49% | +8.27 → +0.42 |
| `mom_12_1_strong` | 27.7% → 27.7% | +2.54 → **+2.58 (unchanged)** |

`breakdown_50day_low` was briefly written up as "the strongest signal in the study". It was
firing whenever the adjusted close sat below the raw 50-day low — i.e. on dividend payers.

### Survivors (corrected data)

| Detector | fires | 6m excess | t | holdout | t_out | yrs+ |
|---|---|---|---|---|---|---|
| **`mom_12_1_strong`** | 27.7% | **+2.58** | **4.43** | **+4.43** | **4.05** | 4/6 |
| `three_day_pullback` | 4.4% | +1.00 | 1.22 | +2.27 | 0.91 | 4/6 |
| `nr7` | 14.8% | +0.88 | 2.09 | +1.32 | 1.10 | 5/6 |
| `rsi2_oversold_uptrend` | 12.2% | +0.91 | 1.89 | +0.74 | 0.74 | 4/6 |
| `below_falling_200` (defense) | 28.2% | −1.56 | −3.20 | −1.24 | — | 1/6 |

**Negative or noise in both regimes — do not deploy:** `vcp`, `ttm_squeeze`, `golden_cross`,
`pocket_pivot`, `bounce_off_200`, `bb_width_low`, `pullback_ma50`. Note `vcp` and `ttm_squeeze`
are popular published patterns and were consistently *negative* here.

## 6. Combinations — the rule for when to bother

95 combinations tested from four reasoned families, **not** the 528 exhaustive pairs (which at
p<0.05 would manufacture ~26 significant-looking results from noise).

**Combine when the detectors answer different questions:**
- *filter × trigger* — one establishes context, the other times entry. This is the family that worked.
- *independent sources* — price structure × volume × volatility.
- *attack with a defensive veto* — removes a disqualifying state rather than adding a claim.

**Don't combine when:** the two say the same thing (`golden_cross` × `above_rising_200`), or the
intersection falls below ~150 observations — small samples produce the biggest fake numbers.

| Combination | n | fires | 6m excess | t | holdout | t_out | lift |
|---|---|---|---|---|---|---|---|
| **`mom_12_1` + `nr7`** | 4,780 | 4.2% | **+3.54** | **3.38** | **+5.25** | **2.79** | +0.96 |
| `mom_12_1` + `rsi2_oversold` | 6,611 | 5.9% | +3.15 | 3.61 | +3.82 | 1.86 | +0.57 |
| `minervini_template` + `nr7` | 4,140 | 3.7% | +1.69 | 2.19 | +3.12 | 2.03 | +0.81 |

Winning structure: **momentum selects which, contraction selects when.**

## 7. The regime switch — measured, not assumed

2022 supplies a real bear market: 12 of 61 dates had SPY below its own 200-day average.

**Trend detectors flip sign entirely:**

| Detector | risk-ON | t | risk-OFF | t |
|---|---|---|---|---|
| `minervini_template` | +1.36 | 2.78 | **−2.24** | −1.71 |
| `above_rising_200` | +0.81 | 3.01 | **−1.75** | −1.50 |
| `new_52w_high` | +1.72 | 2.12 | **−1.52** | −0.55 |
| `pullback_ema20` | +0.29 | 0.54 | **−2.76** | −2.23 |

**Mean reversion inverts the other way:** `rsi14_oversold` +1.92 (noise) → **+9.11 (t=1.95)**.

**Durable in both:** `mom_12_1_strong` (+2.82 / +1.60), `nr7` (+0.88 / +0.86),
`rsi2_oversold_uptrend` (+0.67 / +1.93), `three_day_pullback` (+0.41 / +3.39).

⇒ **Gate the trend family on SPY > its 200-day average.** One observable condition, and the
cheapest insurance available. Wire it in before promoting any trend detector.

Caveats: 61 dates (thin), only 12 risk-off dates, overlapping 6-month windows, no transaction
costs. And the night's real lesson — four strong signals were found, and fixing the data killed
three. Treat anything but momentum as provisional.

---

## 8. The Wyckoff event layer, on corrected data (supersedes §1)

Same clean global panel, peer-relative benchmark, 112,759 observations.

| Detector | fires | 6m excess | t | holdout | t_out | yrs+ |
|---|---|---|---|---|---|---|
| `wyk_markup_pullback` | 8.5% | +0.34 | 0.57 | +2.15 | 1.16 | 4/6 |
| `wyk_sos` | 6.8% | −0.99 | −2.14 | **−4.57** | **−2.93** | 1/6 |
| `wyk_lps` | 5.4% | −1.24 | −2.34 | **−5.47** | **−3.20** | 1/6 |
| `wyk_spring` | 18.2% | −1.39 | **−3.91** | −4.02 | −3.29 | **0/6** |
| **`wyk_gate` (the OR)** | 15.0% | **−0.36** | −0.94 | −1.25 | −0.86 | 4/6 |
| *no event at all* | 85.1% | +0.02 | 0.27 | −0.03 | −0.17 | — |

Fixing the prices made `markup_pullback` fire more (6.5% → 8.5%, as predicted — the bug had
been blocking it) but its **edge fell**, from +1.28% to +0.34% (t=0.57). The prediction that
it would improve was wrong.

`markup_pullback + mom_12_1` gives +1.95%, which beats markup_pullback alone but is **worse
than momentum alone** (+2.58%). It does not add information to a momentum filter; it dilutes it.

**Conclusion:** the entry gate is net negative on clean data, and Spring/SOS/LPS are
significantly negative out of sample. Keeping the Wyckoff vocabulary for *reading* a chart is
a separate question from letting it *gate trades*. It currently does not deserve to.

⚠️ Not yet acted on. Changing the live funnel is a decision for Roy, and one night of evidence
justifies building an alternative to compare against — not switching off something in use.
The recommended path is shadow mode: run both, publish both, compare on live data.

---

## Operational hazards discovered while using this

- **Live/partial bars.** The current session's bar carries partial volume, and every
  volume-ratio test (Wyckoff LPS, `accumulation_day`, `volume_dryup_near_high`) reads that as
  supply drying up. Observed live on TEVA.TA 2026-08-07: 76k volume against a 1.16M median
  triggered an LPS. **Drop the current bar before running detectors intraday.**
- **Short sessions.** TASE trades Mon–Fri with a short Friday; Friday volume runs ~⅓–½ of a
  normal session. Volume-ratio tests are therefore structurally biased toward firing on
  Fridays for `.TA` names. Not yet corrected for.
- **Missing bars.** TEVA.TA had no bar for Thursday 2026-08-06, a trading day. Cause unknown;
  check the broker before acting on recent `.TA` price history.

---

## 9. The exit engine, validated (2026-08-13)

First measurement of `risk → deterioration → ladder`. 112,716 observations, 1,906 tickers,
61 month-ends, 6-month horizon. Peer-relative benchmark. No LLM.
Reproduced on a second machine (Mac, pandas 3.0.5, freshly downloaded panel) — the three
promoted entry detectors matched the mini-PC to three decimal places, so the bench is portable
and the results are not machine artifacts.

### 9.1 The deterioration score does NOT discriminate

| score | n | mean excess | median | win % |
|---|---|---|---|---|
| 0 | 22,196 | −0.33 | −2.41 | 44.4 |
| 1 | 28,292 | +0.18 | −2.42 | 44.4 |
| 2 | 26,846 | +0.54 | −2.52 | 44.9 |
| 3 | 21,318 | −0.02 | −2.80 | 44.3 |
| 4 | 5,499 | −0.91 | −2.79 | 43.5 |
| 5 | 4,128 | −0.69 | −2.72 | 44.2 |
| 6 | 2,614 | −0.77 | −2.73 | 44.6 |
| 7 | 1,524 | −1.34 | −3.14 | 44.2 |
| 8 | 299 | +0.19 | −2.60 | 45.8 |

**Score 0 and score 8 produce effectively identical outcomes.** Medians span a −2.4 to −3.1
band with no monotonic trend; win rates are flat at ~44% throughout. The faint tilt at score 7
reverses at 8. Note mean excess is ~0 by construction (the peer benchmark is a mean and returns
are right-skewed) — **the median column is the honest read, and it is flat.**

Consequence: the 0–9 score drives the ladder's trim staging, so the trims it issues are close
to arbitrary at this horizon.

### 9.2 The trailing stop is return-neutral and halves both tails — KEEP IT

`stop = max(chandelier, structure)`, exit on close-through. Simulated day-by-day against a
buy-and-hold leg, with the stopped leg **redeployed into SPY** for the remaining horizon
(without that redeployment the stop looks catastrophic at −7.5pp; that version is wrong,
because being stopped does not put you in cash).

| bucket | hold | stop+redeploy | delta | p5 | p95 |
|---|---|---|---|---|---|
| ALL | 7.03 | 7.17 | **+0.15** | −29.8 → **−8.5** | 50.7 → **20.1** |
| score 0–2 | 7.10 | 7.22 | +0.12 | −29.6 → −8.4 | 51.3 → 19.5 |
| score 3–4 | 7.15 | 7.21 | +0.06 | −30.2 → −8.8 | 49.8 → 21.5 |
| score 5+ | 5.96 | 6.62 | +0.66 | −29.6 → −8.1 | 47.9 → 21.2 |

Same expected return, distribution cut by roughly two-thirds on both sides. That is what a stop
is for. **99.8% of positions stop out within 6 months** — median gap to price ~3%, matching the
live digests (0.07%, 0.27%, 1.49%, 1.96%, 2.41% observed on 2026-08-09). This is by design:
`max()` takes the TIGHTER of the two stops, and 3×ATR on a low-volatility name is only ~4%.

⚠️ A 60-ticker smoke test showed "helps on score 5+, hurts on 3–4". That **vanished on the full
panel** — small-sample noise. Do not build on smoke tests.

### 9.3 When deterioration and events contradict, deterioration is wrong

| case | share | mean | median | win % |
|---|---|---|---|---|
| CONTRADICT (score≥5 AND entry event) | 1.6% | **+0.39** | −2.10 | **46.0** |
| BEAR only (score≥5, no event) | 6.0% | **−1.11** | −2.97 | 43.97 |
| BULL only (entry event, score<5) | 13.4% | −0.16 | −2.52 | 44.66 |
| calm | 79.0% | +0.10 | −2.54 | 44.42 |

The contradiction bucket has the **best** win rate of the four. The bearish read loses when
challenged. The score carries mild information only uncontradicted (−1.11), and even that is
~1pp. This is the TEVA/DD case from the 2026-08-09 digest, and it resolves against the trim.

### ⚠️ Open objection — do not act on 9.1/9.3 yet

Everything above is measured at a **6-month** horizon. The ladder acts on a **weekly** cadence.
The score may predict 1–4 week weakness and simply have been tested at the wrong frame. Rerun
Studies 1 and 3 at **21 and 63 days** before changing any live trim behaviour. "Your trim signal
is noise" is too consequential to rest on a possible horizon mismatch.

Reproduce: `python research/exits.py [--sample N]`
