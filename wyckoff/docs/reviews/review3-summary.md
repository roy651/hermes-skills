# Wyckoff Signal Integrity — Review 3 Summary

**Context:** review of the completed review-2 work (`docs/reviews/review2-response-2026-06-08.md`): Option 2 markup-pullback lane, `digest.py` consolidation, Tier 1 boundary corpus, Tier 2 real fixtures.
**Verdict:** execution is strong and the reporting integrity is high (you flagged the Tier 2 labels as proposed rather than passing them off as ground truth — correct). Tier 1 is legitimate, non-circular threshold testing. **One concentrated risk remains, and it is now empirically proven:** the markup-pullback lane false-fires on a buying-climax top. Details, the fix, new controls, and the two real-window gaps below.

This doc covers the two things requested: (1) an **independent re-label of all six committed fixtures**, and (2) **proposed + verifiable real windows** for the climax and failed-breakout gaps — plus the empirical FP proof that motivates them.

---

## 1. Independent re-labeling of the six Tier-2 fixtures

Labels below were derived from **independent structural metrics** (% off 52w high, MA position, max drawdown, range/effort), not from the detector's output — that's what breaks the Tier-2 circularity. Detector output shown only for comparison.

| Fixture | off-high | maxDD | >50d / >200d | range/spr/sos/lps/mp/HAS | **Independent label** | Agent's proposed | Verdict |
|---|---|---|---|---|---|---|---|
| ROK | 4.6% | 18.7% | T / T | 0 0 0 0 **1** 1 | markup_pullback (healthy) | markup_pullback | ✅ agree |
| EQIX | 4.2% | 19.6% | T / T | 0 0 0 0 **1** 1 | markup_pullback (healthy) | markup_pullback | ✅ agree |
| LLY | 3.0% | 23.6% | T / T | 0 0 0 0 0 0 | clear_not (extended at highs, no pullback) | clear_not | ✅ agree |
| NKE | 46.4% | 46.2% | F / F | 0 0 0 0 0 0 | clear_not (broken markdown) | clear_not | ✅ agree |
| TDG | 23.7% | 25.3% | T / F | **1 1** 0 0 0 0 | accumulation_unconfirmed (lone Spring) | accumulation_unconfirmed | ✅ agree |
| EIX | 3.8% | 10.4% | T / T | **1 1** 0 0 0 0 | accumulation_unconfirmed (lone Spring) | accumulation_unconfirmed | ✅ agree |

**All six independent labels match the proposed labels — sign off as ground truth.** Two notes:
- ROK/EQIX were further verified by volume profile: the rallies into their peaks ran at **0.67× / 0.74×** the prior-60d average with no sustained climax — genuinely healthy pullbacks, not blow-offs.
- LLY and NKE are useful *bonus* negative controls you already have: LLY is a real `mp_extended` case (near-high, no pullback → mp correctly `None`), NKE a real broken-chart `clear_not`. EIX's range sits only 3.8% off the high — a near-high consolidation; harmless here (`has=False`) but the kind of near-high "range" that can be distribution, worth keeping an eye on as the range detector evolves.

---

## 2. Empirical proof: the MP lane false-fires on a climax top

I built three synthetic patterns and ran `detect_markup_pullback` on each. All three present the same *geometry* (breakout cleared, 3–15% pullback, holding above the breakout level on lighter volume); they differ only in the **volume of the rally into the peak** and whether the pullback held above the breakout.

| Pattern | rally-leg vol ÷ prior baseline | `detect_markup_pullback` | Correct? |
|---|---|---|---|
| climax → pullback (holds above breakout) | **4.61×** (climactic) | **FIRES** | ❌ false positive |
| quiet markup → pullback | 0.95× | FIRES | ✅ true positive |
| breakout → collapse back below level | 4.61× | `None` | ✅ true negative |

