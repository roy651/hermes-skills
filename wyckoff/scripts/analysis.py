from __future__ import annotations
import json
import os
import re
import time
import requests
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path.home() / ".hermes" / ".env")

API_URL = os.environ.get("LLM_API_URL", "http://localhost:8765/v1/chat/completions")

_SYSTEM = """You are a Wyckoff method analyst. Analyze the OHLCV data and return a JSON object — no markdown, no explanation.

Four Wyckoff phases:
- accumulation: consolidation below resistance, volume contracting, institutional buying
- markup: sustained uptrend, expanding volume on advances, contracting on pullbacks
- distribution: consolidation near highs, erratic volume, institutional selling
- markdown: sustained downtrend, volume expands on declines

Key events (identify by date when visible in data):
- SC: Selling Climax — extreme volume at a low, signals panic exhaustion
- AR: Automatic Rally — bounce off SC low
- ST: Secondary Test — retest of SC on lower volume
- Spring: brief pierce below support quickly recovered — strongest accumulation signal
- SOS: Sign of Strength — strong advance with high volume after Spring
- LPS: Last Point of Support — low-volume pullback after SOS, ideal entry
- UT: Upthrust — brief pierce above resistance then closes weak
- UTAD: Upthrust After Distribution — final UT confirming distribution
- LPSY: Last Point of Supply — weak rally in markdown
- SOW: Sign of Weakness — volume-heavy decline confirming distribution

Nine entry criteria (for long/accumulation setups):
1. Broad market trend is up
2. This instrument shows relative strength vs. market
3. A horizontal trading range is clearly visible
4. The range has persisted weeks to months
5. A final shakeout or Spring occurred
6. A SOS appeared with volume confirmation
7. An LPS formed on lower volume than the SOS
8. Price action tightening near resistance
9. No major macro/fundamental headwinds

For ETFs tracking the broad market (SPY, VTI, QQQ), criteria 1 and 2 are evaluated relative to global macro context. Criteria count is still 0–9.

Return ONLY valid JSON:
{
  "phase": "accumulation|markup|distribution|markdown|unclear",
  "phase_confidence": "high|medium|low",
  "key_events": ["Spring on 2026-03-15", "SOS on 2026-03-22"],
  "active_signals": ["LPS forming"],
  "criteria_met": 7,
  "recommendation": "buy|add|hold|reduce|sell|watch|pass",
  "entry_zone": "225–228" or null,
  "stop": "219.00" or null,
  "note": "One concise sentence summary."
}"""


_SYSTEM_EXIT = """You are a Wyckoff method analyst reviewing a CURRENTLY HELD position for EXIT risk. Analyze the OHLCV data and return a JSON object — no markdown, no explanation.

Your job is defensive: detect distribution and weakness early, but do not cry wolf. "hold" is the default for a healthy position; only escalate when distribution evidence is concrete.

Four Wyckoff phases:
- accumulation: consolidation below resistance, volume contracting, institutional buying
- markup: sustained uptrend, expanding volume on advances, contracting on pullbacks
- distribution: consolidation near highs, erratic volume, institutional selling
- markdown: sustained downtrend, volume expands on declines

Distribution / weakness signals to prioritize (identify by date when visible):
- UT: Upthrust — pierce above resistance that closes weak
- UTAD: Upthrust After Distribution — final UT confirming distribution (strong exit signal)
- SOW: Sign of Weakness — volume-heavy decline breaking support
- LPSY: Last Point of Supply — weak low-volume rally failing below prior highs
- Break below a prior LPS / loss of an established support level on rising volume
- Climactic or churning volume at highs with no further price progress
- Markup exhaustion: SOS attempts failing, narrowing upward thrusts

Recommendation guidance for a held position:
- hold: trend intact, no distribution evidence (DEFAULT)
- reduce: early distribution signs (UT, SOW, support tested on volume) — trim risk
- sell: distribution confirmed (UTAD, major support broken on volume, markdown underway)
- buy/add: only for a clean, confirmed markup pullback (rare in exit review)

The nine entry criteria still apply for context (count 0–9, same as long setups).

Return ONLY valid JSON:
{
  "phase": "accumulation|markup|distribution|markdown|unclear",
  "phase_confidence": "high|medium|low",
  "key_events": ["UT on 2026-03-15", "SOW on 2026-03-22"],
  "active_signals": ["distribution forming"],
  "criteria_met": 4,
  "recommendation": "buy|add|hold|reduce|sell|watch|pass",
  "entry_zone": "225–228" or null,
  "stop": "219.00" or null,
  "note": "One concise sentence summary."
}"""


