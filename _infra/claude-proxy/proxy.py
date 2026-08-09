"""
Claude Code Proxy — OpenAI-compatible /v1/chat/completions endpoint.

Routes requests to:
  - Claude Code CLI  (model starts with "claude")
  - OpenRouter       (everything else, forwarded transparently)

Session management:
  - Maintains one persistent Claude session per conversation.
  - Resumes via --resume <session_id> so only the new user message is sent
    each turn (no full-history replay, no extended-thinking blowup).
  - Session is reset when: model changes, message count drops (new conversation),
    or it's the first message.

Hermes config to use this proxy:
  model:
    default: claude-code
    provider: custom
    base_url: http://localhost:8765/v1
    api_mode: chat_completions
"""
import json
import logging
import contextlib
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, request, jsonify
import requests

load_dotenv(Path(__file__).parent / ".env")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
PROXY_PORT = int(os.environ.get("PROXY_PORT", 8765))
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
FALLBACK_MODEL = os.environ.get("FALLBACK_MODEL", "qwen/qwen3.5-flash-02-23")

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path(__file__).parent / "logs" / "proxy.log"),
    ],
)
log = logging.getLogger(__name__)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Session state — one Claude session PER CONVERSATION (so resume/cache survives)
# ---------------------------------------------------------------------------
# Keyed by conversation (see _conv_key): the agent (gateway, sends `tools`) keeps a persistent,
# resumable session so each turn ships only the new message; one-shot callers (wyckoff) never get a
# stored session and so can't evict the agent's. Sessions deliberately survive a fallback — a transient
# claude failure must not force the next turn into a full fresh re-send (the whole point of the cache).
_sessions: dict[str, dict] = {}   # key -> {"id","model","msg_count"}
# Running claude subprocesses, keyed by pid -> start time. The original code kept ONE global
# slot and killed it whenever a new request arrived, as stuck-process cleanup. That is
# incompatible with concurrency by construction: two legitimate parallel calls would SIGKILL
# each other. Track them all instead and reap only genuinely stuck ones by age.
_procs: dict[int, tuple[subprocess.Popen, float]] = {}
_procs_lock = threading.Lock()
STUCK_AFTER_SEC = 330            # communicate() already times out at 300s; older than this is stuck

# Bound concurrency rather than forbidding it. One-shot callers (wyckoff) are independent and
# safe to run in parallel; a RESUMED session is not — it must never race itself — so those are
# additionally serialised per conversation key below.
MAX_CONCURRENT_CLAUDE = int(os.environ.get("MAX_CONCURRENT_CLAUDE", "3"))
_claude_slots = threading.Semaphore(MAX_CONCURRENT_CLAUDE)
_session_locks: dict[str, threading.Lock] = {}
_session_locks_guard = threading.Lock()


def _session_lock(key: str | None) -> threading.Lock | None:
    """Per-conversation lock. None for one-shots — they hold no session state to corrupt."""
    if not key:
        return None
    with _session_locks_guard:
        return _session_locks.setdefault(key, threading.Lock())


def _reap_stuck_procs() -> None:
    now = time.monotonic()
    with _procs_lock:
        for pid, (proc, started) in list(_procs.items()):
            if proc.poll() is not None:
                _procs.pop(pid, None)
            elif now - started > STUCK_AFTER_SEC:
                log.warning(f"claude: reaping stuck subprocess pid={pid} "
                            f"age={now - started:.0f}s")
                proc.kill()
                _procs.pop(pid, None)

# Prepended to any reply NOT served by Claude Code (i.e. a paid API path: OpenRouter now, the Anthropic
# API layer later) so the user can see at a glance when a message is burning metered tokens.
NONCC_MARKER = os.environ.get("NONCC_MARKER", "💸")


def _conv_key(data: dict) -> str | None:
    """Stable per-conversation session key, or None for one-shots (which never benefit from resume).
    The agent/gateway sends `tools`; one-shot callers (wyckoff) don't — so `tools` is the signal. The
    msg_count guard in chat_completions resets the session when a genuinely new conversation starts."""
    return "agent" if data.get("tools") else None


def _mark_openai_json(payload: dict) -> None:
    """Prefix the paid-tokens marker onto a non-streaming OpenAI reply's text (skips tool-call replies)."""
    try:
        msg = payload["choices"][0]["message"]
        if msg.get("content"):
            msg["content"] = f"{NONCC_MARKER} {msg['content']}"
    except (KeyError, IndexError, TypeError):
        pass


def _mark_stream(upstream):
    """Lead a streamed (passthrough) reply with a paid-tokens marker chunk, then stream it unchanged."""
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    lead = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": "fallback",
            "choices": [{"index": 0, "delta": {"content": NONCC_MARKER + " "}, "finish_reason": None}]}
    yield ("data: " + json.dumps(lead) + "\n\n").encode()
    for chunk in upstream.iter_content(chunk_size=None):
        yield chunk
