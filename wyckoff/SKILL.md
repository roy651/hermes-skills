---
name: wyckoff
description: Wyckoff method analysis for ETFs and stocks via Telegram. Weekly entry funnel (prescreen → Wyckoff LLM → up to 5 tiered buy picks) plus a daily portfolio exit-watch for distribution signals.
version: 1.2.0
license: MIT
metadata:
  hermes:
    tags: [cron, finance, trading, telegram]
---

# Wyckoff Trading Assistant

Two complementary jobs:
- **Weekly entry funnel** (Sunday): screens ~600 S&P 500 + NASDAQ 100 names, runs Wyckoff LLM analysis on the survivors, news-validates the top cut, and sends up to **5 tiered entry picks** (🟢 STRONG / 🟡 BORDERLINE) to Telegram. The weekly digest *is* the entry signal.
- **Daily exit-watch** (Mon–Fri after US close): runs Wyckoff analysis on **held positions only**, tuned to surface distribution/weakness (UT, UTAD, SOW, LPSY, broken support) so you know when to reduce or exit.

Two lightweight daily jobs round it out: a price-move alert scan (≥3.5%) and a portfolio valuation report.

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

## Hermes Tool: Run Weekly Entry Funnel On-Demand

The weekly funnel is the main entry-signal generator: prescreen ~600 tickers → Wyckoff LLM on survivors → news-validate the top cut → up to 5 tiered picks (STRONG / BORDERLINE).

```bash
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/entry.py >> logs/weekly.log 2>&1

# Preview without sending to Telegram:
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/entry.py --dry-run

# Lighter/heavier on-demand scan — cohort = how many top prescreen survivors get the LLM read
# (default 30, from config.yaml entry.cohort_size). Fewer = cheaper/faster, smaller pick pool:
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/entry.py --cohort 15 --dry-run
```

Examples of what the user might say → what to run:
- "run the weekly analysis" / "תריץ את הניתוח השבועי" → `entry.py`
- "find me entry picks" / "מה כדאי לקנות?" → `entry.py`
- "what are this week's buys" → `entry.py`
- "run a quick/light entry scan" / "do a smaller scan, ~15 names" → `entry.py --cohort 15`

## Hermes Tool: Run Daily Exit-Watch On-Demand

The daily job reviews **held positions only** for distribution/exit risk (default `--section portfolio`, exit-tuned prompt).

```bash
# Held positions, exit-watch (default)
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/exit.py

# Also available on-demand (entry-tuned): approved watchlist, or everything
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/exit.py --section watchlist
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/exit.py --section all

# Preview without sending:
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/exit.py --dry-run
```

Examples of what the user might say → what to run:
- "analyze my portfolio" / "תנתח את הפורטפוליו" → `exit.py` (portfolio exit-watch)
- "any exit signals?" / "יש סימני מכירה?" → `exit.py`
- "check the watchlist" / "תבדוק את רשימת המעקב" → `exit.py --section watchlist`

## Hermes Tool: Raw Candidate Scan On-Demand

For just the quantitative prescreen (no LLM) — the ~30 raw candidates with scores, saved to `data/watchlist_candidates.json`:

```bash
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/prescreener.py >> logs/prescreener.log 2>&1
```

Examples of what the user might say → what to run:
- "scan for watchlist candidates" / "תמצא מועמדים לרשימת המעקב" → `prescreener.py`
- "run the prescreener" / "תריץ את ה-prescreener" → `prescreener.py`
- "find me new stocks to watch" → `prescreener.py`

## Hermes Tool: Deep-Dive Explanation for a Specific Ticker

When the user asks about a specific ticker after a report — "why is X a trim?", "should I really add Y?", "explain Z", "what's going on with TLT?" — run:

```bash
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/explain.py <TICKER>
```

This prints the deterministic engine **breakdown as data** — the 0–9 exit score with every criterion, the trailing-stop math, the scale-out ladder decision and its reasoning, the Wyckoff structure, and real catalysts (earnings + recent headlines). It does NOT call an LLM and does NOT post to Telegram.

**Your job:** read that data and explain it to the user **conversationally, in their language**, reasoning like an analyst — do not just dump the raw output. For the mechanism's logic and the analytical lens (is the score legit or an artifact? a structural top vs a bleed? a sector cluster? let the stop arbitrate when ambiguous?), load `README.md` and `DESIGN.md` in this skill directory first. Be honest and specific, cite the actual numbers, and flag where the mechanical read may be wrong (ex-dividend, index rebalance, thin volume, a catalyst).

