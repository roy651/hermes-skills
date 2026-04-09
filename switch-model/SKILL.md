---
name: switch-model
description: Switch the active LLM backend or change the Claude Code model. Handles two distinct operations — provider switching (claude/qwen/gemma/nemotron) and Claude model tier switching (haiku/sonnet/opus).
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [model, llm, provider, claude, qwen, gemma, switch]
---

# Switch Model

Manages two distinct layers of model configuration. Always identify which layer the user is asking about before acting.

## The Two Layers

```
Layer 1: Provider  →  which backend to use  →  edit ~/.hermes/config.yaml
Layer 2: Claude model tier  →  which Claude model  →  run claude config set
```

**NEVER edit `~/hermes-skills/claude-proxy/proxy.py` for model changes.**  
**NEVER add `--model` flags to claude CLI calls in the proxy.**

---

## Layer 1 — Switching Provider

**Triggers:** "switch to qwen", "use gemma", "switch to claude", "use nemotron", "change to [provider name]"

Edit the `model:` block in `~/.hermes/config.yaml` using the profiles below. The config is re-read on every request — no restart needed.

### Provider Profiles

**claude** (via local proxy):
```yaml
model:
  default: claude-code
  provider: custom
  base_url: http://localhost:8765/v1
  api_mode: chat_completions
```

**qwen**:
```yaml
model:
  default: qwen/qwen3.5-flash-02-23
  provider: openrouter
  base_url: https://openrouter.ai/api/v1
  api_mode: chat_completions
```

**gemma**:
```yaml
model:
  default: google/gemma-4-31b-it:free
  provider: openrouter
  base_url: https://openrouter.ai/api/v1
  api_mode: chat_completions
```

**nemotron**:
```yaml
model:
  default: nvidia/nemotron-3-super-120b-a12b:free
  provider: openrouter
  base_url: https://openrouter.ai/api/v1
  api_mode: chat_completions
```

### How to Switch

Read `~/.hermes/config.yaml`, replace only the `model:` block with the matching profile above, write the file back. Do not touch any other section.

Confirm with: "Switched to [name]. Takes effect on the next message."

---

## Layer 2 — Changing the Claude Model Tier

**Triggers:** "use haiku", "switch to sonnet", "change Claude to opus", "use a cheaper/faster Claude model"

This is entirely within Claude Code's own configuration — invisible to the proxy.

**CRITICAL: Use the terminal tool to run the bash command below. Do NOT use the file tool. Do NOT call `~/.local/bin/claude config set` (it opens an interactive session, not a command).**

Run this one-liner via the terminal tool, substituting `<model-id>`:

```bash
python3 -c "
import json, os
path = os.path.expanduser('~/.claude/settings.json')
s = json.loads(open(path).read() or '{}')
s['model'] = '<model-id>'
open(path, 'w').write(json.dumps(s, indent=2))
print('Done')
"
```

### Model IDs

| Alias   | Full model ID                    |
|---------|----------------------------------|
| haiku   | claude-haiku-4-5-20251001        |
| sonnet  | claude-sonnet-4-6                |
| opus    | claude-opus-4-6                  |

To check current setting:
```bash
python3 -c "import json,os; s=json.loads(open(os.path.expanduser('~/.claude/settings.json')).read() or '{}'); print(s.get('model','not set'))"
```

Change takes effect immediately on the next Claude call — no proxy restart needed.

Confirm with: "Claude model set to [alias] ([model-id])."

---

## Ambiguous Requests

If the user says something like "change the model" without specifying which layer:
- If they name a provider (qwen, gemma, nemotron, claude) → Layer 1
- If they name a Claude tier (haiku, sonnet, opus) → Layer 2
- If still unclear, ask: "Do you want to switch the backend provider (e.g. to qwen), or change the Claude model tier (e.g. to haiku)?"
