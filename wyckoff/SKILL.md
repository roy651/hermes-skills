---
name: wyckoff
description: Daily Wyckoff method analysis for ETFs and stocks — phase detection, signal identification, and buy/hold/sell recommendations via Telegram.
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [cron, finance, trading, telegram]
---

# Wyckoff Trading Assistant

Runs a daily Wyckoff method analysis on a tracked watchlist and portfolio. Sends a Telegram digest each weekday morning (09:00 Israel time) with phase classification, detected signals, and actionable recommendations.

## Output Example

```
📊 Wyckoff Daily — 2026-05-09

Portfolio
SPY · 10 @ $520.00 · $532.10 (+2.3%)
  ✅ Markup (high) · 7/9 criteria
  Signals: LPS forming
  ✅ Hold · Entry $— · Stop $510.00
  Price holding above prior support after clean LPS.

Watchlist
GLD · $225.40
  🟡 Accumulation (medium) · 6/9 criteria
  Signals: Spring complete, SOS detected
  🔵 Watch · Entry $225–228 · Stop $219.00
  Spring and SOS visible; waiting for LPS confirmation before entry.
```

## Hermes Tool: Run Analysis On-Demand

When the user asks for a Wyckoff analysis or wants to refresh the digest:

```bash
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/daily.py
```

## Hermes Tool: Manage Holdings

When the user says they bought, sold, or wants to update a position:

```bash
cd ~/.hermes/skills/wyckoff

# Show current holdings
.venv/bin/python scripts/manage.py holdings-list

# Add or update a position (qty and avg_cost are numbers)
.venv/bin/python scripts/manage.py holdings-add SPY 10 520.00

# Remove a position (fully exited)
.venv/bin/python scripts/manage.py holdings-remove SPY
```

Examples of what the user might say → what to run:
- "I bought 10 SPY at $520" → `manage.py holdings-add SPY 10 520`
- "I sold all my QQQ" → `manage.py holdings-remove QQQ`
- "Add 5 GLD at 225.50 to my holdings" → `manage.py holdings-add GLD 5 225.50`
- "What are my current positions?" → `manage.py holdings-list`

## Hermes Tool: Manage Watchlist

When the user wants to add or remove a ticker from the watchlist:

```bash
cd ~/.hermes/skills/wyckoff

# Show watchlist
.venv/bin/python scripts/manage.py watchlist-list

# Add a ticker
.venv/bin/python scripts/manage.py watchlist-add VTI

# Remove a ticker
.venv/bin/python scripts/manage.py watchlist-remove TLT
```

## Configuration

Edit `~/.hermes/skills/wyckoff/config.yaml`:

```yaml
watchlist:
  - SPY
  - QQQ
  - VTI
  - GLD
  - TLT

llm:
  model: anthropic/claude-sonnet-4-5
  lookback_days: 120
```

Override LLM model per-session via env var: `WYCKOFF_LLM_MODEL=anthropic/claude-opus-4-7`.

## Install

```bash
# 1. Copy skill to Hermes
cp -r ~/hermes-skills/wyckoff ~/.hermes/skills/

# 2. Create venv and install dependencies
cd ~/.hermes/skills/wyckoff
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

# 3. Register daily cron job in Hermes
python3 - << 'EOF'
import json
JOBS_FILE = "/home/roy650/.hermes/cron/jobs.json"
with open(JOBS_FILE) as f:
    data = json.load(f)
jobs = data if isinstance(data, list) else data.get("jobs", [])
with open("/home/roy650/.hermes/skills/wyckoff/job.json") as f:
    new_job = json.load(f)
jobs = [j for j in jobs if j.get("id") != new_job["id"]]
jobs.append(new_job)
result = jobs if isinstance(data, list) else {**data, "jobs": jobs}
with open(JOBS_FILE, "w") as f:
    json.dump(result, f, indent=2)
print("job registered")
EOF

# 4. Run once to test
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/daily.py
```

## File Structure

```
wyckoff/
├── SKILL.md
├── job.json              # weekday cron at 06:00 UTC = 09:00 Israel
├── requirements.txt
├── config.yaml           # watchlist + LLM settings
├── scripts/
│   ├── daily.py          # main runner: fetch → analyze → send digest
│   ├── analysis.py       # Wyckoff LLM analysis via OpenRouter
│   ├── data.py           # yfinance OHLCV fetch
│   ├── holdings.py       # portfolio state (data/holdings.json)
│   ├── manage.py         # CLI: add/remove holdings and watchlist
│   └── notifier.py       # Telegram sender
├── data/
│   └── holdings.json     # portfolio positions
└── logs/
    └── daily.log
```

## Notes

- Data source: [yfinance](https://github.com/ranaroussi/yfinance) — free, no API key, covers all major ETFs and stocks
- Analysis: LLM (Claude Sonnet via OpenRouter) reasoning on 120 days of OHLCV — not algorithmic signal detection
- Wyckoff is a swing/position methodology; daily candles are the appropriate timeframe
- Treat recommendations as a second opinion, not automated trading signals
