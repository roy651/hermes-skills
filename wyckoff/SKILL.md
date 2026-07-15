---
name: wyckoff
description: Wyckoff method analysis for ETFs and stocks via Telegram. Weekly entry funnel (prescreen → Wyckoff LLM → up to 5 tiered buy picks) plus a daily portfolio exit-watch for distribution signals.
version: 1.4.0
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

## Hermes Tool: Fibonacci Confluence Grid (`fib.py`)

Deterministic (no-LLM, no-Telegram) retracement/extension helper — for "is there a fib level near X?", Elliott/wave-target questions, or seeding a `watchlist_levels` support/resistance from structure. Takes a swing and prints the retracement grid (support/resistance *inside* the swing) + extension grid (measured-move targets *beyond* the terminal pivot) + a nearest-bracket levels suggestion.

```bash
cd ~/.hermes/skills/wyckoff
.venv/bin/python scripts/fib.py SNPS                       # auto-detect dominant swing over 1y
.venv/bin/python scripts/fib.py SNPS --high 651.73 --low 365.74 --dir down   # pin the swing by hand
.venv/bin/python scripts/fib.py SNPS --lookback 400 --json # widen auto-window / machine output
```

Direction sets interpretation: UP swing → retracements are pullback SUPPORT, extensions are upside TARGETS; DOWN swing → retracements are bounce RESISTANCE, extensions are downside TARGETS (sub-zero down-extensions are dropped). Auto-detect finds the extreme high/low over `--lookback` and infers direction from which printed last; **override with `--high/--low` when you have specific pivots** (auto max/min can miss the swing you care about).

**Discipline (critical):** fib levels are **confluence-only — they confirm a Wyckoff signal or mark invalidation, never trigger an entry standalone.** Seed a fib level into `watchlist_levels` **only where it lines up with a real Wyckoff decision level** (spring/LPS/SOS), not just because the arithmetic produced it. This mirrors the user's standing rule: any new analytical lens must be deterministic-first and confluence-only; reject indecisive/standalone use. (Same reason there is deliberately **no automated wave-counting** — the machine does Fibs + rule-checks; wave *labels* stay human/LLM-judged.)

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

### What the watchlist IS — an entry-pipeline tripwire (curation rule)

The watchlist is **not** a generic favorites list. It is a curated set of names you'd buy *only if* Wyckoff hands you a defined, low-risk entry — a **spring that holds, an LPS, or a confirmed SOS/markup breakout**. Curate it for that purpose:

- **Only include names in an accumulation / spring-watch posture.** A name still rolling over with no base gives the scan nothing crisp to watch (only a blunt %-move).
- **Drop distributive names.** If a name prints SOW + LPSY / is topping (e.g. GLD, dropped 2026-07-06), it's the *opposite* of a spring setup — it doesn't belong in an entry list. Keep it only if the user explicitly wants to stalk a spring *below* support that reclaims (a different, speculative setup).
- Two cadences work together: **weekly `exit.py --section watchlist` = deep LLM phase re-assessment**; a **daily no-LLM scan = the cheap level tripwire** that fires when a name hits a pre-defined level, so the user doesn't miss the entry "hole" between the Sunday LLM reads.

### Daily no-LLM watchlist scan — params are coarse alert bands, not entry rules

Deterministic **level-crossing is exactly what a no-LLM scan does best** ("is price ≤ support?" is pure arithmetic). So per-name trigger levels are **not** meaningless for a mechanical scan — they're what upgrades it from `price_alerts.py`'s blunt 3.5%-mover into a *targeted* tripwire. Design rules:

- **Because the user LLM-verifies every alert manually afterward, tune params as coarse "wake me up" bands, not precise entry signals.** Fire on *approach* (~1% of a level), not only exact touch — better early than missed. No volume-confirm / spring-vs-fail discrimination in the scan; that's the LLM verification's job.
- **Store levels in a sidecar map, keep `watchlist:` a bare ticker list.** Enriching watchlist entries to objects breaks `exit.py`/`manage.py` (they read bare strings). Add a separate `watchlist_levels: { TICKER: {support, resistance} }` the scan reads; a name with no clean structure → generic %-move fallback until a base forms.
- Alert semantics: within ~1% of `support` → "spring/support watch"; near/above `resistance` → "breakout/SOS watch". End every alert with "→ reply to LLM-verify."

## Hermes Tool: Weekly Parked / Thesis-Watch (`parked_scan.py`)

The `watchlist` is for names with a *defined* entry (levels → daily tripwire). Names whose thesis is
alive but which have **no low-risk entry yet** (still falling-knife, awaiting a base) don't belong there
— in the daily scan they'd only fire noise, and level-less they'd sit silent and forgotten. They go in a
separate `parked:` list (config.yaml) with a **weekly** no-LLM touch instead:

