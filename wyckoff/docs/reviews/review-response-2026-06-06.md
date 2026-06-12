# Response to "Wyckoff Weekly — Signal Integrity Review & Fix Plan"

**Date:** 2026-06-06 · **Branch:** merged to `main` · **Commit range:** the `wyckoff: review *`
and `wyckoff: clarify demotion labels` commits (after `81cab00`).
**Verification artifacts:** `tests/validate_events.py` (4 synthetic controls, all pass) and a
full `weekly.py --dry-run` against live data (result below).

Thank you — the central diagnosis ("the layers do not constrain each other") was correct and
drove every fix. Summary of disposition, then item-by-item.

## Status at a glance

| Item | Status | Where |
|---|---|---|
| C1/S1/S2 — LLM ungrounded by detector | Fixed | `weekly._reconcile_with_events` (P0) |
| C2/S1 — BORDERLINE has no floor | Fixed | `weekly._composite` reweight + Watch floor (P1) |
| C3/S3 — event ordering not validated | Fixed | `events.detect_events` chronology (P2a) |
| C4 — news gate fails open | Fixed | `news.validate` fails closed (P2b) |
| C5/S4 — stale news from model priors | Fixed | grounded news prompt (P2c) |
| S5 — entry zone below price | Fixed | `_entry_below_price` flag (P2.5) |
| M1–M4 — detection recall | Implemented + calibrated | `events.py` (P3) |
| M5 — prescreen drops markup-pullbacks | Resolved (deliberate) | `prescreener.REL_PERF_CAP` 0.15→0.30 |
| N1/N2/N3 | Fixed | position-size chain / int positions / composite clamp |
| N4 | = P2.5 | — |

**Live dry-run after the fixes (2026-06-06, SPY 3.0% off high):** `0 STRONG, 5 BORDERLINE`.
All five render as `🔵 Watch` with **no Entry/Stop line**; none is an actionable buy. This is
the intended P1 behavior ("a week with no confirmed entries emits zero Buy rows").

## Item-by-item

### P0 — event grounding (S1, S2) — **implemented**
`weekly._reconcile_with_events(result, has_event)` runs in `_analyze_candidate` **before** the
bundle is built. When `has_event` is false it forces `phase_confidence="low"` on a markup read,
rewrites `buy/add`→`watch`, caps `criteria_met` at `STRONG_MIN_CRITERIA-1 (=6)`, and prefixes
the note `[unconfirmed — detector found no SOS/LPS]`.
*Acceptance met:* no row can be `markup/high/9 + Buy` without detected structure; the prefix
marks any spring/SOS the prose claims as unconfirmed.
*Deviation worth noting:* the LLM's free-text note is **prefixed**, not deleted — its reasoning
is preserved but explicitly flagged unconfirmed, and the `🔎 Events` line is the ground truth.
If you'd prefer the note fully replaced when ungrounded, that's a one-line change.

### P1 — BORDERLINE floor + composite (S1) — **implemented**
`_composite` is now `(0.4·crit + 0.4·ev + 0.2·quant) · (0.5 + 0.5·has_event)` with `quant`
clamped (N3). Entry-event presence is a **multiplier**, so a range-only momentum name can no
longer rank beside confirmed structure. Floor: `_format_result` emits `Entry/Stop` **only**
for `buy/add` recs, so Watch/Pass rows can't be traded as confirmed.
*Acceptance met:* see the all-Watch dry-run above.

### P2a — event chronology (S3) — **implemented**
`detect_events` carries integer positions (N2 fixed — no string round-trip) and, if a Spring
postdates the SOS (`spring_i > sos_i`), drops the SOS (and any dependent LPS) as a prior/failed
attempt. **`has_entry_event` now requires a confirming SOS or LPS** — a lone Spring is
early-stage accumulation, not a confirmed entry.
*Deviation worth your attention:* this is stricter than "any of Spring/SOS/LPS." I made it
deliberately, because S3's acceptance ("out-of-order pairs do not reach STRONG") otherwise
fails: after dropping AMCR's stale SOS, a lone Spring under an "any" rule would still clear
Gate D. Consequence: lone-Spring names are BORDERLINE/Watch, never STRONG. The synthetic
out-of-order control confirms AMCR-type input yields `has_event=False`.

### P2b — news fails closed (C4) — **implemented**
`news.validate` now returns `{"clean": False, "flag": "news parse failed — unverified", …}` on
`JSONDecodeError`. A parse failure can no longer act as a green light.

### P2c — grounded news prompt (S4) — **implemented**
The prompt now opens: *"Base your answer ONLY on the headlines listed below. Do NOT use any
prior knowledge… if an event is not in these headlines, it does not exist for this analysis."*
A 2024-era Mott-type mention should no longer surface from model priors.

