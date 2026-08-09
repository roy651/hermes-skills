# memory-dreamer

Weekly memory hygiene ("dreaming") for the **holographic** fact store. One bounded pass:
resolve contradictions, then merge near-duplicates.

**This skill does not capture facts.** Capture is inline — the agent calls
`fact_store(action='add')` during normal conversation as things come up. Auto-extraction at
session end is deliberately off (it runs a per-session LLM pass). Your only job is hygiene on
what has accumulated.

## Pass 1 — Contradictions

1. Call `fact_store` with `action='contradict'`, `limit=10`. It returns fact pairs that share
   entities but diverge in content, ranked by `contradiction_score` (higher = more conflicting).
2. If it returns nothing, skip to Pass 2.
3. For each pair, decide which case it is:
   - **Superseded** — one fact is a newer, corrected version of the other (a changed preference,
     a fixed setting, a moved path). `remove` the stale one. Prefer the fact with the later
     `updated_at`/`created_at`, but read the content — recency is evidence, not proof.
   - **Both true** — they only *look* conflicting because they share an entity (e.g. two
     different projects on the same machine). Leave both. Optionally `update` one with a
     `trust_delta` of `+0.1` if using it confirmed it.
   - **Genuinely unresolvable** — you cannot tell which is current. Leave both and lower the
     less-likely one with `trust_delta: -0.1` so retrieval ranks it below. Never guess-delete.
4. The detector is heuristic (entity overlap × content divergence). A high score is a *candidate*,
   not a verdict — some pairs will be false positives. Confirm by reading both facts.

## Pass 2 — Near-duplicates

Exact duplicates cannot exist (the store enforces a UNIQUE constraint on content), so only
semantic redundancy needs attention.

5. For each category — `user_pref`, `project`, `tool`, `general` — call `fact_store` with
   `action='list'`, that `category`, and `limit=25`. That cap keeps the pass bounded; do not
   raise it.
6. Within a category, look for:
   - **Redundancy** — two facts stating the same thing in different words → `update` the better
     one to the clearest phrasing, then `remove` the other.
   - **Fragment pairs** — one fact states a problem, another its resolution → `update` the first
     into a single fact carrying both cause and fix, then `remove` the second.
7. Be conservative. Merge only when the overlap is unmistakable; when in doubt, leave both. A
   spurious merge destroys information, while a missed one costs nothing but a row.

## Guardrails

- Stay inside the pass. Do not add facts, re-read transcripts, or chase anything the two passes
  did not surface.
- `remove` is permanent. When the choice is between removing and lowering trust, lower trust —
  retrieval already demotes low-trust facts, so a wrong fact that merely sinks is recoverable.
- Cap the whole run at roughly 25 `fact_store` calls. If the store is too large to finish, do the
  contradictions and stop — they matter more than tidiness.

## Delivery

8. Compose a brief summary of what changed:
   ```
   🧠 *Memory update*
   • contradictions: <n> resolved (<one-line example>)
   • duplicates: <n> merged (<one-line example>)
   ```
   Maximum 5 lines. If nothing was resolved or merged, respond with [SILENT].