_SYSTEM_VALIDATE = """You VALIDATE a mechanical Wyckoff exit decision. You do NOT make your own call and you do NOT merely restate it.

A deterministic engine has decided an action for a CURRENTLY HELD position from price/volume rules
(trailing stops, distribution detectors, a 0-9 deterioration score, a scale-out ladder). Stress-test that
decision against the data and against anything price-only mechanics cannot see.

Return valid=true only if the mechanical read is clearly sound. Return valid=false (a FLAG) if you see a
SPECIFIC reason it may be wrong, e.g.:
- a "support break" / volume spike that is really an ex-dividend gap, a stock split, or an index rebalance
- a one-off bad tick or thin-volume artifact (common in small ILS names)
- a known catalyst the price cannot show (an earnings/guidance date, M&A, a binary event)
- the structure is misread (e.g., a base forming, not distribution)

Reply EITHER as JSON: {"valid": true|false, "note": "<one concrete line>"}   (valid=false means FLAG)
OR as a SHORT prose note (1-2 sentences — the key confirming fact, or the specific concern) ending with a
final line, exactly: VERDICT: CONFIRM   (or)   VERDICT: FLAG
Keep it brief — no multi-section markdown essay, no hedging, no boilerplate."""


def _market_context_block(market_ctx: dict) -> str:
    """Render SPY regime + this instrument's relative strength so the LLM can ground
    criteria 1 (broad market trend) and 2 (relative strength vs market)."""
    off = market_ctx.get("spy_pct_off_high")
    r6 = market_ctx.get("spy_ret_6m")
    r12 = market_ctx.get("spy_ret_12m")
    lines = ["Market context (S&P 500 / SPY):"]
    if off is not None and r6 is not None and r12 is not None:
        lines.append(
            f"- SPY is {off*100:.1f}% off its 52-week high; 6-month return {r6*100:+.1f}%, "
            f"12-month {r12*100:+.1f}%."
        )
    rel6 = market_ctx.get("rel_6m")
    rel12 = market_ctx.get("rel_12m")
    if rel6 is not None and rel12 is not None:
        lines.append(
            f"- This instrument vs SPY: 6m {rel6:+.1f}pp, 12m {rel12:+.1f}pp "
            f"(positive = outperforming the market)."
        )
    return "\n".join(lines)


def analyze(
    ticker: str,
    df: pd.DataFrame,
    held: bool = False,
    name: str = "",
    mode: str = "entry",
    market_ctx: dict | None = None,
    detected_events: list[str] | None = None,
) -> dict:
    context = "Currently HELD in portfolio." if held else "On watchlist (not held)."
    label = f"{ticker} ({name})" if name and name != ticker else ticker
    system = _SYSTEM_EXIT if mode == "exit" else _SYSTEM
    csv = df.to_csv()
    user_parts = [f"Ticker: {label}", context]
    if market_ctx:
        user_parts.append("\n" + _market_context_block(market_ctx))
    if detected_events:
        user_parts.append(
            "\nProgrammatically detected Wyckoff events (ground truth from price/volume — "
            "trust these over your own visual reading of the numbers): "
            + ", ".join(detected_events) + "."
        )
    user_parts.append(f"\nOHLCV (last {len(df)} trading days):\n{csv}")
    result = _call_llm(system, user_parts)
    result["ticker"] = ticker
    return result


# Degradation tracking: the claude-proxy silently falls back to a cheaper model when claude-code is
# down (e.g. an expired login). It reports the real backend in the X-Proxy-Backend header; we record
# any non-claude backend so a report can WARN instead of shipping a qwen read as if it were Claude.
_FALLBACK_BACKENDS: set[str] = set()


def _note_backend(backend: str) -> None:
    if backend and not backend.lower().startswith("claude"):
        _FALLBACK_BACKENDS.add(backend)


def reset_degradation() -> None:
    _FALLBACK_BACKENDS.clear()


def degradation() -> set:
    """Non-claude backends the proxy fell back to since the last reset (empty set = clean Claude run)."""
    return set(_FALLBACK_BACKENDS)


def backend_warmup(timeout: int = 30) -> tuple[bool, str]:
    """One tiny probe before the concurrent analysis batch. Two jobs: (1) trigger a Claude OAuth token
    refresh while only ONE request is in flight — a batch racing an expired token is what silently
    drops every call to the fallback model; and (2) report the live backend. Records a fallback too."""
    model = os.environ.get("WYCKOFF_LLM_MODEL", "claude-opus-4-6")
    try:
        resp = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {os.environ.get('LLM_API_KEY', 'local')}",
                     "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": "ok"}],
                  "max_tokens": 1, "temperature": 0},
            timeout=timeout,
        )
        backend = resp.headers.get("X-Proxy-Backend", "")
    except Exception as e:
        return False, f"probe-failed: {e}"
    _note_backend(backend)
    return backend.lower().startswith("claude"), backend or "unknown"