### P2.5 — entry-below-price flag (S5) — **implemented**
`_entry_below_price(entry, price)` parses the zone; when the whole zone is below current price,
the action line is suffixed `⏳ limit (await pullback)`.

### P3 — detection tuning (M1–M4) — **implemented + calibrated against the harness**
- **M1 (SOS):** single-bar trigger retained; added a multi-bar path — cumulative advance over
  `SOS_CUM_BARS=3` clearing resistance on `SOS_CUM_VOL_X=1.3`× volume. Control #3 detects a
  3-bar push with no single +4% bar (`kind="multi"`).
- **M2 (windows):** events are scanned across the full range span (`scan_start = n -
  RANGE_LOOKBACK`), so a Spring/SOS that defined the range is no longer missed.
- **M3 (touches):** replaced raw-count touches with **time-separated clusters**
  (`TOUCH_CLUSTER_GAP`, `MIN_TOUCH_CLUSTERS=2`); a trending window (touches bunched at one end)
  is now rejected. Control #4 (uptrend) returns no range.
- **M4 (LPS):** re-specified to *holds above range-mid on contracting volume*, dropped the
  `LPS_NEAR_SOS` proximity rule. Control #3's LPS low (88) sits well below the SOS high (93.5)
  and is still detected.
*Caveat (honest):* thresholds are calibrated on synthetic controls + spot checks, not yet on a
labeled historical set. The post-fix live run found **no confirmed SOS among 30 candidates**
(hence 0 STRONG) — this may be correct (early-accumulation week) or indicate SOS recall is still
conservative. Recommend a labeled-history calibration pass before trusting STRONG frequency.

### M5 — prescreener intent — **resolved deliberately**
Raised `REL_PERF_CAP` 0.15→0.30. Rationale documented in code: the funnel targets accumulation
**and** markup-pullback; the regime-aware off-high floor already requires a pullback, so the cap
only needs to exclude parabolic momentum still at the highs. The −30pp floor (falling knives)
is unchanged.

### Minor — N1/N2/N3 — **fixed**
N1: `_position_size` requires the full Spring→SOS→LPS chain (`event_score≥4`) for "full
position." N2: integer positions in `events.py`. N3: `quant` clamped in `_composite`.

## Open items for the next pass
1. **STRONG is structurally rare in strong markets — design decision needed** (see the data point below).
2. Whether lone-Spring names should ever be STRONG (I say no; flagging the policy choice).
3. The two `_format_result` copies (`weekly.py`/`daily.py`) remain duplicated — any new digest
   field must be escaped/changed in both.

## Added data point (2026-06-07): why a live run produced 0 STRONG — a prescreen↔SOS tension

The first post-fix scheduled run (SPY ~3% off its 52-week high — i.e. near all-time highs)
produced **0 STRONG / 5 BORDERLINE (all Watch)**. To check whether that is correct behavior or
over-strict detection, the event detector was run across **all 30 candidates** for that run:

```
30 candidates → range: 8 · Spring: 5 · SOS: 0 · LPS: 0   →  STRONG-eligible (SOS or LPS): NONE
```

The detector is working (it finds ranges and Springs); there were simply **no confirmed SOS/LPS
anywhere in the funnel**. Control: **IEX** (the prior week's STRONG with SOS+LPS) now detects
**no range at all** — it has broken out and is no longer basing, so it correctly drops out.

**Root cause — structural, not a detector bug.** The prescreener's regime-aware **off-high floor**
required candidates to be **≥23% off their 52-week high** in this near-ATH regime. An **SOS is a
breakout that lifts price back toward the highs**, so a post-SOS name is *less* off its high and
is **filtered out at Stage 1** before the event detector ever sees it. Consequence: in a strong
market the funnel surfaces **pre-breakout bases** (range/Spring) and rarely confirmed breakouts,
so **STRONG is rare by construction**. The earlier M5 change addressed the *relative-performance*
cap; this run shows the **off-high floor** is the binding constraint.

**Is 0 STRONG correct?** For a precision-first accumulation funnel — yes. An all-Watch week with a
ranked Spring watch-list ("based + shaken out; waiting on the SOS") is the honest output. The
question is whether that is the *intended* product behavior near ATHs.

**Decision to make (with the reviewer) — held; no code changed:**
1. **Leave as-is (recommended).** STRONG = confirmed breakout entry, genuinely rare near ATHs;
   the Spring watch-list is the actionable output.
2. **Add a markup-pullback lane** — let a name qualify if it shows a programmatic **LPS near a
   recent breakout**, bypassing the off-high floor for that specific case. The "proper" fix for
   the off-high↔SOS tension; meaningful work.
3. **Ease the off-high floor** (e.g. cap the regime floor at ~15%) so post-SOS names survive
   Stage 1 — simplest, but admits more near-high momentum names and dilutes accumulation intent.

Recommend folding this into the review and deciding 1–3 deliberately; the current code implements
option 1.
