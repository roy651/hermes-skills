# Wyckoff — design rationale & analytical lens

Companion to `README.md` (which documents *what* the pipelines do). This captures the *why* behind the
design and — more importantly — **the analytical lens** for reading a signal, so a capable model (or a
human) can hold a real discussion about a position rather than just relaying the numbers.

## Core philosophy: the engine decides, the LLM checks
The exit mechanism is **deterministic-first**. An earlier version let an LLM decide the action; it was
inconsistent and prone to plausible-but-wrong calls. Now:
- A **deterministic engine** (`risk` → `deterioration` → `ladder`) makes every call from price/volume rules.
- The **LLM validates** that call — confirms it, or flags a *specific* reason it may be wrong — and is
  **advisory** (it never changes the action). Why: the local proxy LLM is flaky and sometimes unavailable;
  the *discipline* must not depend on it. The LLM's edge is contextual judgment (artifacts, catalysts),
  not arithmetic — so that is the only job it's given.
- Flag sensitivity is currently **sensitive** (~half the book flagged); raise it to strong-only if flags
  get noisy. Both knobs (sensitivity, advisory) are deliberate choices, not accidents.

## Why the ladder is shaped the way it is
- **Absolute-share targets, not "trim 25% of current."** A percent-of-current rule never converges
  (25% of 25% of 25%… never reaches zero and re-fires every week). Targets are a % of a fixed
  `baseline_qty`, so a repeated signal lands on the *same* number and the action becomes HOLD once you've
  acted. This is the single most important correctness property.
- **`max_stage` ratchets (down only).** Once trimmed to stage 2, a one-week score dip can't tell you to
  buy back. Exits are one-way until you re-commit (an *add* resets the baseline).
- **Score → stage:** 3–4 → 75% · 5–6 → 50% · 7+/stop-hit → exit. A hard stop is an unconditional exit
  regardless of score — price is the final arbiter.
- **Structural cap vs markdown floor (the calibration that matters most):**
  - A **bleed with no distribution top** (range-less, only computable criteria) is capped at a 25% trim —
    the trailing stop does the rest. Don't dump a name into a hole on momentum criteria alone.
  - A **confirmed distribution top** (`has_structural`: UT/UTAD/SOW/LPSY/support-break) *uncaps* the ladder —
    a real top earns a real scale-out (50%+).
  - The **`established_markdown` floor** lifts a *confirmed, still-falling, materially-underwater* loser
    (below MA + fresh lows + down >8%) to at least a 25% trim, so beaten-down names aren't silently held.
    The >8% threshold keeps shallow basers (a −4% consolidation) from being trimmed.
- **Concentration cap (20%) and the core exemption.** Over-cap tactical names trim toward 20% regardless of
  Wyckoff — this is how an over-weight (XFIV) gets diluted. **DGRO is the lone core hold** — exempt from
  scale-out and the cap (a strategic dividend compounder, not a tactical trade).
- **The add pathway** fires only on a *clean* name (0/9) with a fresh confirmed entry event, building toward
  a half (~10%) position — not the full cap — to honour "spread unless conviction is high."

## The analytical lens — how to read a single signal
When discussing a position, reason in this order (this is the lens the human review used):
1. **Is the score legit or an artifact?** A big "distribution-volume" / "support-break" day is often an
   **ex-dividend** drop (high-yield names like PFE/DGRO), an **index rebalance** (index ETFs like TCH-F3), a
   **split**, or a **thin-volume** glitch (small ILS names). Check the catalysts/headlines before trusting it.
2. **Structural top vs a bleed.** `has_structural` true = a real Wyckoff top (act with conviction). Only
   computable criteria = momentum weakness; lighter touch, let the stop work.
3. **Still falling vs basing.** `established_markdown` (fresh lows) = actively breaking down → trim. Off the
   lows on quiet volume = possible re-accumulation → don't sell the bottom; hold with the stop.
4. **Ambiguous? Let the stop arbitrate.** When the engine and a plausible reversal read disagree (a deep
   loser flashing a possible Spring), don't force a trim on a tiny position — hold and let the tight trailing
   stop decide. The stop is the backstop on every name.
5. **Zoom out to the portfolio.** Several names in one sector flagging together (energy: HAL/EXE/EQT) is a
   *sector* signal, not three coincidences — weigh the aggregate exposure, not each name in isolation.
6. **Conviction vs profile.** Adds must respect "spread unless high confidence." An add on what turns out to
   be an ex-div gap (IEMG) is a false setup — verify the "strength" is real before sizing up.

## Calibration history (so we don't relitigate)
- `established_markdown` began as a binary at-loss flag → added a **>8% min-loss threshold** (a −4% baser
  like IFGL was being trimmed; now it isn't).
- `support_break` was range-only → made **range-independent** (a high-volume close below the 20-bar swing
  low) so deep-markdown breakdowns with no base (MBLY-type) register.
- `distribution_volume` → falls back to **price-structure (lower highs + lower lows)** when volume is thin,
  so thinly-traded ILS names (SLARL) aren't under-scored by dead-volume bars.
- The validator was wired to **real Finnhub catalysts** (earnings calendar + 21-day headlines). Ex-dividend
  dates are paid-tier, so the validator infers ex-div from the headlines + the gap shape instead.

## Known gaps / open ideas
- The thin-volume guard keys off the **last 20 bars**; an older dead-volume stretch (AMOT's Apr/May) won't
  flag — the validator catches those.
- The add pathway jumps ~2% → ~10% in one step; a smaller first step is an open option.
- Mechanising more of what the validator catches is the standing direction — when the validator repeatedly
  flags the same pattern, turn it into a deterministic detector so the engine needs the LLM less.

## Relationship to the entry funnel
Entry (`entry.py`) and exit (`exit.py`) are mirror images: entry hunts *accumulation* structure
(Spring/SOS/LPS) to **buy**; exit hunts *distribution* structure (UT/UTAD/SOW/LPSY) to **scale out**. They
share the data / analysis / notify core and the 0–N criteria idea; the entry funnel uses Finnhub news as a
gate, the exit side now uses it to validate. See `README.md` for both pipelines end to end.
