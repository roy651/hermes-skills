# Response to Review 2 (`review2-summary.md`)

**Date:** 2026-06-08 · merged to `main`, deployed to mini-PC.
**Artifacts:** `tests/validate_events.py` (Tier 1, 22 fixtures), `tests/validate_events_tier2.py`
+ `tests/fixtures/*.csv` (Tier 2, 6 real snapshots), `tests/build_fixtures.py`.

All five items from the re-review are implemented. Summary, then detail and the items that
need your eye.

## Status

| Item | Status | Evidence |
|---|---|---|
| A — ship Option 1 + header nit | ✅ | 0-STRONG digest now reads "no confirmed breakouts — bases forming" |
| A — build Option 2 (markup-pullback lane) | ✅ | new lane + prescreen admission + funnel wiring; end-to-end 5 STRONG |
| A — skip Option 3 | ✅ | off-high floor untouched |
| B — Tier 1 boundary-pair corpus + confusion matrix | ✅ | 22 fixtures, 100% pass, precision=recall=1.00, 0 distribution FP |
| B — Tier 2 real fixtures | ✅ (PROPOSED labels) | 6 snapshots; matrix TP2/FP0/FN0/TN4; 2 gaps flagged below |
| Carryover — consolidate `_format_result` | ✅ | shared `scripts/digest.py` |
| Carryover — LPS to breakout level | ✅ (in Option 2) | markup-pullback LPS holds above the breakout, not range mid |

## B (Tier 1) — boundary-pair corpus
One matched pair per threshold you listed (SOS_GAIN, SOS_VOL_X, SOS_CUM/CUM_VOL, touch
clusters, RANGE_MAX_WIDTH, SPRING_PIERCE, LPS_VOL_X, chronology), each labeled with expected
`range/spring/sos/lps/event_score/has_entry_event`; plus the markup-pullback boundary set. The
harness prints a `has_entry_event` confusion matrix and enforces **zero FP on the
distribution/failed-breakout class**. All 22 pass.

## A — Option 2 (markup-pullback lane)
`events.detect_markup_pullback`: a confirmed breakout above a prior ceiling, followed by a
pullback that **holds above the breakout level on contracting volume** — independent of
`detect_range` (so it works once a name has left its base). It **bypasses the prescreen
off-high floor / rel-perf cap** (admission in `prescreener._fetch_and_score`); the lone-Spring
policy is unchanged.

Two refinements were driven by testing, not by feel:
1. **Max-pullback bound** (`MP_PULLBACK_MAX=0.15`) — the real-data scan caught a deep give-back
   (AMD ~50% off peak) qualifying. "Near recent highs" now means ≤15% off the peak. Added a
   corpus control (`mp_deep`).
2. **Admission cap** — the first end-to-end dry-run flagged **28** markup-pullbacks crowding the
   funnel. Capped to the **10 shallowest** (nearest highs); accumulation fills the rest
   (`MP_PRESCREEN_CAP=10`).

**End-to-end result:** the funnel went from 0 STRONG (last week, off-high floor excluding all
breakouts) to **5 STRONG markup-pullbacks** (ROK, MO, WST, EQIX, DAL), each `Markup-pullback
LPS … (holds >breakout …)`, 50% sizing. This resolves the structural 0-STRONG-near-ATH issue.

**Acceptance (your spec) — met:** a clean post-breakout pullback becomes STRONG-eligible without
a 60-bar range and without the off-high floor; a still-extended name (no pullback) and a
failed breakout (back below the level) do **not** qualify (corpus controls `mp_extended`,
`mp_failed`).

## B (Tier 2) — real fixtures, PROPOSED labels (please vet)
Frozen CSV snapshots (2025-06-06 → 2026-06-05). Labels are proposed from price structure +
detector output; not yet human-vetted:

| Fixture | Class | Asserted | Why |
|---|---|---|---|
| ROK, EQIX | markup_pullback | mp=True, has=True | cleared ceiling, ~4–5% pullback holding above breakout on lighter volume |
| LLY | clear_not | range=False, has=False | steady uptrend near highs, no base |
| NKE | clear_not | range=False, has=False | deep markdown (~46% off) — must not green-light (no FP) |
| TDG, EIX | accumulation_unconfirmed | range=True, spring=True, has=False | range + Spring, no SOS → lone-Spring stays Watch |

All six match the proposed labels.

**Gaps (need a real example you can point to):**
1. a clean **range → Spring → SOS → LPS** "clear STRONG" chain, and
2. an explicit **failed breakout** (cleared a level, then collapsed back below it).
Both are currently covered only by Tier-1 synthetic controls. If you give a ticker+window, I'll
add them as committed fixtures.

## For your attention (decisions made without labeled-tuning data)
- `MP_PULLBACK_MAX=0.15` and `MP_PRESCREEN_CAP=10` are **guardrails**, not calibrated constants —
  set to keep the lane sane until Tier 2 grows. Flag if you'd tune them differently.
- Markup-pullback `event_score=2` ⇒ capped at **50% position** (N1: "full" needs the full
  Spring→SOS→LPS chain). Confirm that's the intended sizing for a markup entry.
- In a near-ATH regime the markup lane is now the **primary source of STRONG** (this week: all 5).
  That's the regime, not a bug — but confirm it's the desired product behavior vs. wanting
  accumulation STRONGs to also surface.
