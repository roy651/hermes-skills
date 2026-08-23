# Strategy plan: an earnings-event sleeve

Written 2026-08-23, after the widened test (`signal-validation.md` §11) found no reliable edge
in continuous technical selection. This proposes a different *shape* of signal rather than
another detector, and answers the fundamentals-data question directly.

Two requirements govern everything here:
**(A)** find entries with minimal intervention, given that mechanical technical signals alone
have been shown not to work; **(B)** manage positions to good exits.

---

## 1. The reframe: event-driven, not continuous

This is the load-bearing idea, and it is about *shape*, not about finding a better score.

Every signal tested so far is **continuous**: it produces a number for every stock every day, so
it demands a decision every day, and it is right barely more often than a coin. That is the
worst possible fit for someone who does not want to watch screens — maximum intervention for
minimum edge.

An **earnings-event** signal is the opposite:

| | continuous score (what we built) | earnings event (proposed) |
|---|---|---|
| When it fires | every day, every name | ~4× a year per name |
| Calendar | unknown | **known weeks in advance** |
| Decision load | daily, unbounded | a handful per week, scheduled |
| Holding window | undefined — the hard part | defined by the drift window |
| Exit | needs a separate mechanism | time-based, plus the stop |

The scheduled, predictable calendar is the point. It converts "watch the market" into "on these
dates, look at these names" — and it makes the exit fall out of the entry rather than needing
its own unvalidated machinery, which is exactly where the deterioration score failed.

---

## 2. Part A — entries: post-earnings announcement drift

### 2.1 What it is
Prices do not fully adjust to an earnings surprise on the announcement day; they continue
drifting in the direction of the surprise. Documented since Ball & Brown (1968) and Bernard &
Thomas (1989), and still one of the most-studied anomalies in finance.

The measure is **SUE** — standardized unexpected earnings — the surprise divided by its own
historical variability, which makes it comparable across companies of different sizes and
earnings volatility.

### 2.2 The finding that makes this buildable for free
SUE has **two** definitions, and the difference decides whether this costs money:

- **Analyst-based:** actual minus consensus estimate. Consensus estimates are a paid vendor
  product.
- **Seasonal random walk:** expected earnings = the same quarter a year ago, plus drift. **No
  analyst data at all** — computable purely from a company's own filing history.

The seasonal-random-walk version is not a poor substitute. The literature uses it deliberately,
*specifically so that small stocks without analyst coverage are not excluded — because that is
where the drift is strongest.* That aligns exactly with the structural-advantage argument in the
retrospective: the edge lives where coverage is thin, and the free method is the one that
reaches there.

### 2.3 The honest state of the evidence
This must be stated plainly, because the whole retrospective was about not overselling:

- **The effect has decayed.** The high-minus-low SUE spread fell from about **5% in the
  1980s-90s to 3% or below by the late 2010s.**
- **The decline has a cause, and it is not only crowding.** Kettell, McInnis & Zhao (2022) find
  that the *persistence of earnings news* itself has fallen; after controlling for it, the
  downward trend in PEAD is statistically insignificant. Structural changes — decimalisation,
  Reg NMS, high-frequency trading, SOX — moved more of the adjustment onto announcement day.
- **The window has shortened.** Recent work finds much of the drift concentrated in roughly the
  **first nine sessions**, against the classic 60-day framing. Shorter window, more turnover,
  costs matter more.
- **Frictions bite.** PEAD strategies are materially less profitable once fees and short-sale
  constraints are included — and we would be long-only, which removes half the classic spread.

**Expected value: a real but modest effect, decayed, strongest exactly where liquidity is
worst.** That is a genuine tension and it should be priced in from the start, not discovered in
a backtest.

### 2.4 Why it is still worth building
- It is **event-driven**, which is the shape requirement (§1) — that alone is worth a lot here.
- It is **economically grounded**: a surprise is new information about the business, not a
  pattern in a price chart. Every technical detector we tested lacked this.
- It is **orthogonal** to what we already run. Even a weak signal uncorrelated with momentum
  improves a portfolio.
- It **answers the stated blind spot** — beats/misses, cash flow, the actual economics.

---

## 3. Part B — exits: use what survived, drop what didn't

The exit design is already decided by evidence rather than opinion. From §9 and §11:

| Mechanism | Verdict | Role here |
|---|---|---|
| **Time exit at the end of the drift window** | the entry defines it | **primary exit** |
| **Trailing stop, now ratcheted** | validated for the discretionary book | **risk exit** |
| Deterioration score (trim ladder) | **failed validation** | not used |
| Chandelier inside a systematic sleeve | measured *harmful* | not used as primary |
| Buffer band M = 2N | validated | used if the sleeve is ranked |

