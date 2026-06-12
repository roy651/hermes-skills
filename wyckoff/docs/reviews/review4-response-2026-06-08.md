# Response to Review 4 (`review4-summary.md`)

**Date:** 2026-06-08 · merged to `main`, deployed.
**Bottom line:** your pushback is correct on every point — I've adopted the recommendation in
full. The markup lane now has its own confirm-before-acting tier (4a), (c) is the baseline and I
**measured** that the exit-watch actually catches the FP promptly (4b), refined-(b) was probed and
does **not** separate (so not built), and option (a) is recorded as rejected with rationale.

Notably, the CVNA fixture I committed in review 3 is what disproved my own (a) proposal — the
quiet top held 16 bars above its breakout, so an entry-time hold-filter would need ~3 weeks of
latency. Point taken.

## Acceptance (§6)

| # | Criterion | Status |
|---|---|---|
| 1 | MP entries render under a distinct confirm-before-acting tier, separate from accumulation STRONG | ✅ |
| 2 | Exit-watch verified on ≥2 quiet-top windows; entry→exit lag + drawdown reported; 50% sizing kept | ✅ |
| 3 | Option (a) not implemented; rationale recorded | ✅ |
| 4 | refined-(b) not shipped as a gate (probed first; doesn't separate) | ✅ |

## §4a — separate MARKUP-PULLBACK tier *(the structural change)*
The weekly digest now emits three tiers (live dry-run this week: `0 STRONG, 5 MARKUP-PULLBACK, 0 BORDERLINE`):

```
🟢 STRONG — accumulation confirmed (n)      ← range → Spring → SOS → LPS only
🟣 MARKUP-PULLBACK — confirm before acting (n)   ← the MP lane, 50% size, with the caveat inline
🟡 BORDERLINE (n)
```

A pick is markup-tiered when its entry event is the markup-pullback LPS and **not** a range
SOS/LPS (`is_markup` on the bundle). Accumulation STRONG semantics are unchanged. The
"confirm-by-eye" guidance is now encoded in the **output**, not the operator's memory — the theme
of the whole series.

## §5.2 — exit-watch actually catches the quiet-top FP (measured)
`tests/measure_exit_lag.py` walks the real daily exit-watch forward from each entry:

| case | entry | breakout | det. breakout-stop | exit-watch signal | dd at signal | eventual maxDD |
|---|---|---|---|---|---|---|
| **CVNA** quiet-top | 2021-09-15 | 65.82 | **+2 bars** | **+0 bars** (reduce/sell) | −3.8% (still green) | 58.9% |
| **PTON** quiet-top | 2021-02-15 | 139.75 | **+1 bar** | **+0 bars** | +5.7% | 46.6% |
| **EQIX** healthy | 2026-06-01 | 992.90 | never (held) | +0 bars | −2.0% (green) | −2.0% (rose) |

Read-out:
- **The exit-watch flags both quiet-tops immediately (lag 0), before any real loss** — vs eventual
  drawdowns of ~47–59%. Under (c) + 50% sizing the realized loss is small. A mechanical "stop at
  the breakout level" also exits in 1–2 bars. **So the back-end genuinely contains this FP** — and
  this *also* makes option (a) redundant, not just slow.
- **Caveat (honest):** the exit-watch *also* fired on the healthy EQIX at entry. The exit prompt
  reads a near-high pullback as distribution risk, so it bounds downside but is **not a selective
  discriminator** — leaning on it auto-exits healthy markup pullbacks too. That bluntness is
  exactly why the lane belongs in a **human confirm-before-acting tier** (4a), not auto-trade.

## §5.3 — refined-(b) probed first; it does NOT separate → not built
`tests/probe_refined_b.py`, post-breakout window:

| | up/down vol | close location |
|---|---|---|
| healthy ROK / EQIX | 0.83 / 0.93 | 0.54 / 0.40 |
| quiet-top CVNA / PTON | 1.19 / 1.23 | 0.51 / 0.54 |

Close-location **overlaps entirely**; up/down-volume separates only weakly and in the
*counter-intuitive* direction (tops show more up-volume). On 2 samples/class that's noise, not a
signal. Per §6.4 I did not build a gate on it. If you want it pursued, it needs its own labeled
corpus and a measured FN budget first — the probe says it isn't promising.

## §6.3 — option (a) rejected, rationale recorded
Not implemented. The committed `CVNA_quiettop_210915` fixture holds 16 bars above the breakout
before failing, so (a)'s `N` would need >16 → ~3 weeks latency on every entry, would reject
fast-resuming winners (a large new FN class), and can't even evaluate short windows (ROK had 2
post-peak bars). And §5.2 shows the exit-watch already catches the FP at lag 0, so (a) is
redundant on top of being slow. Effort filter (review 3) stays — it closes the *climactic* FP.

## Net posture for the markup lane (encoded, not remembered)
Entry filters: regime off-high floor (bypassed for MP), effort filter (climax), quality-ranked
admission cap. Output: **own confirm-before-acting tier, 50% size, caveat inline.** Back-end:
exit-watch catches the residual quiet-top FP at lag 0 (measured). The one genuinely irreducible
gap — telling a quiet top from a healthy LPS *at entry* — is now handled by a human, by design.
