---
name: navman
description: Navigation drill coordinator — generates balanced navigation assignments and participant pairings for field navigation exercises via a dedicated Telegram bot.
license: MIT
---

# NavMan — Navigation Drill Coordinator

## Description

A dedicated Telegram bot for navigation coordinators preparing drill assignments.
The coordinator uploads a points database, optionally filters by a map boundary,
sets special points, and the skill algorithmically generates balanced navigation
paths and participant pairings.

All user-facing text is in Hebrew.

## Workflow

```
/session → /upload_points → /done
        → /upload_map → /done → /confirm_map   (optional, or /skip_map)
        → /special <start> <mid> <finish>
        → /generate <n> <avg_km> <min_km> <max_km> <participants>
        → /upload_participants → /done
        → /assign
        → /export
```

## Setup

### 1. Create the Telegram bot

1. Message @BotFather: `/newbot`
2. Set name and username
3. Copy the token

### 3. Configure `.env`

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env`:
```
TELEGRAM_BOT_TOKEN=your_bot_token
ALLOWED_CHAT_IDS=your_telegram_chat_id

# Vision LLM (any OpenAI-compatible endpoint)
VISION_API_URL=https://openrouter.ai/api/v1/chat/completions
VISION_API_KEY=your_openrouter_key
VISION_MODEL=anthropic/claude-3-5-sonnet
```

To find your chat ID: message @userinfobot on Telegram.

### 4. Start the bot

```bash
bash ~/.hermes/skills/navman/run.sh
```

To run as a background daemon:
```bash
nohup bash ~/.hermes/skills/navman/run.sh \
    >> ~/.hermes/skills/navman/logs/daemon.log 2>&1 &
echo $! > ~/.hermes/skills/navman/navman.pid
```

To stop:
```bash
kill $(cat ~/.hermes/skills/navman/navman.pid)
```

## Commands Reference

| Command | Alias | Description |
|---------|-------|-------------|
| `/session` | `/s` | Start new session (wipes state) |
| `/status` | `/st` | Show current state |
| `/help` | `/h` | List all commands |
| `/upload_points` | `/up` | Enter points upload mode |
| `/upload_map` | `/um` | Enter map image upload mode (optional) |
| `/upload_participants` | `/upa` | Enter participants upload mode |
| `/done` | `/d` | Finish uploading (triggers processing) |
| `/skip_map` | `/sm` | Skip map filtering (use all points) |
| `/confirm_map` | `/cm` | Accept LLM-extracted point list |
| `/edit_map <ids>` | `/em <ids>` | Manually set filtered point IDs |
| `/special <s> <m> <f>` | `/sp <s> <m> <f>` | Set start, intermediate, finish point IDs |
| `/generate <n> <avg> <min> <max> <p>` | `/gen ...` | Generate n navigation tasks (p = participants) |
| `/assign` | `/a` | Pair participants and assign tasks |
| `/export` | `/ex` | Send XLS result files |

## Input Formats

### Navigation Points Table
Columns (any order, auto-detected):
- **ID**: integer point number (< 1000)
- **X**: ITM easting (e.g., 668237.376)
- **Y**: ITM northing (e.g., 3390075)
- **Description**: Hebrew text

Supported: CSV, XLS/XLSX, or photos of the table (docling table extraction + optional LLM description correction).

### Map Image
A photo of a topographic map with a hand-drawn closed boundary line.
The LLM identifies which numbered points appear inside the boundary.
The coordinator confirms or manually corrects the list.

### Participant Table
Columns:
- **Index**: integer
- **Name**: Hebrew text
- **Score**: integer, decimal, fraction (7/10), or percentage (85%)

## Algorithm

### Path Generation
- Coordinates: ITM (meters) → Euclidean distance in km
- For each navigation task: finds optimal ordering of n intermediate points
  between start and end via brute-force permutations (max 120 for n=5)
- Point selection: greedy coverage seed + simulated annealing refinement
- Maximizes unique points used across all assignments

### Participant Pairing
- Scores normalized to [0, 100] regardless of input format
- Sorted best → worst (ties by Hebrew name)
- Paired: best with worst, 2nd with 2nd-to-last, etc.
- Each pair gets one S→I and one I→F task (randomly ordered)

## Output

Two Excel files sent via Telegram:

**assignments.xlsx** — one row per task:
`מס' משימה | מקטע | נקודה 1 | נקודה 2 | ... | אורך (ק"מ)`

**pairings.xlsx** — one row per pair:
`מס' זוג | משתתף א | ציון א | מס' משימה א | מקטע א | אורך א | משתתף ב | ... `

## File Structure

```
navman/
├── SKILL.md
├── .env.example
├── .env                    # credentials (git-ignored)
├── requirements.txt
├── scripts/
│   ├── run.sh              # daemon launcher
│   ├── bot_handler.py      # Telegram bot + command router
│   ├── session.py          # per-chat JSON state
│   ├── ingestion.py        # CSV/XLS/image table parser
│   ├── map_parser.py       # vision LLM map boundary extractor
│   ├── nav_algorithm.py    # path generation (greedy + SA)
│   ├── participants.py     # scoring, pairing, assignment
│   └── export.py           # openpyxl XLS generation
├── data/
│   ├── <chat_id>.json      # session state files
│   ├── uploads/            # downloaded Telegram files
│   └── exports/            # generated XLS files
└── logs/
    └── bot_handler.log
```

## Pitfalls

1. **Image table quality** — docling performs best with clear, well-lit photos where table
   lines are visible. If extraction fails, uploading CSV/XLS is always more reliable.

2. **Map image quality** — the LLM performs better with clear, high-contrast
   images. If extraction is wrong, use `/edit_map <ids>` to set manually.

3. **Path length infeasibility** — if the area is very small or very large
   relative to `min_km`/`max_km`, the algorithm may fail to find valid paths.
   Adjust the distance parameters accordingly.

4. **Vision API** — requires `VISION_API_KEY` and a model with vision support
   (e.g., `anthropic/claude-3-5-sonnet` via openRouter). Without it, only
   CSV/XLS uploads are supported (Tesseract-only for image tables).

5. **Session state** is stored per `chat_id` in `data/<chat_id>.json`.
   Starting `/session` wipes all previous state for that chat.
