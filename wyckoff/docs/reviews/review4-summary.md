# Wyckoff Signal Integrity — Review 4 Summary

**Context:** decision requested in `docs/reviews/review3-response-2026-06-08.md` — the **quiet-rally distribution-top FP** that survives the effort filter (CVNA 0.89×, PTON 0.63×), with three proposed remedies: (a) re-test-hold confirmation, (b) distribution-after-markup check, (c) accept + document + lean on exit-watch. Agent leaned (a).

**Recommendation:** **do not build (a).** Adopt **(c) as the safety baseline**, and make one **structural** change — give the markup-pullback lane its own *confirm-before-acting* tier rather than full STRONG. Park a *refined* (b) as an optional research item. Rationale and tasks below.

The effort filter (review 3) is correct and stays — it closes the *climactic* FP (SMCI 4.11× rejected). This doc only concerns the residual *quiet-top* FP.

---

## 1. The data that drives the decision

Measured post-peak **bars held above the breakout level** on the committed fixtures:

| Fixture | class | effort (rally÷prior) | mp fires? | bars held ≥ breakout after peak |
|---|---|---|---|---|
| EQIX | healthy | 0.76× | yes | **29, never broke** (in-window) |
| ROK | healthy | 0.75× | yes | 2 (window ends 3 bars past peak — truncated) |
| CVNA quiet-top | **FP** | 0.89× | yes | **16, then broke at +17** |
| CVNA failed-breakout | dist | n/a | no (already rejected) | 0, broke at +1 |
| SMCI climax | dist | 4.11× | no (effort filter) | n/a |

The decisive number: **the quiet-top held above its breakout for 16 sessions — ~3 weeks — looking like a textbook LPS — before it failed.**

---

## 2. Why not (a) — re-test-hold confirmation

Option (a) assumes quiet tops are *first reactions that roll over quickly*. **The committed CVNA fixture disproves that** — it held 16 bars. Consequences:

- To reject a 16-bar hold, (a)'s `N` must exceed ~16 → **~3 weeks of latency on every entry.**
- That same `N` rejects legitimate fast-resuming pullbacks (a large new **false-negative** class) and can't even evaluate ROK (only 2 post-peak bars visible).
- EQIX (29) vs CVNA (16) *does* separate, but it's one sample per class and the bands are close — not a reliable threshold, and operationally too slow regardless.

(a) trades a quiet-top FP for heavy latency + a big FN class, and still might miss slow tops. Don't build it.

## 3. The core finding — the quiet-top FP is largely irreducible *at entry*

At the decision point a quiet-volume distribution top and a healthy LPS are near-inseparable:
- **volume magnitude** can't split them (both ~0.6–0.9×), and
- **time-held-above-breakout** can't either (16 vs 29, with real winners sometimes resolving faster).

The information that distinguishes them — resume vs distribute — genuinely arrives **later**, after the top fails (CVNA broke at +17). This is a property of the pattern, not a detector gap. So the guard belongs where the information actually arrives: the **back end**, not an entry-time filter.

---

## 4. Recommendation

### 4a. Structural — tier the markup-pullback lane separately *(the main change)*
The MP lane bypasses the off-high floor **and** the rel-perf cap, yet currently earns the same **STRONG** label as a complete Spring→SOS→LPS accumulation — while being the highest-FP path in the system, with an FP that's irreducible at entry. Stop conflating the two.

- Emit MP entries under their own tier, e.g. **`MARKUP-PULLBACK — confirm before acting`**, distinct from accumulation STRONG.
- This encodes the standing "confirm-by-eye" guidance into the **output structure**, not the operator's memory — same theme as the whole series (move safety from "operator remembers" into deterministic structure).
- Keeps the lane (it still solves 0-STRONG-near-ATH) without granting it autonomous-STRONG trust, and avoids paying the FN cost of an aggressive entry filter.

### 4b. Baseline safety — adopt (c), and verify the back-end actually catches it
- Keep **50% sizing** (already in place) — bounds the loss on a quiet-top FP.
- **Verify the daily exit-watch** would have flagged CVNA's / PTON's breakdown promptly (see tasks). The whole (c) posture rests on this; if exit-watch lag is large, (c) is weaker than it looks and the tiering in 4a becomes even more important.

### 4c. Optional research — *refined* (b), not the version proposed
Not "detect UTAD/SOW" (fuzzy, own corpus, high FN). Instead a narrow **effort-vs-result** read on the post-breakout window: **up-day vs down-day volume balance** and **close location**, to spot supply emerging while price holds. It *might* tilt CVNA-type tops away from ROK/EQIX. Treat as a measured research item with its own labeled set and an explicit FN budget — **not** a gate to ship until it's shown to separate the corpus without killing winners.

---

## 5. Validation tasks (do these before committing)

1. **Re-cut extended forward windows** for the quiet-top and healthy cases (CVNA quiet-top, PTON, plus ROK/EQIX extended past the peak) so each fixture includes the bars *after* the entry decision. The current snapshots end at/near the decision point, so they can't measure what happens next.
2. **Quantify exit-watch lag:** on those forward windows, how many bars after the LPS does the daily exit-watch raise an exit, and what's the drawdown from entry to that signal? This is the actual cost of a quiet-top FP under (c)+50% sizing. Report it.
3. **Test refined-(b):** compute up/down-volume balance + close-location over the post-breakout window for the four cases; check whether it separates healthy (ROK/EQIX) from quiet-tops (CVNA/PTON) before building anything on it.
4. **Caveat to respect:** current evidence is one fixture per class and ROK's window is truncated — treat the 16-vs-29 separation as suggestive, not robust. Expand the corpus before drawing thresholds.

---

## 6. Acceptance criteria

1. MP entries render under a distinct **confirm-before-acting** tier, visibly separate from accumulation STRONG; accumulation STRONG semantics unchanged.
2. Exit-watch verified against ≥2 quiet-top forward windows (CVNA, PTON), with the entry→exit lag and drawdown reported; 50% sizing retained for the MP tier.
3. Option (a) not implemented; rationale recorded.
4. If refined-(b) is pursued, it ships only with its own labeled corpus and a measured FN cost — never as an unvalidated gate.