_last_result: dict | None = None  # raw JSON from last Claude Code invocation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return str(content or "")


def _last_user_message(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return _extract_text(msg.get("content", ""))
    return ""


def _messages_to_prompt(messages: list[dict]) -> str:
    """Full history → single prompt string (used only for fresh sessions)."""
    system = ""
    turns = []

    for msg in messages:
        role = msg.get("role", "")
        content = _extract_text(msg.get("content", ""))
        if not content:
            continue
        if role == "system":
            system = content
        elif role == "user":
            turns.append(("Human", content))
        elif role == "assistant":
            turns.append(("Assistant", content))

    if not turns:
        return system

    if len(turns) == 1 and turns[0][0] == "Human":
        user_msg = turns[0][1]
        if system:
            return f"[Context: {system[:400]}]\n\n{user_msg}"
        return user_msg

    parts = []
    if system:
        parts.append(f"[Context: {system[:400]}]")
    for role, content in turns:
        parts.append(f"{role}: {content}")
    return "\n\n".join(parts)


def _call_claude(messages: list[dict], resume_id: str | None = None) -> tuple[str, str | None, str | None]:
    """
    Invoke claude CLI. Returns (response_text, session_id, model_used).

    Fresh session: pass full reconstructed prompt.
    Resumed session: pass only the latest user message — Claude already has
    the prior context in its persisted session.

    Kills any previously running claude subprocess before starting a new one,
    preventing pile-up when the gateway retries after a timeout.
    """
    _reap_stuck_procs()

    # The prompt is passed on stdin, never as an argv element: a full reconstructed
    # conversation can be tens of KB and would blow past ARG_MAX, making the exec fail
    # with "[Errno 7] Argument list too long" (which surfaced as 503s after a restart
    # cleared the session cache and forced full prompts down the fresh path).
    if resume_id:
        prompt = _last_user_message(messages)
        cmd = [CLAUDE_BIN, "--print", "--dangerously-skip-permissions",
               "--output-format", "json", "--resume", resume_id]
        log.info(f"claude: resume={resume_id}  new_msg_len={len(prompt)}")
    else:
        prompt = _messages_to_prompt(messages)
        cmd = [CLAUDE_BIN, "--print", "--dangerously-skip-permissions",
               "--output-format", "json"]
        log.info(f"claude: fresh session  prompt_len={len(prompt)}")

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    with _procs_lock:
        _procs[proc.pid] = (proc, time.monotonic())

    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=300)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise RuntimeError("claude timed out after 300s")
    finally:
        with _procs_lock:
            _procs.pop(proc.pid, None)

    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {stderr[:300]}")

    try:
        global _last_result
        data = json.loads(stdout)
        _last_result = data
        # Use "result" or "content" if the key exists (even if empty); only fall
        # back to raw stdout for non-standard formats that lack both keys.
        if "result" in data:
            text = data["result"] or ""
        elif "content" in data:
            text = data["content"] or ""
        else:
            text = stdout.strip()
        session_id = data.get("session_id")
        # The CLI reports every model it touched under modelUsage — the main model
        # (e.g. claude-opus-5) AND, intermittently, a tiny background/housekeeping model
        # (claude-haiku-*, for topic classification etc.). Pick the model that ingested the most
        # prompt: the main model always takes the full system prompt + conversation, while the
        # background model gets a tiny sub-prompt. (Output tokens would misfire on a short reply,
        # where a housekeeping call can out-token the actual answer.)
        # Cached tokens MUST be counted: `inputTokens` excludes cache reads, so on a warm cache the
        # main model reports inputTokens=2 against cacheRead=32761 and loses to the uncached haiku
        # call — which silently mislabelled every batch run as haiku and blinded the DEGRADED banner.
        mu = data.get("modelUsage") or {}

        def _prompt_size(m: str) -> int:
            u = mu[m] or {}
            return (u.get("inputTokens", 0) + u.get("cacheReadInputTokens", 0)
                    + u.get("cacheCreationInputTokens", 0))

        model_used = max(mu, key=_prompt_size, default=None)
    except (json.JSONDecodeError, AttributeError):
        text = stdout.strip()
        session_id = None
        model_used = None

    log.info(f"claude: response_len={len(text)}  session_id={session_id}  model={model_used}")
    return text, session_id, model_used


