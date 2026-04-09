#!/usr/bin/env bash
# Usage: switch.sh <profile>
# Profiles: claude, qwen, gemma, nemotron
# Also handles Claude model tier: switch.sh claude-model <haiku|sonnet|opus>
set -e

HERMES_CONFIG="$HOME/.hermes/config.yaml"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"

profile="${1:-}"

case "$profile" in
  claude)
    python3 - <<'EOF'
import yaml, os
path = os.path.expanduser('~/.hermes/config.yaml')
with open(path) as f:
    config = yaml.safe_load(f)
config['model'] = {
    'default': 'claude-code',
    'provider': 'custom',
    'base_url': 'http://localhost:8765/v1',
    'api_mode': 'chat_completions',
}
with open(path, 'w') as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
with open(path) as f:
    result = yaml.safe_load(f)
print(f"Provider switched → {result['model']['default']} ({result['model']['provider']})")
EOF
    ;;
  qwen)
    python3 - <<'EOF'
import yaml, os
path = os.path.expanduser('~/.hermes/config.yaml')
with open(path) as f:
    config = yaml.safe_load(f)
config['model'] = {
    'default': 'qwen/qwen3.5-flash-02-23',
    'provider': 'custom',
    'base_url': 'http://localhost:8765/v1',
    'api_mode': 'chat_completions',
}
with open(path, 'w') as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
with open(path) as f:
    result = yaml.safe_load(f)
print(f"Provider switched → {result['model']['default']} ({result['model']['provider']})")
EOF
    ;;
  gemma)
    python3 - <<'EOF'
import yaml, os
path = os.path.expanduser('~/.hermes/config.yaml')
with open(path) as f:
    config = yaml.safe_load(f)
config['model'] = {
    'default': 'google/gemma-4-31b-it:free',
    'provider': 'custom',
    'base_url': 'http://localhost:8765/v1',
    'api_mode': 'chat_completions',
}
with open(path, 'w') as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
with open(path) as f:
    result = yaml.safe_load(f)
print(f"Provider switched → {result['model']['default']} ({result['model']['provider']})")
EOF
    ;;
  nemotron)
    python3 - <<'EOF'
import yaml, os
path = os.path.expanduser('~/.hermes/config.yaml')
with open(path) as f:
    config = yaml.safe_load(f)
config['model'] = {
    'default': 'nvidia/nemotron-3-super-120b-a12b:free',
    'provider': 'custom',
    'base_url': 'http://localhost:8765/v1',
    'api_mode': 'chat_completions',
}
with open(path, 'w') as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
with open(path) as f:
    result = yaml.safe_load(f)
print(f"Provider switched → {result['model']['default']} ({result['model']['provider']})")
EOF
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
hermes = os.path.expanduser('~/.hermes/config.yaml')
claude = os.path.expanduser('~/.claude/settings.json')
with open(hermes) as f:
    hc = yaml.safe_load(f)
cc = json.loads(open(claude).read() or '{}')
print(f"Provider : {hc['model']['default']} ({hc['model']['provider']})")
print(f"Claude   : {cc.get('model', 'default (sonnet)')}")
EOF
    ;;
  *)
    echo "Usage: switch.sh <claude|qwen|gemma|nemotron|claude-model <haiku|sonnet|opus>|status>"
    exit 1
    ;;
esac
