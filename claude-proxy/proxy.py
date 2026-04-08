"""
Claude Code Proxy — OpenAI-compatible /v1/chat/completions endpoint.

Routes requests to:
  - Claude Code CLI  (model starts with "claude")
  - OpenRouter       (everything else, forwarded transparently)

Hermes config to use this proxy:
  model:
    default: claude-code          # or claude-sonnet-4-6, etc.
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
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")  # path to claude CLI

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
# Helpers
# ---------------------------------------------------------------------------

def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return str(content or "")


def _messages_to_prompt(messages: list[dict]) -> str:
    """
    Convert OpenAI messages array → single prompt for `claude -p`.

    Strategy:
    - Single user message: pass it directly (clean, no noise)
    - Multi-turn: reconstruct conversation with Human/Assistant markers
    - System message: prepended as context (truncated to avoid bloating)
    """
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
        # tool / tool_result roles: skip — Claude handles its own tools

    if not turns:
        return system

    # Single-turn: pass cleanly without wrapper
    if len(turns) == 1 and turns[0][0] == "Human":
        user_msg = turns[0][1]
        if system:
            # Prepend a condensed system context
            return f"[Context: {system[:400]}]\n\n{user_msg}"
        return user_msg

    # Multi-turn: full conversation reconstruction
    parts = []
    if system:
        parts.append(f"[Context: {system[:400]}]")
    for role, content in turns:
        parts.append(f"{role}: {content}")
    return "\n\n".join(parts)


def _call_claude(messages: list[dict]) -> str:
    """Run `claude -p <prompt>` and return the response text."""
    prompt = _messages_to_prompt(messages)
    cmd = [
        CLAUDE_BIN,
        "--print",
        "--dangerously-skip-permissions",
        "--output-format", "json",
        prompt,
    ]
    log.info(f"claude: prompt length={len(prompt)} chars")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}: {result.stderr[:300]}")

    # claude --output-format json gives {"type":"result","result":"...","session_id":"..."}
    try:
        data = json.loads(result.stdout)
        text = data.get("result") or data.get("content") or result.stdout
    except (json.JSONDecodeError, AttributeError):
        text = result.stdout.strip()

    log.info(f"claude: response length={len(text)} chars")
    return text


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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    data = request.json or {}
    model = data.get("model", "")
    messages = data.get("messages", [])

    if model.lower().startswith("claude"):
        log.info(f"→ Claude Code  model={model}  msgs={len(messages)}")
        try:
            content = _call_claude(messages)
            return jsonify(_openai_response(content, model))
        except Exception as e:
            log.error(f"Claude Code error: {e}")
            return jsonify({"error": {"message": str(e), "type": "proxy_error"}}), 500

    # Forward to OpenRouter
    log.info(f"→ OpenRouter  model={model}")
    if not OPENROUTER_KEY:
        return jsonify({"error": {"message": "OPENROUTER_API_KEY not set", "type": "proxy_error"}}), 500
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(OPENROUTER_URL, headers=headers, json=data, timeout=120)
    return jsonify(resp.json()), resp.status_code


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


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "claude_bin": CLAUDE_BIN})


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    Path(Path(__file__).parent / "logs").mkdir(exist_ok=True)
    log.info(f"Claude Code proxy starting on port {PROXY_PORT}")
    log.info(f"Claude bin: {CLAUDE_BIN}")
    log.info(f"OpenRouter key: {'set' if OPENROUTER_KEY else 'NOT SET'}")
    app.run(host="127.0.0.1", port=PROXY_PORT, threaded=True)
