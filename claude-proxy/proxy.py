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
import os
import subprocess
import sys
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
# Session state — one active Claude session at a time
# ---------------------------------------------------------------------------
_session: dict = {
    "id": None,       # claude session_id from last response
    "model": None,    # model that owns this session
    "msg_count": 0,   # message count when session was last updated
}


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


def _call_claude(messages: list[dict], resume_id: str | None = None) -> tuple[str, str | None]:
    """
    Invoke claude CLI. Returns (response_text, session_id).

    Fresh session: pass full reconstructed prompt.
    Resumed session: pass only the latest user message — Claude already has
    the prior context in its persisted session.
    """
    if resume_id:
        prompt = _last_user_message(messages)
        cmd = [CLAUDE_BIN, "--print", "--dangerously-skip-permissions",
               "--output-format", "json", "--resume", resume_id, prompt]
        log.info(f"claude: resume={resume_id}  new_msg_len={len(prompt)}")
    else:
        prompt = _messages_to_prompt(messages)
        cmd = [CLAUDE_BIN, "--print", "--dangerously-skip-permissions",
               "--output-format", "json", prompt]
        log.info(f"claude: fresh session  prompt_len={len(prompt)}")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}: {result.stderr[:300]}")

    try:
        data = json.loads(result.stdout)
        text = data.get("result") or data.get("content") or result.stdout
        session_id = data.get("session_id")
    except (json.JSONDecodeError, AttributeError):
        text = result.stdout.strip()
        session_id = None

    log.info(f"claude: response_len={len(text)}  session_id={session_id}")
    return text, session_id


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

@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    data = request.json or {}
    model = data.get("model", "")
    messages = data.get("messages", [])
    msg_count = len(messages)
    streaming = data.get("stream", False)

    if model.lower().startswith("claude"):
        # Decide whether to resume the existing session or start fresh
        sess = _session
        should_resume = (
            sess["id"] is not None
            and sess["model"] == model
            and msg_count > sess["msg_count"]
            and msg_count > 1
        )

        if not should_resume and sess["id"]:
            log.info(
                f"claude: session reset "
                f"(model_changed={sess['model'] != model}, "
                f"msg_count={msg_count} prev={sess['msg_count']})"
            )

        log.info(f"→ Claude Code  model={model}  msgs={msg_count}  resume={should_resume}  stream={streaming}")
        try:
            content, new_session_id = _call_claude(
                messages, resume_id=sess["id"] if should_resume else None
            )
            if new_session_id:
                _session.update({"id": new_session_id, "model": model, "msg_count": msg_count})
            if streaming:
                return _stream_response(content, model)
            return jsonify(_openai_response(content, model))
        except Exception as e:
            log.warning(f"Claude Code error: {e} — falling back to {FALLBACK_MODEL}")
            _session.update({"id": None, "model": None, "msg_count": 0})
            data["model"] = FALLBACK_MODEL
            model = FALLBACK_MODEL

    # Forward to OpenRouter — always non-streaming to avoid provider-side
    # streaming rate limits (e.g. Google AI Studio on free gemma tier).
    # We fake-stream the collected response back to hermes as SSE.
    log.info(f"→ OpenRouter  model={model}  msgs={msg_count}")
    if not OPENROUTER_KEY:
        return jsonify({"error": {"message": "OPENROUTER_API_KEY not set", "type": "proxy_error"}}), 500
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
    }
    or_data = {k: v for k, v in data.items() if k not in ("stream", "stream_options")}
    resp = requests.post(OPENROUTER_URL, headers=headers, json=or_data, timeout=120)
    if resp.status_code != 200:
        return jsonify(resp.json()), resp.status_code
    content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    if streaming:
        return _stream_response(content, model)
    return jsonify(_openai_response(content, model))


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
    active = _session["id"] is not None
    return jsonify({
        "status": "ok",
        "claude_bin": CLAUDE_BIN,
        "session_active": active,
        "session_id": _session["id"],
        "session_model": _session["model"],
        "session_msg_count": _session["msg_count"],
    })


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    Path(Path(__file__).parent / "logs").mkdir(exist_ok=True)
    log.info(f"Claude Code proxy starting on port {PROXY_PORT}")
    log.info(f"Claude bin: {CLAUDE_BIN}")
    log.info(f"OpenRouter key: {'set' if OPENROUTER_KEY else 'NOT SET'}")
    log.info(f"Fallback model: {FALLBACK_MODEL}")
    app.run(host="127.0.0.1", port=PROXY_PORT, threaded=True)
