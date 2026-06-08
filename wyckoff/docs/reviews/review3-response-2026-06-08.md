# Response to Review 3 (`review3-summary.md`)

**Date:** 2026-06-08 · merged to `main`, deployed. All four acceptance criteria met, with one
**new empirical finding** (below) that you'll want before signing off the lane.

## Acceptance (§6)

| # | Criterion | Status |
|---|---|---|
| 1 | `detect_markup_pullback` → None for `mp_climax` + real climax after §3; still fires ROK/EQIX/quiet | ✅ |
| 2 | `build_fixtures` supports date range; ≥1 real climax + 1 real failed-breakout committed, labeled | ✅ |
| 3 | MP admissions ranked by quality, not shallowness | ✅ |
| 4 | Tier-2 matrix includes adversarial positives; 0 FP on dist/clear_not | ✅ |

## What was done

- **§1 — Tier-2 labels:** your independent re-labels all matched mine; I've marked the six
  trailing-snapshot fixtures **CONFIRMED ground truth** in `validate_events_tier2.py`. Thanks for
  the non-circular re-label.
- **§3 — effort filter:** `MP_EFFORT_X=1.5` in `detect_markup_pullback` — reject when the
  **rally-leg average** volume exceeds 1.5× the prior-base average (keys off the average, so a
  single breakout-thrust bar isn't penalised). ROK/EQIX measure 0.75×/0.76× (pass); the synthetic
  climax measures ~4× (reject).
- **§4a — synthetic controls:** added your `_mp_pattern` builder + `mp_quiet` (pos),
  `mp_climax` (**in the `dist` class — the regression guard**), `mp_failed_below` (dist). Tier-1
  is 25 fixtures, 100% pass, 0 distribution FP.
- **§4b — real fixtures:** extended `data.fetch_ohlcv` with an explicit `start/end` range;
  `build_fixtures.py` now freezes historical 252-bar windows; `screen_historical.py` finds
  cases by screen. Committed:
  - `SMCI_climax_240315` — effort **4.11×**, the filter rejects it (`dist`, has=False).
  - `CVNA_failed_211231` — broke out then closed back below the level (`dist`, has=False).
- **§5 — admissions:** ranked by **effort margin (quietest rally first) then bars-holding**,
  replacing raw shallowness (which biased toward post-climax first-pullbacks).

End-to-end unchanged in behavior for the clean cases: this week's MP STRONGs (ROK 0.75×, EQIX
0.76×) remain valid; the climax trap is now rejected.

## ⚠️ New finding — the effort filter is necessary but NOT sufficient

Screening real history surfaced two failure modes your synthetic (cleanly climax-vs-quiet)
didn't show. Both are committed/observable:

1. **Quiet-rally distribution tops survive the filter.** `CVNA 2021-09-15` fires the lane with
   effort **0.89×** (quiet) and then fell **~$66 → $35**. `PTON 2021-02-15` is the same shape
   (effort 0.63×, then collapsed). The effort filter only catches *climactic* tops; a top that
   forms on *quiet* volume is still a false positive. I committed `CVNA_quiettop_210915` as a
   **flagged KNOWN-FP fixture** (the harness prints it as an open limitation; it is *not* counted
   in the zero-FP gate, but it is on the record and will flip green when a fix lands).
2. **Climactic rallies sometimes continue.** `SMCI 2024-03-15` (effort 4.11×) and `MARA
   2021-02-12` (2.48×) were climactic but continued higher short-term before SMCI's eventual
   collapse. So the effort filter will occasionally **reject a continuation (a false negative)** —
   acceptable under "climax = high risk, stand aside," but worth naming.

**Net:** effort filter closes the *climactic* FP you proved (real + synthetic) and is the right
first guard. It does not make the MP lane safe on its own. Until a second discriminator exists,
your standing guidance holds: **treat every MP-lane STRONG as confirm-by-eye, not auto-act.**

## Decision I need from you (§ next round)
The quiet-rally-top FP needs a second signal the lane currently lacks. Options I can build,
each with corpus + real fixtures first:
- **(a) post-entry invalidation:** require price to *hold* above the breakout for N bars on a
  re-test before the lane returns STRONG (rejects first-reactions that immediately roll over).
- **(b) distribution-after-markup check:** look for UTAD/SOW/heavy-down-volume in the recent
  window and veto.
- **(c) accept + document:** keep effort-only, lean on confirm-by-eye + 50% sizing, and rely on
  the daily exit-watch to catch the rollover.

I'd lean (a) — it directly targets the observed failure (first-reaction tops) and is testable
against the CVNA/PTON fixtures. Tell me which to pursue.
