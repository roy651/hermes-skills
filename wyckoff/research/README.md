# The research bench

A place to answer "does this pattern actually work?" in minutes, with statistics that don't
lie to you.

**Read this before adding a detector or changing the entry logic.** It is written for a
session with no memory of how it came to exist.

---

## The one rule: the lab is not the line

```
wyckoff/
  scripts/      ← THE LINE. Runs in cron jobs. Touches real decisions.
    detectors.py    the detector bank (shared by both sides)
  research/     ← THE LAB. Never runs in production. Can be wrong safely.
    panel.py        universe → prices  → cache/panel.pkl
    observations.py prices   → labelled observations → cache/observations.pkl
    score.py        score every detector
    combos.py       score detector combinations
    regime.py       score detectors split by market regime
    promote.py      THE GATE — decides what may cross into scripts/
    cache/          ~1GB, gitignored, regenerable
```

A detector reaches production **only** by passing `promote.py`. That is not bureaucracy: the
session that built this bank produced five "significant" findings and **three were data
artifacts**. The gate is the attempt to make the scepticism that caught them mechanical.

---

## Running it

```bash
cd wyckoff
.venv/bin/python research/panel.py           # ~6 min · 2,079 symbols, 10y daily bars
.venv/bin/python research/observations.py    # ~3 min · builds the labelled panel
.venv/bin/python research/promote.py --explain   # the verdict, with rejection reasons
```

Optional deeper cuts, all reading the same cache:

```bash
.venv/bin/python research/score.py     # full scorecard, all detectors, by group
.venv/bin/python research/combos.py    # combination families
.venv/bin/python research/regime.py    # risk-on vs risk-off split
```

`panel.py` and `observations.py` are the slow steps and are cached. Once they have run, adding
a detector and re-scoring takes **about three minutes**, which is the entire point of this
directory existing.

---

## Adding a detector

1. Write a function in `scripts/detectors.py` taking `(f, i)` — precomputed features and a bar
   index — returning `bool`. Add any new indicator to `compute_features()`, not inside the
   detector; detectors are called millions of times and must be index lookups.
2. Register it in `REGISTRY` under the group it claims to belong to.
3. Re-run `observations.py` then `promote.py`.

**The one trap to avoid:** never compare an *adjusted* series to a *raw* one. See below.

---

## Why the gate looks the way it does

### The integrity check runs first and is fatal

`promote.py` refuses to score anything until every bar in the panel satisfies
`low ≤ close ≤ high`.

This exists because of a real bug. Yahoo dividend-adjusts only `adjclose`; `open/high/low`
come back raw. `data.py` mixed them, so on a dividend payer the close sat **below the bar's
own low** — 86% of PG's bars, 80% of KO's, ~3% of NVDA's. Any detector comparing close to
high or low silently became a *dividend-yield sort*.

It produced beautifully convincing results. `breakdown_50day_low` scored a 25.8% fire rate at
t = −5.02 and was written up as the strongest signal in the study. On corrected data it fires
**0.55%** of the time. `accumulation_day` and both Donchian detectors died the same way.

Nothing downstream can detect this — only the data can. Hence: first, and fatal.

### The statistical criteria

| Check | Threshold | Why |
|---|---|---|
| Sample size | n ≥ 250 | Small samples produce the biggest fake numbers. `donchian_55` reached +12.56% on n=217. |
| Both periods positive | in-sample **and** holdout | 2025-2026 is never touched during selection. |
| Significance | \|t\| ≥ 2.0 in at least one period | Fama-MacBeth: average within each date, then test across ~61 dates. Pooling 112,759 correlated observations would inflate every t-stat several-fold. |
| Consistency | positive in ≥ 4 of 6 years | A real effect shows up repeatedly. Spring was positive in **0 of 6**. |
| Regime | not `neither`/`unknown` | Records *when* it may fire, not whether. |

**Read the holdout column, not the t-stat.** With ~33 detectors and ~95 combinations under
test, roughly five will look significant by chance alone.

### Why the benchmark is peer-relative

Each observation is measured against the equal-weighted mean forward return of same-region
peers on the same date. Benchmarking to an index instead makes every number negative — the
median stock lags a cap-weighted index — which buries the differences that matter. Against a
peer mean, **zero means "no better than a coin toss among peers."**

---

## When to test combinations

Combine only when two detectors **answer different questions**.

