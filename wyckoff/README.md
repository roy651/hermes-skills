# Wyckoff — entry & exit mechanisms

Two complementary pipelines built on the Wyckoff method, sharing one data / analysis / notify core.
Both run on the mini-PC (`~/.hermes/skills/wyckoff`, kind-B skill), are source-controlled in this repo,
and report to Telegram.

| Pipeline | Script | Cadence | Job |
|---|---|---|---|
| **Entry funnel** — surface a few high-quality buy candidates from a broad universe | `entry.py` | Sunday 08:00 UTC | `wyckoff_weekly` |
| **Exit watch** — manage *held* positions (scale-out / stops) | `exit.py --section portfolio` | weekly, Sunday (~12:00 IL) | Hermes cron |

> Design split: **mechanics decide, the LLM assists.** Programmatic detectors + a deterministic engine
> make the calls; the LLM grounds the entry read and *validates* the exit verdicts — it is never the
> sole decider.

---

## Shared core
- **`data.py`** — `fetch_ohlcv(ticker, days)` → `TickerData(df, name, currency)` via Yahoo. ILS prices are
  normalised agorot→ILS (÷100). (ILS **avg_cost** in `holdings.json` is also in agorot — `digest`/exit code ÷100.)
- **`analysis.py`** — local claude-proxy LLM (`_call_llm`, 3 retries). Prompts: `_SYSTEM` (entry),
  `_SYSTEM_EXIT` (legacy held read), `_SYSTEM_VALIDATE` (exit validator). Functions `analyze()`, `validate()`.
- **`events.py`** — programmatic Wyckoff events (range, Spring, SOS, LPS, markup-pullback). Pure pandas.
- **`deterioration.py`** — exit-side mirror of `events.py` (Upthrust/UTAD/SOW/LPSY/support-break) + the 0–9 exit score.
- **`risk.py` / `ladder.py`** — deterministic stops/state and the scale-out/in decision (exit engine).
- **`digest.py`** — Telegram blocks: `format_block` (entry/watchlist), `format_managed_block` (exit engine block).
- **`finnhub.py` / `news.py`** — Finnhub free-tier (company news, analyst consensus, earnings calendar, market cap)
  + LLM news-validation. **Entry funnel only** (see Notes).
- **`holdings.py` / `manage.py`** — `data/holdings.json` (`{ticker: {qty, avg_cost}}`) + watchlist (`config.yaml`) CLI.
- **`notifier.py`**, **`config.yaml`** (watchlist, model, `lookback_days`).

---

## ENTRY funnel (`entry.py`)
From ~600 names → ≤5 high-conviction buy candidates.

1. **Universe** (`prescreener._get_universe`) — S&P 500 + NASDAQ 100 + ~20 sector/asset ETFs (~600, deduped);
   S&P names mapped GICS→sector ETF for sector-relative strength.
2. **Market regime** (`_get_spy_context`) — SPY 52w-off-high + 6m/12m returns, and a **regime-aware off-high floor**:
   require a 25% pullback when SPY is at all-time highs, sliding to 15% at a 20% SPY drawdown
   (`0.15 + 0.5·max(0, 0.20 − spy_off_high)`). Don't buy "dips" that aren't dips in a hot tape.
3. **Prescreen** (`screen_universe`, quant only; ~600 → ~30):
   - Hard gates: 20-day ADV ≥ **$20M**; 6m performance within **±30pp of SPY** and ≤ +30pp vs sector; price ≥ **90% of MA200**.
   - 5-criterion score (off-high in [floor, 65%], above MA200, ATR contraction, volume contraction, BB squeeze);
     **accumulation lane admits ≥ 3/5**.
   - **Markup-pullback lane** (`events.detect_markup_pullback`) — a confirmed breakout (3% above a 150-bar ceiling)
     pulling back 3–15% but holding above the breakout on contracting volume; an effort filter rejects climactic
     advances. Bypasses the off-high / rel-perf gates; capped at 10.
   - Top 30 by score → `data/watchlist_candidates.json`.
4. **Earnings gate** (`finnhub.earnings_within`, 14d) — drop names reporting within two weeks (OHLCV is noisy across earnings).
5. **LLM Wyckoff read** (`analyze`, entry mode, ×4 parallel) — programmatic events passed as *ground truth*; LLM returns
   phase, `criteria_met` (0–9, the nine entry criteria), recommendation, entry_zone, stop, note.
   **`_reconcile_with_events`**: with no confirmed SOS/LPS/markup-pullback, demote markup→low-confidence, downgrade
   buy→watch, cap criteria at 6.
6. **News gate** (top 8 by composite, `news.validate`) — Finnhub 30-day headlines + analyst consensus → LLM checks for a
   disqualifying corporate event (M&A, going-private, regulatory, severe miss). Fail-closed.