def _openai_response(content: str, model: str) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _stream_response(content: str, model: str):
    """Yield SSE chunks for a complete response (hermes always requests stream=True)."""
    from flask import Response
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    now = int(time.time())

    def generate():
        # First chunk: role + content
        yield "data: " + json.dumps({
            "id": cid, "object": "chat.completion.chunk", "created": now, "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}],
        }) + "\n\n"
        # Final chunk: finish_reason + usage
        yield "data: " + json.dumps({
            "id": cid, "object": "chat.completion.chunk", "created": now, "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/v1/debug/last-result", methods=["GET"])
def debug_last_result():
    """Return the raw JSON from the last Claude Code invocation."""
    if _last_result is None:
        return jsonify({"error": "no result yet"}), 404
    return jsonify(_last_result)


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    data = request.json or {}
    model = data.get("model", "")
    messages = data.get("messages", [])
    msg_count = len(messages)
    streaming = data.get("stream", False)

    if model.lower().startswith("claude"):
        key = _conv_key(data)
        # Bound total concurrency; additionally serialise per conversation so a resumable
        # session never overlaps itself (its --resume state is not concurrency-safe).
        # One-shot callers such as wyckoff have key=None and only take the semaphore.
        slock = _session_lock(key)
        with _claude_slots, (slock or contextlib.nullcontext()):
            sess = _sessions.get(key) if key else None
            # Resume this conversation's session (ship only the new turn) when it grew by ≥1 message;
            # a shrink (msg_count drop) means a NEW conversation took the key → fall back to fresh.
            should_resume = (
                sess is not None
                and sess["model"] == model
                and msg_count > sess["msg_count"]
                and msg_count > 1
            )
            if sess and not should_resume:
                log.info(f"claude: session reset key={key} (msg_count={msg_count} prev={sess['msg_count']})")

            log.info(f"→ Claude Code  key={key}  model={model}  msgs={msg_count}  resume={should_resume}  stream={streaming}")
            try:
                content, new_session_id, model_used = _call_claude(
                    messages, resume_id=sess["id"] if should_resume else None
                )
                actual = model_used or model     # what the CLI actually ran (e.g. claude-opus-4-8), not the request label
                if key and new_session_id:
                    # Keep the session keyed on the REQUEST label so the next same-label turn still resumes.
                    _sessions[key] = {"id": new_session_id, "model": model, "msg_count": msg_count}
                resp = _stream_response(content, actual) if streaming else jsonify(_openai_response(content, actual))
                resp.headers["X-Proxy-Backend"] = actual      # served by Claude Code — the REAL model, no marker
                return resp
            except Exception as e:
                # Fallback DISABLED (per request): the OpenRouter/qwen fallback is out of credits, and
                # Claude must stay the sole handler. Return a clear error so callers (wyckoff runs, the
                # agent) fail cleanly instead of getting an empty body from a dead 402 fallback — and so
                # a claude blip never silently spends on a paid model. The session is kept (untouched),
                # so the next turn can still resume. Re-enable by restoring:
                #     data["model"] = FALLBACK_MODEL; model = FALLBACK_MODEL  (and remove this return)
                log.warning(f"Claude Code error: {e} — fallback disabled, returning 503 (no qwen)")
                return jsonify({"error": {"message": f"claude unavailable: {e}",
                                          "type": "claude_unavailable"}}), 503

    # Forward to OpenRouter (paid, NON-Claude-Code → mark the reply so the user knows it cost tokens).
    log.info(f"→ OpenRouter  model={model}  msgs={msg_count}")
    if not OPENROUTER_KEY:
        return jsonify({"error": {"message": "OPENROUTER_API_KEY not set", "type": "proxy_error"}}), 500
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(OPENROUTER_URL, headers=headers, json=data, timeout=120, stream=streaming)
    if streaming:
        return app.response_class(_mark_stream(resp),
                                   mimetype="text/event-stream",
                                   headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache",
                                            "X-Proxy-Backend": model})
    out_json = resp.json()
    _mark_openai_json(out_json)
    out = jsonify(out_json)
    out.headers["X-Proxy-Backend"] = model               # the real backend (a paid fallback when claude failed)
    return out, resp.status_code


@app.route("/v1/models", methods=["GET"])
def list_models():
    return jsonify({
        "object": "list",
        "data": [
            {"id": "claude-code", "object": "model", "created": 0, "owned_by": "claude-code-proxy"},
            {"id": "claude-sonnet-4-6", "object": "model", "created": 0, "owned_by": "claude-code-proxy"},
            {"id": "claude-opus-4-6", "object": "model", "created": 0, "owned_by": "claude-code-proxy"},
        ],
    })


@app.route("/v1/models/<path:model_id>", methods=["GET"])
def get_model(model_id):
    return jsonify({"id": model_id, "object": "model", "created": 0, "owned_by": "claude-code-proxy"})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "claude_bin": CLAUDE_BIN,
        "sessions": {k: {"id": v["id"], "msg_count": v["msg_count"]} for k, v in _sessions.items()},
    })


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    Path(Path(__file__).parent / "logs").mkdir(exist_ok=True)
    log.info(f"Claude Code proxy starting on port {PROXY_PORT}")
    log.info(f"Claude bin: {CLAUDE_BIN}")
    log.info(f"OpenRouter key: {'set' if OPENROUTER_KEY else 'NOT SET'}")
    log.info(f"Fallback model: {FALLBACK_MODEL}")
    app.run(host="127.0.0.1", port=PROXY_PORT, threaded=True)
