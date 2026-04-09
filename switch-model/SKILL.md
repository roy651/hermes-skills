---
name: switch-model
description: Switch the active LLM backend or change the Claude Code model tier. Run the switch.sh script — do not edit config files manually.
version: 2.0.0
license: MIT
metadata:
  hermes:
    tags: [model, llm, provider, claude, qwen, gemma, switch]
---

# Switch Model

Two distinct operations. Always run the script via the terminal tool and show the output as confirmation. Never fake or assume a switch happened — the script output is the confirmation.

## How to Invoke

Always use the terminal tool to run:
```
bash ~/.hermes/skills/switch-model/switch.sh <command>
```

## Commands

| Goal | Command |
|------|---------|
| Switch to Claude (via proxy) | `bash ~/.hermes/skills/switch-model/switch.sh claude` |
| Switch to Qwen (free, reliable) | `bash ~/.hermes/skills/switch-model/switch.sh qwen` |
| Switch to Qwen Coder (free) | `bash ~/.hermes/skills/switch-model/switch.sh qwen-coder` |
| Switch to MiMo v2 Flash | `bash ~/.hermes/skills/switch-model/switch.sh mimo` |
| Switch to GPT-OSS 120B | `bash ~/.hermes/skills/switch-model/switch.sh gpt-oss` |
| Change Claude tier to Haiku | `bash ~/.hermes/skills/switch-model/switch.sh claude-model haiku` |
| Change Claude tier to Sonnet | `bash ~/.hermes/skills/switch-model/switch.sh claude-model sonnet` |
| Change Claude tier to Opus | `bash ~/.hermes/skills/switch-model/switch.sh claude-model opus` |
| Show current model status | `bash ~/.hermes/skills/switch-model/switch.sh status` |

## Rules

- **ALWAYS run the script.** Do not edit `~/.hermes/config.yaml` or `~/.claude/settings.json` directly.
- **Show the terminal output** as your confirmation. The output line is the proof the switch happened.
- **Never say "Switched to X" without first running the script and seeing the output.**
- Provider switches (claude/qwen/gemma/nemotron) take effect on the next message — hermes re-reads config per request.
- Claude model tier changes take effect on the next Claude CLI call — no restart needed.

## Triggers

- "switch to qwen/qwen-coder/mimo/gpt-oss/claude" → provider switch
- "use haiku/sonnet/opus" or "change Claude to [tier]" → claude-model switch
- "what model am I on" or "show model status" → status
