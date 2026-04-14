# memory-dreamer

Nightly memory consolidation ("dreaming"). Distills recent short-term session notes into structured long-term memory.

## Instructions

1. Call `structured_memory list_pending_sessions` to find sessions awaiting distillation.
2. If there are no pending sessions, respond with [SILENT] and stop.
3. For each pending session, call `structured_memory read_session` with its session_id to load the full content.
4. From each session extract learnings worth preserving long-term:
   - Architectural decisions and their rationale
   - Bug root causes and how they were fixed
   - User preferences or workflow patterns for a specific skill
   - Infrastructure constraints or behaviors discovered
   - Cross-file findings (write to multiple targets if relevant, add cross-reference line in each)
5. For each learning, call `structured_memory add` with:
   - target: the appropriate `skills/<name>` or `infra/<name>` file
   - type: feedback | project | reference | user
   - content: the concise fact
   - why: root cause or what led to this discovery
   - apply: when and how to use this in future decisions
   If the target file doesn't exist, call `structured_memory create_file` first with relevant keywords.
6. Be selective — only write what is genuinely useful for future sessions. Skip small talk, one-off commands, and anything already obvious from the code.
7. After processing each session, call `structured_memory mark_distilled` with its session_id.
8. Respond with a brief summary of what was distilled (or [SILENT] if nothing was saved).
