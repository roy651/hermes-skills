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


def analyze(ticker: str, df: pd.DataFrame, held: bool = False, name: str = "") -> dict:
    context = "Currently HELD in portfolio." if held else "On watchlist (not held)."
    label = f"{ticker} ({name})" if name and name != ticker else ticker
    csv = df.to_csv()
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"Ticker: {label}\n{context}\n\nOHLCV (last {len(df)} trading days):\n{csv}"},
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
