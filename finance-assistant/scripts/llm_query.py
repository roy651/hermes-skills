"""LLM-powered /ask handler — packages financial context and calls OpenRouter."""
import json
import os
from datetime import date
from pathlib import Path

import requests as http

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

LLM_URL = os.environ.get("LLM_API_URL", "https://openrouter.ai/api/v1/chat/completions")
LLM_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "google/gemini-flash-1.5")

_SYSTEM = """You are a personal finance assistant for an Israeli family.
You have access to their ActualBudget data provided as JSON context.
Answer questions concisely in the same language the user writes in (Hebrew or English).
Format amounts as ₪X,XXX. Be direct and helpful. Never reveal raw JSON to the user."""


def answer(question: str) -> str:
    import actual_client as ac

    today = date.today()
    try:
        balances = ac.get_balances()
        budget = ac.get_monthly_budget()
        summary = ac.get_income_vs_expense()
        anomalies = ac.get_anomalies()
        recent_txns = ac.get_transactions(limit=50)
    except Exception as e:
        return f"שגיאה בשליפת נתונים מ-ActualBudget: {e}"

    context = {
        "today": today.isoformat(),
        "account_balances": balances,
        "this_month_summary": summary,
        "budget_vs_actual": budget,
        "anomalies_this_month": anomalies[:10],
        "recent_transactions": recent_txns[:30],
    }

    messages = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": f"Financial data:\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\nQuestion: {question}",
        },
    ]

    try:
        resp = http.post(
            LLM_URL,
            headers={"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"},
            json={"model": LLM_MODEL, "messages": messages, "max_tokens": 800},
            timeout=60,
        )
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"שגיאה בקריאה ל-LLM: {e}"