So: **exit on time, unless the stop fires first.** No score, no ladder, no judgement call. The
one open question is the window length — the classic 60 days versus the ~9 sessions recent work
suggests — and that is an empirical question the backtest answers rather than a design choice.

Note the ratchet and structure-stop minimum shipped on 2026-08-23 (`risk.py`); the stop is a
sounder instrument now than when §9 measured it.

---

## 4. The data question, answered

### 4.1 Free path — SEC EDGAR
`https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` returns **every XBRL-tagged fact
across every filing** for a company: revenue, EPS, operating cash flow, margins — the full
us-gaap taxonomy, with sub-minute publication delay. The companion `xbrl/frames` endpoint
aggregates one fact across all filers for a period, which is the shape needed for a
cross-sectional screen.

Crucially it is keyed on **filing date**, so it is genuinely point-in-time: you can reconstruct
what was knowable on any past date. That is what our current fundamentals archive cannot do —
vendor figures are *restated*, so using today's numbers to predict a 2018 return is lookahead.

**Cost: free. Effort: real** — CIK↔ticker mapping, tag inconsistency across filers, restatements
and amended filings, and quarterly-vs-annual arithmetic (Q4 is often only derivable as
annual-minus-three-quarters).

### 4.2 Paid path — and it solves the bigger problem too
**Sharadar** (via Nasdaq Data Link) is the strong candidate, and not mainly for fundamentals:

- Point-in-time fundamentals back to the 1990s, with both as-reported and restated dimensions,
  each row carrying the date the figure **became available**.
- **Delisted companies included** — "nearly completely free of survivorship bias."
- **An S&P 500 constituents table.**

That last point matters more than the fundamentals. **Survivorship is the binding constraint on
everything we do** — §11 could not be resolved because our universe is today's survivors. A
historical constituents table plus delisted price history fixes that. One subscription unblocks
the entire research programme *and* supplies the earnings data.

Reference points: FMP is ~$19/month but is not built for point-in-time; a Sharadar-alternative
PIT API advertises ~$49/month; Sharadar's own tiers need checking on its subscribe page.

**Recommendation: price Sharadar first.** If a few tens of dollars a month removes the one
constraint that invalidated a month of research, it is the highest-value purchase available —
and far better value than more of my time spent working around a biased panel.

### 4.3 What is still not free
Analyst **estimates**, **guidance** and **pipeline** commentary. The seasonal-random-walk SUE
(§2.2) routes around estimates entirely. Guidance and pipeline are qualitative and live in
8-Ks and call transcripts — a later, and much harder, question.

---

## 5. Build plan, with a gate on each phase

| # | Work | Gate before proceeding |
|---|---|---|
| 0 | **Price Sharadar.** If affordable, buy it and rebuild the panel point-in-time | A decision, not a task. Everything after is cleaner if this passes |
| 1 | EDGAR loader: CIK map, quarterly EPS + operating cash flow keyed on filing date, cached | Reproduces a known company's reported history exactly |
| 2 | Compute SUE (seasonal random walk) for the panel; build an event table of (ticker, filing date, SUE) | Top-vs-bottom SUE decile spread is visible and positive |
| 3 | Measure the drift window honestly — 5 / 10 / 21 / 63 sessions, Fama-MacBeth *t*, medians alongside means | Positive after costs, in **both** halves of the sample |
| 4 | Portfolio sleeve: reuse `research/backtest.py` — time exit + ratcheted stop | Beats SPY risk-adjusted after 10bp, in both eras |
| 5 | **Hook it up:** a weekly *earnings calendar* digest — who reports next week and their prior SUE — plus an event alert when a held or watched name posts a large surprise | Both running as cron jobs |

Phase 5 is not optional decoration. Per the standing instruction, the pipeline hook is named
before the build starts: this ends as **a scheduled digest that says "these names report this
week, these surprised last week"** — which is precisely the minimal-intervention mechanism
requirement (A).

---

## 6. What this does not fix, stated up front

- **It is not a large edge.** A decayed 3% spread, long-only, after costs, is thin. Build it for
  the *shape* — scheduled, bounded, economically grounded — not for the magnitude.
- **The best drift is in the least liquid names**, where our costs are worst. This tension is
  structural and may be the thing that kills it. Measure cost as a function of dollar volume,
  not as a flat assumption.
- **EDGAR is US-only.** TASE names are outside it, which matters given the retrospective argued
  the thin-coverage Israeli shelf is where a structural advantage might live. A separate source
  (Maya/TASE filings) would be needed and is out of scope here.
- **It does not rescue the technical work.** It is a different signal family, not a repair.