def _call_llm(system: str, user_parts: list[str], raw: bool = False) -> dict | str:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(user_parts)},
    ]
    model = os.environ.get("WYCKOFF_LLM_MODEL", "claude-opus-4-6")
    payload = {"model": model, "messages": messages, "temperature": 0, "max_tokens": 512}
    headers = {
        "Authorization": f"Bearer {os.environ.get('LLM_API_KEY', 'local')}",
        "Content-Type": "application/json",
    }
    # Retry: the local proxy can return an empty body or time out under concurrent load.
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.post(API_URL, headers=headers, json=payload, timeout=120)
            resp.raise_for_status()
            _note_backend(resp.headers.get("X-Proxy-Backend", ""))
            text = resp.json()["choices"][0]["message"]["content"].strip()
            if not text:
                raise ValueError("empty LLM response")
            if raw:
                return text
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            return json.loads(text)
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise last_err


def _verdict_from_text(text: str) -> dict:
    """Parse a validator reply into {valid, note}, accepting BOTH JSON and prose. qwen returns JSON;
    Opus writes a prose verdict (often a markdown essay) despite the JSON ask. Order: clean/embedded
    JSON → an explicit `VERDICT: CONFIRM|FLAG` tag → score the lead's substance. The bare word "flag"
    is NOT decisive (Opus says "endorse, with one flag"); substantive stance words are. Never returns
    None for a real reply — that silent drop is exactly what hid the validator before."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        t = t[4:] if t.lower().startswith("json") else t
        t = t.strip()
    # 1. JSON — the whole reply, or an object embedded in prose
    for cand in [t] + re.findall(r'\{[^{}]*?"valid"[^{}]*?\}', text, re.S):
        try:
            o = json.loads(cand)
            if "valid" in o:
                return {"valid": bool(o["valid"]), "note": str(o.get("note", "")).strip()[:160]}
        except Exception:
            pass
    # note = the model's own headline: the first **bold** phrase, else the first sentence (de-marked-up)
    bold = re.search(r"\*\*(.+?)\*\*", text, re.S)
    lead = bold.group(1) if bold else re.split(r"(?<=[.!?])\s", re.sub(r"^#+[^\n]*\n+", "", t), 1)[0]
    note = re.sub(r"\s+", " ", re.sub(r"[#*`>]+", "", lead)).strip(" :.—-")[:160]
    # 2. explicit verdict tag, if the model gave one
    m = re.search(r"VERDICT\s*[:=]\s*(CONFIRM|FLAG)", text, re.I)
    if m:
        return {"valid": m.group(1).upper() == "CONFIRM", "note": note}
    # 3. score the stance over the lead — substance beats the bare token "flag"/"confirm"
    head = text[:400].lower()
    pos = sum(w in head for w in ("endorse", "agree", "confirm", "sound", "defensible",
                                  "let it stand", "uphold", "correct call", "valid call", "stands"))
    neg = sum(w in head for w in ("overstated", "premature", "misread", "reject", "disagree", "hold all",
                                  "do not", "don't", "too aggressive", "false positive", "not distribution",
                                  "base forming", "wrong", "questionable"))
    return {"valid": pos >= neg, "note": note}


def validate(ticker: str, df: pd.DataFrame, name: str, verdict: dict,
             market_ctx: dict | None = None, catalyst: dict | None = None) -> dict:
    """Validator role: the LLM stress-tests the engine's decision (it does NOT decide or narrate).
    `verdict` = {action, score, signals, stop}; `catalyst` = {earnings_soon: bool, headlines: [str]}
    (real Finnhub context). Returns {valid: bool|None, note}; valid=None = LLM unavailable."""
    label = f"{ticker} ({name})" if name and name != ticker else ticker
    sig = ", ".join(verdict.get("signals") or []) or "none"
    qty, last = verdict.get("qty"), verdict.get("price")
    pos = f" Position: {qty} shares" + (f", last price {last}" if last is not None else "") + "."
    user_parts = [
        f"Ticker: {label} — CURRENTLY HELD.{pos}",
        f"Mechanical decision to validate: {verdict['action']} — any number in the action is a SHARE COUNT, not a price.",
        f"Deterioration score: {verdict['score']}/9. Signals: {sig}. Trailing stop (a price level): {verdict['stop']}.",
    ]
    if market_ctx:
        user_parts.append("\n" + _market_context_block(market_ctx))
    if catalyst:
        ctx = []
        if catalyst.get("earnings_soon"):
            ctx.append("Earnings within ~14 days — price/volume is noisy around earnings.")
        heads = catalyst.get("headlines") or []
        if heads:
            ctx.append("Recent headlines (use to spot an ex-dividend, split, M&A, or catalyst the price can't show):")
            ctx += [f"- {h}" for h in heads[:5]]
        if ctx:
            user_parts.append("\n" + "\n".join(ctx))
    user_parts.append(f"\nOHLCV (last {len(df)} trading days):\n{df.to_csv()}")
    try:
        text = _call_llm(_SYSTEM_VALIDATE, user_parts, raw=True)
    except Exception:
        return {"valid": None, "note": ""}        # genuine LLM failure (timeout/HTTP) → unavailable
    return _verdict_from_text(text)
