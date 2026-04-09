#!/usr/bin/env bash
# Usage: switch.sh <profile>
# Profiles: claude, qwen, qwen-coder, mimo, gpt-oss
# Claude model tier: switch.sh claude-model <haiku|sonnet|opus>
# Status: switch.sh status
#
# Claude routes through local proxy (http://localhost:8765/v1, provider: custom).
# All OpenRouter models go direct (provider: openrouter) — they all support streaming.
set -e

profile="${1:-}"

_set_hermes_model() {
  python3 - "$1" "$2" "$3" <<'EOF'
import yaml, os, sys
path = os.path.expanduser('~/.hermes/config.yaml')
with open(path) as f:
    config = yaml.safe_load(f)
config['model'] = {
    'default':  sys.argv[1],
    'provider': sys.argv[2],
    'base_url': sys.argv[3],
    'api_mode': 'chat_completions',
}
with open(path, 'w') as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
with open(path) as f:
    m = yaml.safe_load(f)['model']
print(f"Provider switched → {m['default']} ({m['provider']})")
EOF
}

case "$profile" in
  claude)
    _set_hermes_model "claude-code" "custom" "http://localhost:8765/v1"
    ;;
  qwen)
    _set_hermes_model "qwen/qwen3.5-flash-02-23" "openrouter" "https://openrouter.ai/api/v1"
    ;;
  qwen-coder)
    _set_hermes_model "qwen/qwen3-coder:free" "openrouter" "https://openrouter.ai/api/v1"
    ;;
  mimo)
    _set_hermes_model "xiaomi/mimo-v2-flash" "openrouter" "https://openrouter.ai/api/v1"
    ;;
  gpt-oss)
    _set_hermes_model "openai/gpt-oss-120b" "openrouter" "https://openrouter.ai/api/v1"
    ;;
  claude-model)
    tier="${2:-sonnet}"
    case "$tier" in
      haiku)  model_id="claude-haiku-4-5-20251001" ;;
      sonnet) model_id="claude-sonnet-4-6" ;;
      opus)   model_id="claude-opus-4-6" ;;
      *)      echo "Unknown tier: $tier. Use haiku, sonnet, or opus."; exit 1 ;;
    esac
    python3 - "$model_id" <<'EOF'
import json, os, sys
path = os.path.expanduser('~/.claude/settings.json')
s = json.loads(open(path).read() or '{}')
s['model'] = sys.argv[1]
open(path, 'w').write(json.dumps(s, indent=2))
print(f"Claude model set → {sys.argv[1]}")
EOF
    ;;
  status)
    python3 - <<'EOF'
import yaml, json, os
hc = yaml.safe_load(open(os.path.expanduser('~/.hermes/config.yaml')))['model']
cc = json.loads(open(os.path.expanduser('~/.claude/settings.json')).read() or '{}')
print(f"Provider : {hc['default']} ({hc['provider']})")
print(f"Claude   : {cc.get('model', 'default (sonnet)')}")
EOF
    ;;
  *)
    echo "Usage: switch.sh <claude|qwen|qwen-coder|mimo|gpt-oss|claude-model <haiku|sonnet|opus>|status>"
    exit 1
    ;;
esac
