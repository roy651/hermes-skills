# Reolink renewal cycle & buffer-drift analysis

Session-derived notes on *how the Reolink Basic Plan cycle behaves* and *how the rolling
3-reminder buffer drifts*, so a future run tuning the schedule doesn't have to re-derive it.

## The plan cycle is calendar-monthly (fixed day-of-month), not 30-day rolling

Data point (2026-07-05): renewed **Jul-5 → new expiry Aug-5**. Same day-of-month, +1 calendar
month. A 30-day cycle would have landed on **Aug-4**, so 30-day is ruled out. Model the cycle as
"renew on the Nth → expires the Nth of next month." The `+1 month` (fixed-DOM) buffer chain matches
this, which is why the buffer stays aligned.

## OPEN QUESTION — does an early (still-active) renewal stack or reset?

The only renewal we've *measured* was from an **expired** state (lapsed Jul-4, renewed Jul-5 → Aug-5).
When expired there's nothing to stack onto, so it necessarily anchored to the renewal date. We have
**not** observed a renewal while the plan is still active. Two possible semantics:

- **Stack-from-expiry** (common): renewing while active *adds* a month to the current expiry
  (keep remaining days) → expiry moves to next-month-same-DOM, DOM stays put. Buffer stays perfectly aligned.
- **Reset-from-renewal-date**: new expiry = renewal_date + 1 month; the sliver of remaining days is lost.

**Confirm empirically on the next active-state renewal** (the Aug-4 run): note the reported EXPIRY vs.
what each model predicts, and record the answer here. It decides whether the buffer needs the
lead-realign fix below or can trust the +1-month chain.

## The margin-erosion drift (why the lead slot must realign to EXPIRY−1)

If Reolink is reset-from-renewal-date, the "renew 1 day early" margin erodes to zero after one cycle:

```
seed:  expiry Aug-5, lead reminder Aug-4  (1 day early ✓)
Aug-4 fires → renew Aug-4 → new expiry Sep-4
       next buffered slot is Sep-4 = expiry DAY, not Sep-3  (margin gone)
Sep-4 fires → renew → Oct-4 …  (stable at firing ON expiry day)
```

The old top-up seeded slots 2 & 3 at `L + 1 month`, so the lead converged onto expiry day. The 2-day
renewal window still fires it, and a miss is still caught by next month's slot, so it's not
catastrophic — but it defeats the "renew a day early" intent, and the 3-day re-anchor is too loose to
correct a 1-day slip. **Fix:** after every renewal, re-derive the lead slot from the freshly reported
`EXPIRY − 1` rather than trusting the chain for the front of the queue.

## Why the buffer does NOT spawn a duplicate same-month set ("Aug-4 and Aug-2")

- Top-up **extends forward from the latest existing slot** (`L + 1 month`, fixed DOM) and never
  re-creates the *earliest* slot while any future slot exists → appends one, never regenerates a set.
- The **only** path that changes existing dates is the re-anchor, which **deletes all future
  `reolink-renew-*` first, then reseeds** → replaces, can't duplicate.
- Dated names (`reolink-renew-YYYY-MM-DD`) make "create a slot that already exists" idempotent —
  but note `hermes cron create` does **not** enforce unique names; idempotency relies on the algorithm
  reading existing names first (step 1) and only adding missing ones. A mis-count could still dup.

**Real caveat:** after a long outage several slots go past-due; those stale past-dated one-shots are
excluded from the future-count (so no dup) but **linger in the list** and may fire late on catch-up.
Hence the "delete any past-due `reolink-renew-*`" cleanup step in the top-up.