**Worth testing**
- *filter × trigger* — one establishes context ("is this a healthy uptrend?"), the other times
  entry ("is now the moment?"). This is the family that worked.
- *independent sources* — price structure × volume × volatility.
- *attack with a defensive veto* — removes a disqualifying state rather than adding a claim.

**Not worth testing**
- Two detectors saying the same thing (`golden_cross` × `above_rising_200`) — n shrinks,
  information doesn't grow.
- Anything whose intersection falls below ~150 observations.
- Exhaustive pairwise search. 33 detectors is 528 pairs; at p<0.05 that manufactures ~26
  significant-looking results with no economic reasoning behind any of them.

A combination is only interesting if it **beats both parents**. `combos.py` reports that as
the `lift` column.

---

## Known data hazards

- **Adjusted vs raw price scales** — the bug above. Guarded by the integrity check.
- **Live/partial bars.** The current session's bar has partial volume. Any volume-ratio test
  (the Wyckoff LPS, `accumulation_day`, `volume_dryup_near_high`) will read that as "supply
  drying up." Do not run detectors intraday without dropping the current bar.
- **Short sessions.** TASE trades Mon–Fri with a **short Friday**, so Friday volume runs
  roughly ⅓–½ of a normal session. Volume-ratio tests are structurally biased toward firing on
  Fridays for `.TA` names. Not yet corrected for.
- **Overlapping windows.** Consecutive observations share forward return periods.
  Fama-MacBeth handles cross-sectional correlation but not this.
- **Merger artifacts** (13F work). A holder count collapsing usually means the security
  stopped existing, not that anyone sold.
- **Survivorship.** The panel is current index membership; delisted names are absent, so
  results skew optimistic.

---

## What has been established so far

See `../docs/signal-validation.md` for the numbers. In one line: **only 12-1 momentum is
robust on its own**, the winning structure is *momentum filters / contraction triggers*, every
trend detector flips sign when SPY is below its 200-day average, and the Wyckoff event layer
(Spring/SOS/LPS) did not earn its place in the entry decision.

## Expansion directions, ranked

1. **Exit detectors.** Everything here tests *entries*. The exit engine
   (`risk → deterioration → ladder`) has never been validated, and timely exits are the
   stated priority. Same bench, one change: measure returns *after* the exit fires and ask
   whether pain was avoided.
2. **Transaction costs and portfolio simulation.** Per-name excess return is not money. An
   equity curve with spread and slippage says whether a 4%-fire-rate signal survives reality.
3. **Pre-2020 panel.** The regime switch rests on 12 risk-off dates. Adding 2008 and 2018Q4
   would make it trustworthy rather than suggestive.
4. **More detectors.** Cheapest expansion — the bank is pluggable.
5. **Fundamental overlay.** Finnhub is already wired for earnings and consensus. Test whether
   momentum + earnings revision beats momentum alone.
6. **Universe variants.** TASE-only (no FX risk for an ILS holder), or sector-relative rather
   than region-relative benchmarking.

---

## Status log

**2026-08-13 — exit engine validated (§9 of docs/signal-validation.md).**
`research/exits.py` runs three studies on the exit half of the system. Verdict: the trailing
stop earns its place (return-neutral, cuts both tails by ~⅔); the 0–9 deterioration score does
not discriminate at a 6-month horizon; when deterioration and events contradict, events win.

**Bench portability confirmed.** Rebuilt from scratch on a second machine (Mac, pandas 3.0.5,
freshly downloaded panel, different network) and reproduced the promotion result to three
decimal places. The mini-PC is not required for research — only for cron registration and
anything touching runtime PII.

### Next, in order
1. **Short-horizon rerun of Studies 1 and 3 at 21 and 63 days.** The ladder acts weekly; the
   6-month test may be the wrong frame. BLOCKING any change to live trim behaviour.
2. **Plan B — costs + portfolio simulation** (`research/portfolio_sim.py`): monthly rebalance
   on the promoted signal, top-N equal weight, 10bp round-trip base case with 0–30bp
   sensitivity. Output equity curve, CAGR, max DD, Sharpe, turnover vs SPY/IWM.
3. **Phase 2 — `scripts/scan.py`** + Thursday prompt. Needs the mini-PC only for cron.
4. **`engine-health` monthly job** — the recurring hook: re-run the bench, report promotion
   status changes, integrity failures and stat drift. Would have caught the §0 price bug.
