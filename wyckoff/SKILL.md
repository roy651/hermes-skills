---
name: wyckoff
description: Daily Wyckoff method analysis for ETFs and stocks — phase detection, signal identification, and buy/hold/sell recommendations via Telegram. Weekly prescreener proposes watchlist candidates from S&P 500 + NASDAQ 100.
version: 1.1.0
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
SPY (SPDR S&P 500 ETF Trust) · 10 @ $520.00 · $532.10 (+2.3%)
  ✅ Markup (high) · 7/9 criteria
  Signals: LPS forming
  ✅ Hold · Entry $— · Stop $510.00
  Price holding above prior support after clean LPS.

Watchlist
GLD (SPDR Gold Shares) · $225.40
  🟡 Accumulation (medium) · 6/9 criteria
  Signals: Spring complete, SOS detected
  🔵 Watch · Entry $225–228 · Stop $219.00
  Spring and SOS visible; waiting for LPS confirmation before entry.
```

## Report Format — How to Read the Daily Digest

Each ticker block in the digest looks like this:

```
SPY (SPDR S&P 500 ETF Trust) · 10 @ $520.00 · $532.10 (+2.3%)
  ✅ Markup (high) · 7/9 criteria
  Signals: LPS forming
  ✅ Hold · Entry $— · Stop $510.00
  One-sentence summary.
```

**Line 1 — ticker header:**
- Ticker symbol and full instrument name
- For holdings: quantity held, average cost, current price, P&L %
- For watchlist: current price only

**Line 2 — phase and confidence:**
- 🟡 Accumulation — price consolidating at lows, smart money buying. Good phase to prepare an entry.
- ✅ Markup — uptrend in progress. Hold existing positions, look for pullback entries.
- ⚠️ Distribution — price consolidating at highs, smart money selling. Prepare to reduce or exit.
- 🔴 Markdown — downtrend in progress. Avoid new longs. Wait for next accumulation.
- ⬜ Unclear — structure not yet readable; no action.
- Confidence (high/medium/low): how clearly the phase is identifiable in the data.
- X/9 criteria: how many of the 9 Wyckoff entry criteria are met (higher = stronger setup).

**Line 3 — active signals:**
Key events detected recently. Common signals:
- Spring: dip below support quickly reversed — strongest buy signal in accumulation
- SOS (Sign of Strength): strong advance on high volume — confirms accumulation
- LPS (Last Point of Support): low-volume pullback after SOS — ideal entry point
- UT (Upthrust): spike above resistance that fails — warning sign of distribution
- UTAD: final upthrust confirming distribution — consider exiting

**Line 4 — recommendation:**
- 🟢 Buy / Add: high-conviction entry opportunity
- ✅ Hold: trend intact, no reason to exit
- 🟠 Reduce: weakness signals; trim position
- 🔴 Sell: distribution confirmed; exit
- 🔵 Watch: setup forming but not ready yet; keep monitoring
- ⬜ Pass: no tradeable structure; ignore for now
- Entry and Stop prices are suggested levels (not guaranteed)

**Nine Wyckoff criteria for a quality long entry:**
1. Broad market trend is up
2. This instrument is stronger than the market
3. A horizontal trading range is present
4. The range has lasted weeks to months
5. A final shakeout or Spring occurred
6. A SOS appeared with volume
7. An LPS on lower volume followed
8. Price tightening near resistance
9. No major macro/fundamental headwinds

5–6 criteria met → 30% position size; 7–8 → 50%; 9 → full position.

---

## Hermes Tool: Run Analysis On-Demand

When the user asks for a Wyckoff analysis or wants to refresh the digest:

```bash
# Full digest (portfolio + watchlist)
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/daily.py

# Portfolio only
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/daily.py --section portfolio

