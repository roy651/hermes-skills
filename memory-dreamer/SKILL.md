# memory-dreamer

Nightly memory consolidation ("dreaming"). Two passes: distillation (new facts from sessions) then compaction (keep existing entries clean and non-redundant).

## Pass 1 — Distillation

1. Call `structured_memory list_pending_sessions` to find sessions awaiting distillation.
2. If there are no pending sessions, skip to Pass 2.
3. For each pending session, call `structured_memory read_session` with its session_id.
4. Extract learnings worth preserving:
   - Architectural decisions and their rationale
   - Bug root causes and how they were fixed
   - User preferences or workflow patterns for a specific skill
   - Infrastructure constraints or behaviors discovered
   - Cross-file findings: write to multiple targets, add a one-line cross-reference in each
5. For each learning:
   - Check if an existing entry in the target file already covers this fact. If yes, use `replace` to update it rather than `add`ing a duplicate.
   - If no existing entry covers it, use `add`.
   - Required fields: target (skills/<name> or infra/<name>), type, content, why, apply.
   - If the target file doesn't exist, call `create_file` first with relevant keywords.
6. Be selective — skip small talk, one-off commands, anything obvious from the code or already in memory.
7. Call `structured_memory mark_distilled` on each processed session.

## Pass 2 — Compaction

For each file that received new entries in Pass 1 (or all files if no sessions were processed):

8. Call `structured_memory read` on the file and review all entries together.
9. Apply these rules:
   - **Redundancy**: two entries say the same thing → merge into one improved entry using `replace` on the older one, then `remove` the other.
   - **Superseded**: a newer entry directly contradicts or fully obsoletes an older one → `remove` the older entry.
   - **Bug + fix pair**: one entry describes a bug, another describes the fix → merge into a single entry that captures both the root cause and the resolution.
10. Be conservative — when in doubt, leave both entries. Only compact when the redundancy or contradiction is clear.

## Delivery

11. Compose a brief summary of what changed:
    ```
    🧠 *Memory update*
    • infra/gateway: <one-line summary of what changed>
    • skills/navman: <one-line summary>
    ```
    One line per file that was modified. Maximum 5 lines.
    If nothing was saved or compacted, respond with [SILENT].
