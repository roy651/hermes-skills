# Wyckoff Event Detection — Glossary & Thresholds

Documents the programmatic detectors in `scripts/events.py`. These are deliberately
conservative heuristics on daily OHLCV — they flag *candidate* structures for the LLM and
the STRONG gate, not certainties. Recalibrate the constants here when behavior drifts; the
calibration CLI is `python scripts/events.py TICKER [days]`.

## Trading range (`detect_range`)

A horizontal base in the recent window. Constants:

| Constant | Value | Meaning |
|---|---|---|
| `RANGE_LOOKBACK` | 60 | bars examined for the range |
| `RANGE_MAX_WIDTH` | 0.20 | max band-to-band spread; wider ⇒ not horizontal |
| `RANGE_BAND_Q` | 0.10 | support/resistance percentile (10th low / 90th high) |
| `TOUCH_TOL` | 0.02 | within 2% of a band counts as a touch |
| `MIN_TOUCHES` | 3 | touches required of **each** of support and resistance |

`support = lows.quantile(0.10)`, `resistance = highs.quantile(0.90)`, `mid = midpoint`.
Percentile *bands* (not absolute min/max) are used deliberately: the Spring is the lowest
low and an Upthrust the highest high, so if support were `low.min()` a Spring could never
sit *below* support and would be undetectable. Returns bounds, width, duration, touch
counts. All later events require a range first.

## Spring (`detect_events` → `spring`)

A shakeout: price pierces below support and closes back above it the same bar.

- `low < support * SPRING_PIERCE` (0.99) **and** `close > support`
- `confirmed = True` if any of the next 3 bars closes higher than the Spring bar
- Most recent qualifying bar within `EVENT_SCAN` (40) bars is reported.

## SOS — Sign of Strength (`sos`)

A demand surge off the base.

- single-bar gain `(close − prior_close)/prior_close > SOS_GAIN` (0.04)
- on volume `> SOS_VOL_X × 20-day average` (1.5×)
- `close ≥ range mid`
- Most recent qualifying bar within `EVENT_SCAN` bars.

## LPS — Last Point of Support (`lps`)

A quiet higher-low pullback after the SOS — the textbook entry.

- occurs **after** the SOS bar
- `close > range mid`
- volume `< LPS_VOL_X × SOS-bar volume` (0.7×)
- `close` within `LPS_NEAR_SOS` (3%) of the SOS high

## Scoring (`event_summary`)

`score`: 0 = no range, 1 = range only, 2+ = range plus one or more of Spring/SOS/LPS.
`has_entry_event` (the hard Gate D in `weekly.py`) is true iff a Spring, SOS, or LPS exists.
The weekly composite rank uses `event_score / 4` (range + the three events) as one of its
three equal-weighted terms.

## Known limitations

- Fixed 60-bar window; a range older than ~3 months is missed.
- Single-bar SOS/Spring; multi-bar shakeouts or slow SOS advances may be missed.
- No volume-profile or point-and-figure count; purely price/volume thresholds.
- Tuned on liquid US equities/ETFs (daily candles). Thin names may misfire.