Examples → what to run:
- "למה ה-HAL מקבל המלצת trim?" → `explain.py HAL`, then explain the distribution signals and why
- "should I add IEMG?" → `explain.py IEMG`, then weigh the setup against the validator's caution
- "מה המצב של MBLY?" → `explain.py MBLY`

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

### Environment (`~/.hermes/.env`)

- `LLM_API_URL` — Wyckoff/news LLM endpoint (defaults to the local claude-proxy `http://localhost:8765/v1/chat/completions`).
- `FINNHUB_API_KEY` — required for the weekly funnel's news/fundamentals stage (earnings-calendar exclusion, market cap, company news, analyst consensus). Free key at https://finnhub.io/. Without it, the weekly run still produces picks but skips earnings exclusion and news validation (so nothing reaches the STRONG tier).
- `WYCKOFF_NEWS_MODEL` — model used to reason over Finnhub headlines (default `claude-haiku-4-5`).

News validation pulls headlines + analyst consensus from Finnhub and reasons over them via the local claude-proxy — no Perplexity/OpenRouter dependency.

## Install

```bash
# 1. Copy skill to Hermes
cp -r ~/hermes-skills/wyckoff ~/.hermes/skills/

# 2. Create venv and install dependencies
cd ~/.hermes/skills/wyckoff
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

# 3. Register all cron jobs in Hermes (job.json is an array of 4 jobs)
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
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/exit.py
```

## File Structure

```
wyckoff/
├── SKILL.md
├── job.json              # array of 4 cron jobs (weekly, portfolio, price_alerts, portfolio_value)
├── requirements.txt
├── config.yaml           # approved watchlist + LLM settings
├── scripts/
│   ├── entry.py         # Sunday entry funnel: prescreen → Wyckoff LLM → news → top-5 tiered
│   ├── exit.py          # daily exit-watch (default --section portfolio, exit mode)
│   ├── prescreener.py    # quant screener (no LLM): S&P 500 + NASDAQ 100 → ~30 candidates
│   ├── events.py         # programmatic range/Spring/SOS/LPS detection (no LLM) + calibration CLI
│   ├── analysis.py       # Wyckoff LLM analysis (entry/exit modes + market & event context)
│   ├── news.py           # Finnhub + claude-proxy news/fundamental validation
│   ├── finnhub.py        # Finnhub client (earnings calendar, market cap, news, consensus)
│   ├── price_alerts.py   # daily ≥3.5% move scan (no LLM)
│   ├── portfolio_value.py# daily P&L valuation report
│   ├── explain.py        # on-demand plain-language deep dive for one ticker
│   ├── data.py           # Yahoo Finance OHLCV fetch
│   ├── holdings.py       # portfolio state (data/holdings.json)
│   ├── manage.py         # CLI: add/remove holdings and watchlist
│   └── notifier.py       # Telegram sender (4096-char auto-split)
├── data/
│   ├── holdings.json              # portfolio positions
│   ├── factor_tags.yaml           # factor concentration tags (committed)
│   └── watchlist_candidates.json  # latest prescreener output (not committed)
└── logs/
```

## Schedule

| Job | id | Cron (UTC) | Israel Time | Description |
|-----|-----|-----------|-------------|-------------|
| Weekly entry funnel | `wyckoff_weekly` | `0 8 * * 0` | 11:00 Sun | Prescreen → Wyckoff → up to 5 tiered picks |
| Daily exit-watch | `wyckoff_portfolio` | `0 20 * * 1-5` | 23:00 Mon–Fri | Held positions, distribution signals |
| Price alerts | `wyckoff_price_alerts` | `1 20 * * *` | 23:01 daily | ≥3.5% move scan, no LLM |
| Portfolio value | `wyckoff_portfolio_value` | `5 20 * * 1-5` | 23:05 Mon–Fri | Daily P&L valuation |

## Hermes Tool: Update Holdings from an Uploaded Broker Export (.xlsx)

