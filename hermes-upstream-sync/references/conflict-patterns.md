# Conflict Resolution Patterns for Hermes-Agent Fork Sync

This file documents recurring merge conflict patterns when rebasing local improvements onto upstream `main`.

## Pattern A: Structural Refactoring + Local Logic (`cron/scheduler.py`)

**Scenario:** Upstream extracts logic into `run_one_job()`, but your local commit has retry-on-timeout logic that was inlined.

**Resolution:**
- Keep the inline retry logic (your improvement)
- Wrap it in the same try/except structure as `run_one_job`
- Preserve the `verbose` logging behavior if your local code used it
- Don't remove the `run_job()` call - the retry wraps it

```python
def _process_job(job: dict) -> bool:
    """Run one due job end-to-end: execute, save, deliver, mark."""
    try:
        try:
            success, output, final_response, error = run_job(job)
        except TimeoutError:
            logger.warning(
                "Job %s timed out on first attempt — retrying once",
                job.get("name", job["id"]),
            )
            success, output, final_response, error = run_job(job)
        
        # Continue with rest of the job (save, deliver, mark)
        ...
    except Exception as e:
        logger.error("Error processing job %s: %s", job['id'], e)
        mark_job_run(job["id"], False, str(e))
        return False
```

**Why:** Your local improvement (retry on timeout) is more valuable than the upstream abstraction. Keep the retry, preserve the structure.

## Pattern B: Dual-Format Display Enhancement (`gateway/run.py`)

**Scenario:** You add prominent display (like bold transcript header), but upstream expects backward-compatibility hidden format.

**Resolution:** Output BOTH formats:
1. **Prominent format** for human visibility (e.g., `**Voice Message Transcript:**`)
2. **Hidden format** for compatibility (e.g., `[The user sent a voice message~ ...]`)

```python
result = await asyncio.to_thread(transcribe_audio, path)
if result["success"]:
    transcript = result["transcript"]
    successful_transcripts.append(transcript)
    # Display transcript prominently for user visibility
    enriched_parts.append(
        f"**Voice Message Transcript:**\n\n{transcript}\n\n---"
    )
    # Also add the hidden format for compatibility
    enriched_parts.append(
        f'[The user sent a voice message~ Here\'s what they said: "{transcript}"]'
    )
```

**Why:** Your enhancement improves UX; the hidden format preserves any code that extracts the transcript. Outputting both is the minimal-cost path forward.

## General Conflict Tips

1. **Read upstream first:** `git show origin/main:<file>` to see what they did
2. **Preserve local intent:** Your improvement (retry, better display) is the goal
3. **Keep both formats** unless upstream explicitly deprecated one
4. **Test the resolve:** `git add` the file, then `git rebase --continue`
5. **Force-push after:** `git push fork HEAD:local/improvements --force` (only if rebase succeeded)