Read-out:
- The lane **cannot distinguish a healthy mid-markup pullback from a post-buying-climax first reaction** — both fire. This is the classic Wyckoff trap (buying the first dip after a climactic top that's actually the onset of distribution), and the prescreen bypass of the rel-perf cap means strong/parabolic names reach the lane.
- The lane **already handles the obvious failed breakout** (price back below the level → `None`), so that specific gap is partly covered; the dangerous failed case is the one that holds *marginally* above, which is the same signature as the climax FP above.
- **The discriminator is rally-leg volume vs the prior baseline:** climax 4.61× vs healthy 0.95× vs real ROK/EQIX 0.67–0.74×. A single high-volume *thrust* bar is fine (ROK had a 2.5× breakout day inside an otherwise-0.67× rally) — so the filter must key off the **rally-leg average**, not a single-bar spike.

This week's two live STRONGs (ROK, EQIX) are clean by this measure — but that's the data, not the code. Until the filter exists, every MP-lane STRONG should be treated as "confirm by eye," not auto-act.

---

## 3. Fix — add an effort filter to `detect_markup_pullback`

Reject (or flag) a markup-pullback when the breakout rally into the peak carries **climactic / expanding** volume relative to the prior base. Validated thresholds below cleanly separate the must-pass from the must-reject set.

```python
MP_EFFORT_X = 1.5   # rally-leg avg volume must NOT exceed this × the prior-base avg
                    # (climactic markup → distribution risk, not a healthy re-accumulation)

# inside detect_markup_pullback, after rally_vol is computed and before returning the LPS:
prior_vol = float(vol[lb_start:recent_start].mean())   # ceiling-establishing window
if prior_vol > 0 and rally_vol > MP_EFFORT_X * prior_vol:
    return None    # blow-off / climactic advance — not a markup-pullback entry
```

**Must-pass / must-reject (use as the acceptance set):**

| Case | rally ÷ prior | with `MP_EFFORT_X=1.5` |
|---|---|---|
| ROK (real) | 0.67× | pass ✅ |
| EQIX (real) | 0.74× | pass ✅ |
| quiet synthetic | 0.95× | pass ✅ |
| climax synthetic | 4.61× | **reject ✅** |

Caveat: `MP_EFFORT_X=1.5` is set from these four points + the synthetic controls; treat it as a guardrail and re-tune once the real climax fixture (§4) is committed. Keep targeting the rally-leg *average*, not a single bar, so a legitimate breakout thrust isn't penalized.

---

## 4. Two new controls + the real-window gaps

### 4a. Synthetic controls — add to Tier 1 now (built and verified here)
These are immediately committable and deterministic. The builder (same shape used for the proof above):

```python
def _mp_pattern(climax: bool, fail: bool = False):
    """Breakout above a ~100 ceiling to a ~115 peak, then a pullback.
       climax=True → climactic (expanding) rally volume; fail=True → pulls back below the breakout."""
    rows = []
    for i in range(60):  p = 80 + 20*(i/59); rows.append(_bar(p, p+0.5, p-0.5, p, 1_000_000))   # run to 100
    for i in range(60):  p = 100 - 5*(i/59); rows.append(_bar(p, p+0.5, p-0.5, p,   900_000))   # ease to 95
    rv = 2_600_000 if climax else 850_000
    for k in range(30):
        p = 95 + (115-95)*((k+1)/30)
        rows.append(_bar(p, p+0.6, p-0.4, p, int(rv*(1+0.05*k)) if climax else rv))
    end = 96 if fail else 108
    for k in range(12):
        p = 115 + (end-115)*((k+1)/12)
        rows.append(_bar(p, p+0.4, p-0.4, p, 600_000))
    return _df(rows)
```

| New fixture | builder | expected (after §3 fix) | class |
|---|---|---|---|
| `mp_climax` | `_mp_pattern(climax=True)` | `mp=False, has=False` | **dist** (currently FIRES — guards the FP) |
| `mp_failed_below` | `_mp_pattern(climax=True, fail=True)` | `mp=False, has=False` | dist |

Add `mp_climax` to the **`dist` class** so the zero-FP-on-`dist` gate enforces it. **Before §3 lands it documents the failure (FIRES); after §3 it must flip to `None`.** That single fixture is the regression guard for this whole issue.

### 4b. Real windows — procedure for the mini-PC (this sandbox can't fetch market data)
Finnhub isn't reachable here, and `build_fixtures.py` only snapshots the trailing 252 days, so it can't capture a historical climax. Two steps on the box:

1. **Extend the fetcher** to accept an explicit date range, e.g. `fetch_ohlcv(ticker, start=..., end=...)`, and give `build_fixtures.py` a `(ticker, start, end, name)` list so historical windows can be frozen.
2. **Find the windows by screen, not by memory** (more robust than hand-picking tickers): run `detect_markup_pullback` across your historical universe and collect cases where it fires **and** `rally_vol / prior_vol > 1.5` (climactic) **and** price subsequently closed back below the breakout level within ~6–8 weeks. Those are *confirmed* historical FPs — freeze the earliest qualifying 252-bar window of each as a fixture, labeled `mp=False/has=False` once §3 is in.
   - Candidate starting points to verify first (well-known blow-off → first-pullback episodes; confirm each against the screen before committing — do **not** trust the label without the volume check): a 2024 AI/semiconductor parabolic name, and a 2021 meme/▲high-short-interest spike. Whichever passes the screen becomes the real `mp_climax_real` fixture.
   - For the **failed-breakout** gap, screen for a clean breakout above a multi-month ceiling that closed back below it within ~4 weeks; freeze as `failed_breakout_real` (`has=False`).

Each committed real fixture gets a one-line rationale and a **human-set** label (from the chart, independent of the detector) so Tier 2 becomes a real FP/FN test rather than a regression of detector-vs-itself.

---

## 5. Decisions you asked about (carried from review 2)

- **`MP_PULLBACK_MAX=0.15`, `MP_PRESCREEN_CAP=10`:** keep as guardrails; don't calibrate by feel. One change worth making: the cap currently admits the **10 shallowest** pullbacks, which biases toward names nearest their highs — exactly where post-climax first-pullbacks live. Once the effort filter (§3) is in, rank admissions by **pullback quality** (effort filter margin, bars holding above breakout) rather than raw shallowness.
- **50% sizing for markup-pullback entries:** endorse. A pullback entry without a full Spring→SOS→LPS base is lower-information; half size is right. The real control is the FP filter, not the size.
- **MP lane as the primary STRONG source near ATHs:** acceptable as regime behavior — near all-time highs, confirmed-breakout pullbacks genuinely are where entries are. But it's currently the *whole* high-conviction output through the least-guarded path, so gate trust in it behind §3 + the §4 controls. Surface MP STRONGs in the meantime; treat them as confirm-by-eye until the filter lands.

---

## 6. Acceptance criteria for this round

1. `detect_markup_pullback` returns `None` for `mp_climax` (and the real climax fixture) after §3; still fires for ROK/EQIX/quiet. Tier-1 zero-FP-on-`dist` gate stays green with `mp_climax` added to that class.
2. `build_fixtures.py` supports an explicit date range; at least one real climax fixture and one real failed-breakout fixture committed, each with a human-set label.
3. MP admissions ranked by quality rather than shallowness once §3 is in.
4. Tier-2 harness reports a `has_entry_event` confusion matrix that now includes the adversarial positives — target remains **zero false positives on the dist / clear_not classes**.