7. **Composite + gates + tiers**
   - `composite = (0.4·criteria/9 + 0.4·event/4 + 0.2·quant/5) · (0.5 + 0.5·has_event)` — structure-dominant; a confirmed
     entry event roughly doubles the rank.
   - **STRONG gates (all four):** rec ∈ {buy, add}; criteria ≥ 7; news clean; has a confirmed entry event.
   - Tiers (≤ 5 picks total): 🟢 **STRONG** (accumulation, range SOS/LPS) · 🟣 **MARKUP-PULLBACK** (confirm before acting —
     bypassed the off-high floor, quiet-top risk) · 🟡 **BORDERLINE** (shows which gates failed).
   - **Suggested size:** 9 crit + event ≥ 4 → full · 7–8 → 50% · 5–6 → 30% · < 5 → starter.
8. **Digest** (`format_block`, `gate_action=True`) — phase, criteria, signals, Buy/Entry/Stop (limit-vs-pullback aware),
   ADV/cap/sector, suggested size, analyst consensus, news flag. Header = SPY regime; factor-concentration warning if ≥ 3 share a tag.

---

## EXIT watch (`exit.py --section portfolio`)
Manage *held* positions with a rigorous, convergent, deterministic engine; the LLM only validates.

**Pipeline:** fetch → engine → validate → assemble.

1. **Fetch** all holdings' OHLCV in parallel; pull USD/ILS for portfolio-value & concentration math.
2. **Deterministic engine** (sequential, per holding — no LLM):
   - **`risk.py`** — trailing stop = the *tighter* of an ATR-chandelier (`highest_high − 3·ATR`) and a 20-bar structure low.
     Persists `data/positions_state.json`: `baseline_qty`, `max_stage`, `highest_high`, `entry_date` (first-seen; see Notes).
     `stop_hit` = price < stop.
   - **`deterioration.py`** — **0–9 exit score**: structural (Upthrust/UTAD/SOW/LPSY/support-break) + computable
     (rel-strength flip, MA rollover, distribution volume, off-highs). Plus an **`established_markdown` floor flag**
     (below the medium MA + still making fresh lows + at a loss) and a **`has_structural`** flag.
   - **`ladder.py`** — one convergent action: **ADD / HOLD / TRIM to N shares / EXIT**.
     - Score → stage: **3–4 → trim to 75% · 5–6 → 50% · 7+ / stop-hit → exit.**
     - Targets are **absolute shares of `baseline_qty`** → repeated signals converge (no "trim 25% of current" regress);
       **`max_stage` ratchets** (never un-trims).
     - Floors/caps: a non-structural *bleed* → trim 25% max (the stop does the rest); `established_markdown` → **≥ trim 25%**;
       a **20% concentration cap** (over-cap → trim toward cap); **DGRO is core-exempt** (always HOLD); a clean (0/9) name
       with a fresh entry setup below half-cap → **ADD toward a half (~10%) position**.
3. **Validator** (`analysis.validate`, parallel) — the LLM **stress-tests** the engine verdict: **✅ confirmed** or
   **⚠️ flag** with a specific reason it might be wrong (ex-div, index rebalance, thin-volume artifact, a catalyst, a misread base).
   **Advisory only** — it never changes the action. LLM-unavailable → no validation line (action stands).
4. **Digest** (`format_managed_block`) — `qty @ cost · price (P&L)` · structure label + exit N/9 · signals ·
   **action + Δshares · stop + % away · % of portfolio** · ✅/⚠️ validation.

**Authority:** the engine *decides*; the validator *checks*. Flag sensitivity = *sensitive* (~half flagged; raise the
threshold if it gets noisy); validator stays *advisory*.

---

## Notes & limitations
- **Entry date has no effect on the exit mechanism.** `entry_date` in `positions_state.json` is a first-seen marker — not
  read by any calculation, not displayed. The trailing stop's `highest_high` anchors to first-observation (correct for a
  trailing stop — it shouldn't reach back to an old peak). True purchase dates live in Hermes' portfolio DB; no wiring needed.
- **News/Finnhub is entry-only.** The exit validator currently *infers* catalysts (ex-div, earnings) from price + model
  knowledge — there is no live feed on the exit side. **Planned enhancement:** wire `finnhub.py` (earnings_within + ex-div +
  headlines) into `validate()` so those flags are factual, not guessed.
- **Known engine gaps** (the validator covers these via ⚠️ flags today; to be mechanised): range-less high-volume breakdowns
  (a deep-markdown SOW with no prior range); thin-volume names where the volume criteria mislead. Calibration TODO: a
  **min-loss threshold** on the `established_markdown` floor so shallow basers aren't trimmed.
- **LLM = local claude-proxy** (flaky under load): both pipelines retry ×3 and degrade gracefully — the exit engine renders
  without the LLM, and news/validation is simply omitted when unavailable.

---

## Deploy & verify
Edit here (Mac) → `git push` → mini-PC `git pull` → copy changed `scripts/` into `~/.hermes/skills/wyckoff/scripts/`
(preserve `.venv` / `.env` / `data/` / `logs/`); reinstall deps if `requirements.txt` changed.

- **Module self-tests:** `.venv/bin/python scripts/{risk,deterioration,ladder}.py`
- **Dry-runs (print, no Telegram, no state write):** `… scripts/exit.py --section portfolio --dry-run` · `… scripts/entry.py --dry-run`
