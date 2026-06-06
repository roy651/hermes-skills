from __future__ import annotations
import json
import os
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
) -> dict:
    context = "Currently HELD in portfolio." if held else "On watchlist (not held)."
    label = f"{ticker} ({name})" if name and name != ticker else ticker
    system = _SYSTEM_EXIT if mode == "exit" else _SYSTEM
    csv = df.to_csv()
    user_parts = [f"Ticker: {label}", context]
    if market_ctx:
        user_parts.append("\n" + _market_context_block(market_ctx))
    user_parts.append(f"\nOHLCV (last {len(df)} trading days):\n{csv}")
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(user_parts)},
    ]
    model = os.environ.get("WYCKOFF_LLM_MODEL", "claude-opus-4-6")
    resp = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {os.environ.get('LLM_API_KEY', 'local')}",
            "Content-Type": "application/json",
        },
        json={"model": model, "messages": messages, "temperature": 0, "max_tokens": 512},
        timeout=90,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    result = json.loads(text)
    result["ticker"] = ticker
    return result