When the user **uploads their broker "online balances" (יתרות מקוונות) spreadsheet** and asks to update/sync their holdings ("update my holdings from this file", "תעדכן את התיק מהקובץ"), use the deterministic importer — do **not** parse the spreadsheet yourself (it is Hebrew with redundant columns; manual parsing silently gets a quantity or cost wrong, and holdings data must be exact — it drives every trim/stop).

The uploaded file is cached locally as a document attachment (`~/.hermes/cache/documents/doc_*_<name>.xlsx`). Pass that path to the importer:

```bash
# 1. DRY-RUN first — prints the per-ticker diff, writes nothing:
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/import_holdings.py "<uploaded_file_path>"

# 2. After the user confirms the diff, apply it (backs up holdings.json first):
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/import_holdings.py "<uploaded_file_path>" --apply
```

**Your job:**
1. Run the dry-run and relay the printed diff (qty/cost changes per ticker) to the user in their language.
2. Wait for explicit confirmation, then re-run with `--apply`.
3. If the output lists **UNMAPPED rows**, a newly-bought security has no ticker mapping yet — tell the user its name + security-number; it needs a one-line entry added to `SECNUM_TO_TICKER` in `scripts/import_holdings.py` (the importer refuses `--apply` while any row is unmapped, so it never imports a position to the wrong ticker).

The importer matches each position by its **stable Israeli security-number** (מספר נייר), reads quantity + average cost (`.TA` costs are agorot, stored as-is), preserves the risk-state scale-out baseline (so a trimmed position still reads as partially scaled-out), and never deletes a holding that is merely absent from the file.

## Known Pitfalls & Workarounds

### Yahoo Finance API Rate Limiting

**Problem:** Yahoo Finance returns `"Edge: Too Many Requests"` without standard HTTP error codes, causing scripts to hang indefinitely. This is a **sliding window rate limit** (typically several hours of cooldown after ~30-50 rapid requests), not a per-day quota.

**Fix Applied (`scripts/data.py`):**
- Exponential backoff: 2s → 4s → 8s → 16s → 32s with jitter
- `MAX_RETRIES = 5` before failing
- 1-second delay between ticker batches (`scripts/exit.py`)
- Check for `"Too Many Requests"` **string in response body** (not just HTTP codes)

**Fallback Pattern:** When Yahoo Finance is blocked after retries, the script fails gracefully and processes remaining tickers. Consider adding:
- **12-hour cache layer** — only refetch if data is stale
- **Finnhub fallback** — use for price data when Yahoo is blocked (though Finnhub has limited ETF coverage)

**Debug Tools:**
- `scripts/verify_yahoo_limits.py` — Reproduce and diagnose rate limiting (test with `--count 60 --sleep 1`)
- `references/yahoo-finance-rate-limit.md` — Complete troubleshooting guide

## Notes

- Data source: Yahoo Finance API (direct) — free, no API key, covers all major ETFs and stocks
- Prescreener: pure Python/math, no LLM — fetches concurrently (10 workers), takes ~2-3 min for ~600 tickers. Filters: regime-aware off-high floor, two-sided rel-perf vs SPY, sector-relative strength, liquidity (ADV), 5 accumulation-shape criteria
- LLM analysis: via the local claude-proxy on 120 days OHLCV (entry vs exit prompt) — not algorithmic signal detection
- News/fundamentals: Finnhub (earnings calendar, market cap, company news, analyst consensus) + local claude-proxy reasoning
- Event detection: `events.py` programmatically detects the trading range, Spring, SOS, and LPS and feeds them to the LLM as ground truth (so criteria 3–8 aren't eyeballed). It also has a **markup-pullback lane** (a confirmed breakout that pulled back and holds above the breakout level on contracting volume) — these candidates bypass the off-high floor, so the funnel can surface leaders near their highs (digest label: `Markup-pullback LPS …`). Calibrate/inspect with `python scripts/events.py TICKER [days]`; ground-truth tests in `tests/validate_events.py` (synthetic) and `tests/validate_events_tier2.py` (real snapshots). Thresholds in `references/wyckoff-events-glossary.md`
- Wyckoff is a swing/position methodology; daily candles are the appropriate timeframe
- Treat recommendations as a second opinion, not automated trading signals
- Prescreener candidates in `data/watchlist_candidates.json` are suggestions only; you decide what goes in `config.yaml`

- TASE-listed securities use `.TA` suffix on Yahoo Finance (e.g., `AMOT.TA`, `TCH-F3.TA`); not all are available
