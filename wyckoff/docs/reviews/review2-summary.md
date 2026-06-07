# Wyckoff Signal Integrity — Review 2 Summary & Next Tasks

**Context:** follow-up to `wyckoff-signal-integrity-review.md` and your `docs/review-response-2026-06-06.md`.
**Re-review verdict:** P0–P3, M5, N1–N3 all verified against the code — correct, and the deviations you flagged (note-prefix vs delete; lone-Spring excluded from Gate D) are the right calls. Keep them. The 0-STRONG live run is a genuine structural outcome, not a regression.

Two tasks below: (A) the off-high↔SOS decision — **proceed 1 → 2**; (B) a ground-truth test corpus, which is the higher priority.

---

## A. Decision: ship Option 1 (done), build Option 2 next. Skip Option 3.

**Option 1 is the correct default and it's already live** — an all-Watch week near ATHs with a ranked Spring watch-list is the honest output. No change needed except one UX nit: the digest header for a 0-STRONG week should read as *"no confirmed breakouts this week — bases forming"* so it doesn't look like a failure.

**Option 3 (ease the off-high floor) — do NOT do.** It's a false economy. SOS detection is gated on `detect_range`, so loosening the prescreen floor won't produce SOS detections on trending names; it mainly admits consolidations-near-highs, which near ATHs are as likely to be distribution as re-accumulation. Dilution without reliable benefit.

**Option 2 (markup-pullback lane) is the real fix — start here.**

Key constraint to design around: the current LPS detector is gated on `detect_range` + a prior SOS. A name in an established markup is **no longer in a 60-bar horizontal range** (this is why IEX dropped out — it broke out and stopped basing), so the existing path can never fire for it. Option 2 therefore needs a **new, second detection mode**, not a parameter tweak:

- Detect a **recent breakout level** (e.g. prior swing-high / prior range resistance that price has cleared and is now above), independent of `detect_range`.
- Detect an **LPS relative to that breakout**: a higher low that **holds above the breakout level** (stricter than the current "above range mid") on **contracting volume**.
- This lane **bypasses the prescreener off-high floor** for that specific, structurally-justified case (a markup pullback need not be 20%+ off its high).
- Keep the lone-Spring policy unchanged (Spring alone → BORDERLINE/Watch, never STRONG).
- Give it its own controls in the corpus below before wiring it into the STRONG gate.

**Acceptance for Option 2:** a name in a clean post-breakout pullback (LPS above the breakout level, contracting volume, near recent highs) becomes STRONG-eligible *without* a 60-bar range and *without* passing the off-high floor — while a name still extended at the highs with no pullback does **not** qualify.

---

## B. Build a ground-truth (GT) corpus — top priority, do this alongside/ before Option 2

The blocker right now: thresholds are calibrated on 4 synthetic controls. "0 SOS across 30 candidates" is ambiguous — quiet-base week vs detector too tight — and we can't tell without labeled data. We need a GT set to measure **false positives and false negatives** and to defend every threshold.

Build it in **two tiers**:

### Tier 1 — Synthetic boundary pairs (threshold sensitivity)
For **each** decision threshold, hand-construct OHLCV fixtures as **matched pairs that straddle the boundary** — one just inside, one just outside — so we catch off-by-epsilon errors and confirm the boundary sits where intended. Label each with expected `events`, `event_score`, and `has_entry_event`.

| Threshold (events.py) | "Just over" fixture | "Just under" fixture |
|---|---|---|
| `SOS_GAIN=0.04` (single-bar) | +4.2% bar on 1.6× vol | +3.8% bar on 1.6× vol |
| `SOS_VOL_X=1.5` | +4.5% on 1.6× vol | +4.5% on 1.4× vol |
| `SOS_CUM_GAIN=0.06` / `SOS_CUM_VOL_X=1.3` (multi-bar) | 3-bar +6.3% clearing resistance on 1.4× | 3-bar +5.7% on 1.4× |
| `MIN_TOUCH_CLUSTERS=2` / `TOUCH_CLUSTER_GAP=5` | 2 visits 8 bars apart | 2 touches 3 bars apart (1 cluster) |
| `RANGE_MAX_WIDTH=0.20` | band width 18% | band width 22% |
| `SPRING_PIERCE=0.99` | low dips to 0.985×support, closes above | low only to 0.995×support |
| `LPS_VOL_X=0.7` | pullback at 0.65× SOS vol | pullback at 0.75× SOS vol |
| chronology (`spring_i > sos_i`) | Spring then SOS (valid) | SOS then later Spring (must drop SOS → `has_event=False`) |

### Tier 2 — Curated real historical windows (realism / generalization)
Pull real OHLCV for known cases (save as fixtures so tests are offline/deterministic) across these classes, each labeled with the expected outcome:

- **Clear STRONG** — textbook range → Spring → SOS → LPS (e.g. a 2024–25 name you can eyeball). Expect `has_entry_event=True`, correct chronology, STRONG-eligible.
- **Clear markup-pullback (Option 2)** — established uptrend, clean LPS above a recent breakout. Expect Option-2 STRONG-eligible, no 60-bar range required.
- **Clear NOT** — steady uptrend with no base (no range), and choppy no-structure. Expect `range=None`, `has_entry_event=False`.
- **Distribution / failed breakout** — range that tops out, or SOS followed by a later Spring back into the range. Expect SOS dropped / not STRONG (guards against the most dangerous false positive).
- **On-the-verge** — real cases that sit near a threshold; label the intended call and let the test pin it. These are the ones that expose drift.

### Harness + metrics (extend `tests/validate_events.py`)
- Load each fixture, run `detect_events`, assert the labeled `events` / `has_entry_event`.
- Emit a **confusion matrix for `has_entry_event`** (the FP/FN we actually care about) plus per-event precision/recall across the corpus.
- **Pass bar:** zero false positives on the Distribution/failed-breakout class (never green-light a top), and the boundary pairs land on the correct side of each threshold. Track FN rate on Clear-STRONG/markup-pullback as the recall number we tune against.
- Keep fixtures as committed CSVs (or a small generator with fixed seeds) so runs are deterministic and offline.

**Why this gates everything else:** once the corpus exists, the off-high↔SOS decision stops being a judgment call — we'll see directly whether STRONG is structurally rare or the SOS thresholds are too tight, and Option 2's new LPS lane can be tuned against labeled markup-pullbacks instead of by feel.

---

## Carryover (minor)
- Consolidate the duplicated `_format_result` (`weekly.py` / `daily.py`) — same drift class that caused the earlier HTML-escape bug.
- LPS currently holds above range **mid**; consider tightening to the **breakout/resistance** level (and reuse that logic for the Option 2 lane).