# Watchlist only
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/daily.py --section watchlist
```

Examples of what the user might say → what to run:
- "run wyckoff analysis" / "תריץ ניתוח וויקוף" → `daily.py` (full)
- "analyze my portfolio" / "תנתח את הפורטפוליו" → `daily.py --section portfolio`
- "check the watchlist" / "תבדוק את רשימת המעקב" → `daily.py --section watchlist`
- "refresh the digest" / "תעדכן את הסיכום" → `daily.py` (full)

## Hermes Tool: Run Weekly Prescreener On-Demand

When the user asks to scan for new watchlist candidates or wants to refresh the candidate list:

```bash
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/prescreener.py >> logs/prescreener.log 2>&1
```

This scans ~600 tickers from S&P 500 + NASDAQ 100 + sector ETFs using 5 programmatic Wyckoff accumulation filters (no LLM). The top ~30 candidates are sent to Telegram and saved to `data/watchlist_candidates.json`. The user reviews and adds approved tickers via `manage.py watchlist-add TICKER`.

Examples of what the user might say → what to run:
- "scan for watchlist candidates" / "תמצא מועמדים לרשימת המעקב" → `prescreener.py`
- "run the prescreener" / "תריץ את ה-prescreener" → `prescreener.py`
- "find me new stocks to watch" / "תמצא מניות חדשות למעקב" → `prescreener.py`
- "refresh the candidate list" → `prescreener.py`

## Hermes Tool: Deep-Dive Explanation for a Specific Ticker

When the user asks for more detail about a specific ticker — what it is, what the analysis means, what to watch for — run:

```bash
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/explain.py <TICKER>
```

Examples of what the user might say → what to run:
- "תסביר לי את ה-SPY" → `explain.py SPY`
- "מה זה QQQ?" → `explain.py QQQ`
- "תרחיב על הניתוח של GLD" → `explain.py GLD`
- "למה ה-TLT מקבל המלצת מכירה?" → `explain.py TLT`

The script fetches the latest data, runs a plain-language LLM analysis in Hebrew, and sends a detailed explanation to Telegram.

## Hermes Tool: Wyckoff Method Explanation

When the user asks general questions about the Wyckoff method — what it is, how it works, what the phases mean — answer from your own knowledge. You do not need to run a script for this. Key points to cover if asked:

- **The Wyckoff method** is a 100-year-old technical analysis approach by Richard Wyckoff. It identifies the behavior of large institutional players ("smart money" or the "composite operator") through price and volume patterns.
- **The four phases**: Accumulation (institutions buy quietly at low prices), Markup (price rises as the public follows), Distribution (institutions sell quietly at high prices), Markdown (price falls).
- **Why it works**: Large players cannot hide their activity — buying large quantities moves price up and increases volume. Wyckoff patterns are the fingerprints of this activity.
- **It is not a crystal ball**: it identifies likely scenarios based on structure, not certainties. Use it for probability, not prediction.
- **For ETFs specifically**: patterns are less clean than individual stocks (ETFs are already diversified), but broad market ETFs (SPY, QQQ) show the most reliable Wyckoff structures.

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

### Bulk add from prescreener candidates

When the user says "add them all", "add AAPL MSFT NVDA", or "add all except X Y Z":

1. Read `~/.hermes/skills/wyckoff/data/watchlist_candidates.json` to get the full candidate list
2. Apply the user's filter (all / specific tickers / all except listed)
3. Run `manage.py watchlist-add TICKER` once per approved ticker

```bash
cd ~/.hermes/skills/wyckoff

# Example: add all candidates
python3 -c "
import json
candidates = json.load(open('data/watchlist_candidates.json'))['candidates']
for c in candidates:
    import subprocess
    subprocess.run(['.venv/bin/python', 'scripts/manage.py', 'watchlist-add', c['ticker']])
"

