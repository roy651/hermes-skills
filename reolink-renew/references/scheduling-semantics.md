# Reolink renewal cycle & buffer-drift analysis

Session-derived notes on *how the Reolink Basic Plan cycle behaves* and *how the rolling
3-reminder buffer drifts*, so a future run tuning the schedule doesn't have to re-derive it.

## The plan cycle is calendar-monthly (fixed day-of-month), not 30-day rolling

Data point (2026-07-05): renewed **Jul-5 → new expiry Aug-5**. Same day-of-month, +1 calendar
month. A 30-day cycle would have landed on **Aug-4**, so 30-day is ruled out. Model the cycle as
"renew on the Nth → expires the Nth of next month." The `+1 month` (fixed-DOM) buffer chain matches
this, which is why the buffer stays aligned.

## RESOLVED (2026-08-04) — an early (still-active) renewal STACKS from expiry

Measured on the Aug-4 active-state renewal: plan was **active with expiry Aug-5**; renewed on **Aug-4**
→ new expiry **Sep-5**. That is `old_expiry + 1 month` (**stack-from-expiry**), NOT `renewal_date + 1 month`
(reset-from-today would have given Sep-4). So:

- **Stack-from-expiry CONFIRMED.** Renewing while active keeps the remaining sliver and adds a month to
  the *current expiry*. Expiry stays on a fixed day-of-month; the `+1 month` fixed-DOM buffer chain stays
  perfectly aligned and the lead margin does **not** erode. The margin-erosion drift below is therefore
  **not a real risk** under Reolink's actual semantics — the lead re-anchor is kept only as a cheap
  belt-and-suspenders (it's a no-op when already aligned).

Data points so far: Jul-5 (from expired) → Aug-5; Aug-4 (from active, expiry Aug-5) → Sep-5.

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