```bash
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/parked_scan.py   # weekly digest, no credits
```

Per name it prints price, 1-week move, the 0-9 deterioration score, and a verdict. The one thing it
watches for is a name that **stops deteriorating / starts basing** (score ≤2 and no ma_rollover/rel_weak
→ 🟢) — the cue to **promote it into `watchlist` and seed levels**, at which point the daily tripwire
takes over. A high-score name with structure → ⚠️ distributive (thesis at risk, consider dropping). Runs
as `wyckoff_parked_watch` (Sundays 07:00 UTC). Promote/demote by moving a ticker between `parked:` and
`watchlist:` — same "curated by character" discipline as the watchlist itself.

## Hermes Tool: Trade-Execution Ledger (`trade_log.py`)

holdings.json is a **snapshot** (qty + avg_cost, overwritten on every broker re-import) with **no dates**;
`positions_state.entry_date` is a tracker baseline, not a real fill date. So actual entries/exits can't be
reconstructed after the fact — do **not** try to store dates in holdings.json (they'd be wiped on re-import
and can't be back-derived from the balances export anyway). Instead, capture fills going forward in an
append-only ledger `data/trade_log.jsonl` (gitignored, runtime-only like holdings.json):

```bash
# When the user reports a fill in chat, record it:
trade_log.py add --date 2026-07-11 --ticker KMB --side buy --qty 10 --price 112.41 --note "starter"
# Periodic entry-execution review ("how did the last N weeks' entries do?"):
trade_log.py review --weeks 4          # prints; add --send to also post to Telegram
```

`review` fetches current price per fill and shows % since entry (buys) or since exit (sells: a good exit
shows the name fell further). Deterministic, no Claude credits. This is the source of truth for the
"review my recent entries" question — seed it whenever a fill is mentioned.

## Instrument character decides the tool — exempt bonds/rate-driven names from the trailing stop

Wyckoff (and its trailing stop) is only valid for instruments that trend on **supply/demand** —
equities, and commodity / sector / index ETFs (GLD, SLV, an Israeli banks or real-estate ETF) which
genuinely accumulate and distribute. It is the WRONG tool for instruments priced by a **formula**:
bonds / Treasury ETFs (price = rates × duration), buffered/defined-outcome, and leveraged/inverse ETFs.
On those a trailing stop **mis-fires** — it forces a sale at the *yield high* on rate noise (e.g. XFIV,
the BondBloxx 5-yr Treasury ETF, tripped `stop_check` on a 3-cent / 0.06% close-through on a broad-red
day; a bond's drawdown while it earns carry is expected and self-correcting, not deterioration).

- **Sort by character, not by the ETF-vs-stock wrapper.** A sector/commodity ETF trends → full Wyckoff.
  A bond ETF is rate-driven → exempt. (Don't be fooled by a fund-looking ticker: an Israeli banks ETF
  and a real-estate ETF are equity *sector* baskets that trend → they stay in the Wyckoff book.)
- **Tag exempt holdings** in `holdings.json` with `"asset_class": "bond"` (also `treasury` / `cash` /
  `money_market`) or explicit `"no_trailing_stop": true`. `holdings.no_trailing_stop(h)` centralises the
  check: `stop_check.py` skips them (and logs which were skipped); `exit.py` does not feed their stop into
  the scale-out ladder. (Shipped 2026-07-11.)
- **Their exit is a thesis/rate decision, not a mechanical stop** — there is no reliable automated
  price-exit for a bond. Discipline them via the monthly bond-sleeve review below, plus (optionally) a
  *wide, static* informational alert band; the user decides. A user trimming such a name is a discretionary
  duration/rate call, not a stop — treat it as consistent, don't argue it against the (absent) stop.

## Hermes Tool: Monthly Bond-Sleeve Review

`bond_review.py` is the exit discipline for the exempt (bond/rate-driven) sleeve, since those carry no
trailing stop. Once a month it gathers price + unrealised P/L + the rate backdrop (5-yr Treasury yield via
`^FVX`, level and 1m/3m trend) and asks the LLM one focused question per name — is the duration thesis
intact: **HOLD / TRIM / ADD** — then posts to Telegram. It reviews only holdings where `no_trailing_stop(h)`
is true, and is silent if the sleeve is empty. Uses `analysis.backend_warmup()` + a DEGRADED banner like
entry/exit.

```bash
cd ~/.hermes/skills/wyckoff && .venv/bin/python scripts/bond_review.py --dry-run   # preview, no Telegram
```

Examples → what to run:
- "review my bonds" / "how's the bond sleeve / XFIV thesis?" → `bond_review.py`
- Runs monthly as `wyckoff_bond_review` (Hermes cron; register alongside the other jobs).

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
├── config.yaml           # watchlist/parked/levels + LLM settings — GITIGNORED runtime-only PII (template: config.example.yaml)
├── scripts/
│   ├── entry.py         # Sunday entry funnel: prescreen → Wyckoff LLM → news → top-5 tiered
│   ├── exit.py          # daily exit-watch (default --section portfolio, exit mode)
│   ├── stop_check.py     # daily trailing-stop breach check (no LLM); skips no_trailing_stop holdings
│   ├── bond_review.py    # monthly LLM review of the bond/rate-driven sleeve (no trailing stop applies)
│   ├── prescreener.py    # quant screener (no LLM): S&P 500 + NASDAQ 100 → ~30 candidates
│   ├── events.py         # programmatic range/Spring/SOS/LPS detection (no LLM) + calibration CLI
│   ├── analysis.py       # Wyckoff LLM analysis (entry/exit modes + market & event context)
│   ├── news.py           # Finnhub + claude-proxy news/fundamental validation
│   ├── finnhub.py        # Finnhub client (earnings calendar, market cap, news, consensus)
│   ├── price_alerts.py   # daily ≥3.5% move scan (no LLM)
│   ├── portfolio_value.py# daily P&L valuation report
│   ├── explain.py        # on-demand plain-language deep dive for one ticker
│   ├── fib.py            # deterministic Fibonacci retracement/extension confluence grid (no LLM/Telegram)
│   ├── parked_scan.py    # weekly no-LLM thesis-watch on the parked: list
│   ├── trade_log.py      # append-only executed-fill ledger + entry-performance review
│   ├── import_holdings.py# secnum-matched broker-export (.xlsx) importer (dry-run + --apply)
│   ├── data.py           # Yahoo Finance OHLCV fetch → TickerData(df, name, currency); .TA agorot→ILS normalized
│   ├── holdings.py       # portfolio state (data/holdings.json); no_trailing_stop() = bond-sleeve check
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

### Smoke-testing a script can POST to the user's Telegram

Most digest scripts **send to Telegram on a bare run** — running one to "just test it" fires a real message to the user's channel. Not all have a preview flag:
- **Have `--dry-run` / `--no-send`:** `entry.py`, `exit.py` (use these for previews — see the on-demand sections above).
- **NO preview flag — a bare run sends** (silently, only when something trips): `watchlist_scan.py`, `parked_scan.py`, `stop_check.py`, `bond_review.py`, `price_alerts.py`, `portfolio_value.py`.

So to **validate config/logic without sending**, don't invoke the sender script — exercise the piece under test in isolation instead (e.g. `python -c "import yaml; ..."` to confirm a config parses, or import the pure function). If you must run the full script to smoke-test, tell the user it will (or did) post, since it duplicates their scheduled digest. (Learned 2026-07-14: a `watchlist_scan.py` smoke-test re-sent that day's XOM/IWM scan.)

### `config.yaml` is gitignored runtime-only PII — watchlist changes are NOT committed

As of 2026-07-14 the user reclassified the **watchlist / parked list / watchlist_levels** as PII (same class as holdings): `config.yaml` is now **gitignored**, lives runtime-only in the git checkout, and a neutral `config.example.yaml` (empty lists) is the tracked template. Consequences:
- Editing the watchlist/parked/levels is a **runtime-only edit — do not `git commit` it** (and it won't show in `git status`). Only *neutral code* (scripts) gets committed/pushed.
- A `git pull` on the Mac will **delete** the Mac's working-tree `config.yaml` (it's ignored + removed from the index) — copy it aside first if a local copy is wanted. The mini-PC runtime copy is untouched.
- When adding a new git-only helper script, commit the script; the watchlist/level change that motivated it stays out of git.

### Diagnosing an entry/exit bug: check `git diff`, not just committed code

wyckoff **runs from the git checkout** (`~/.hermes/skills/wyckoff` is a symlink to `~/hermes-skills/wyckoff`), so the **working tree is the live code** — an *uncommitted* change is already running in the next scheduled job. Before you confirm a bug diagnosed from the committed source (especially a diagnosis inherited from another session/chat), run `git diff scripts/<file>.py`: the fix may already be sitting uncommitted in the working tree, making the committed-code reading a **phantom bug**. (Learned 2026-07-15: an inherited analysis correctly showed the entry `_gates()` `C_news` gate made 🟢 STRONG structurally unreachable — but `git diff` revealed that exact fix already applied and un-committed, i.e. live but drift-risk. The right move was to surface + commit it, not re-implement.) Design note this confirmed: entry STRONG tiering is **news-less by design** — news is a downstream verify/veto lens on the shortlist (adverse news *demotes* STRONG→BORDERLINE; absence never blocks), **not** a gate. Don't "fix" the absence of a news gate.

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