# Or loop manually for a specific subset
.venv/bin/python scripts/manage.py watchlist-add AAPL
.venv/bin/python scripts/manage.py watchlist-add MSFT
```

Examples of what the user might say → what to do:
- "add them all to the watchlist" → read candidates, add all
- "add AAPL, MSFT, and NVDA from the list" → add just those three
- "add all except TSLA and META" → read candidates, skip those two, add the rest
- "תוסיף את כולם לרשימת המעקב" → same as "add them all"
- "תוסיף את כולם חוץ מ-TSLA" → add all except TSLA

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

# 3. Register all cron jobs in Hermes (job.json is now an array of 3 jobs)
python3 - << 'EOF'
import json
JOBS_FILE = "/home/roy650/.hermes/cron/jobs.json"
with open(JOBS_FILE) as f:
    data = json.load(f)
jobs = data if isinstance(data, list) else data.get("jobs", [])
with open("/home/roy650/.hermes/skills/wyckoff/job.json") as f:
    new_jobs = json.load(f)
new_ids = {j["id"] for j in new_jobs}
jobs = [j for j in jobs if j.get("id") not in new_ids]
jobs.extend(new_jobs)
result = jobs if isinstance(data, list) else {**data, "jobs": jobs}
with open(JOBS_FILE, "w") as f:
    json.dump(result, f, indent=2)
print(f"registered {len(new_jobs)} jobs")
EOF

# 4. Run once to test
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/daily.py
```

## File Structure

```
wyckoff/
├── SKILL.md
├── job.json              # array of 3 cron jobs (portfolio, watchlist, prescreener)
├── requirements.txt
├── config.yaml           # approved watchlist + LLM settings
├── scripts/
│   ├── daily.py          # daily runner: fetch → LLM analyze → send digest
│   │                     #   --section portfolio|watchlist|all
│   ├── prescreener.py    # weekly screener: S&P 500 + NASDAQ 100 → top 30 candidates
│   ├── analysis.py       # Wyckoff LLM analysis via OpenRouter
│   ├── data.py           # Yahoo Finance OHLCV fetch
│   ├── holdings.py       # portfolio state (data/holdings.json)
│   ├── manage.py         # CLI: add/remove holdings and watchlist
│   └── notifier.py       # Telegram sender
├── data/
│   ├── holdings.json           # portfolio positions
│   └── watchlist_candidates.json  # latest prescreener output (not committed)
└── logs/
    ├── daily.log
    └── prescreener.log
```

## Schedule

| Job | Cron (UTC) | Israel Time | Description |
|-----|-----------|-------------|-------------|
| Portfolio analysis | `0 20 * * 1-5` | 23:00 Mon–Fri | Holdings after US market close |
| Watchlist analysis | `20 20 * * 1-5` | 23:20 Mon–Fri | Approved watchlist after US close |
| Prescreener | `0 6 * * 0` | 09:00 Sunday | Scan ~600 tickers, propose candidates |

## Hermes Tool: Bulk Load from Israeli Broker Export

Roy's broker exports a Hebrew table with columns: שם נייר (name), מספר נייר (TASE security #), שער אחרון (last price), כמות בתיק (qty), שער עלות (avg cost), נתח מהתיק (portfolio %).

To load these into Wyckoff holdings:
1. Map Hebrew names + TASE IDs to Yahoo Finance tickers (see `references/roy-portfolio-tickers.md`)
2. Use `manage.py holdings-add <TICKER> <QTY> <AVG_COST>` for each
3. Watch for TASE tickers (`.TA` suffix) — some return 404 from yfinance (e.g., `SLRL.TA`)

**Known portfolio**: `references/roy-portfolio-tickers.md` has the full confirmed mapping and known broken tickers.

**About qty and avg_cost**: These values only affect the P&L display line in the digest header (e.g., `10 @ $520 · $532 (+2.3%)`). They have **zero effect** on Wyckoff analysis — phase detection and signals are OHLCV-only. Placeholder values (1 @ 0) are functional but produce meaningless P&L numbers.

## Notes

- Data source: Yahoo Finance API (direct) — free, no API key, covers all major ETFs and stocks
- Prescreener: pure Python/math, no LLM — fetches concurrently (10 workers), takes ~2-3 min for ~600 tickers
- Daily analysis: LLM (Claude Sonnet via OpenRouter) on 120 days OHLCV — not algorithmic signal detection
- Wyckoff is a swing/position methodology; daily candles are the appropriate timeframe
- Treat recommendations as a second opinion, not automated trading signals
- Prescreener candidates in `data/watchlist_candidates.json` are suggestions only; you decide what goes in `config.yaml`

- TASE-listed securities use `.TA` suffix on Yahoo Finance (e.g., `AMOT.TA`, `TCH-F3.TA`); not all are available
