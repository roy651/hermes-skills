"""
Report generator for finance-assistant.

Usage:
  python report.py --type weekly|monthly   # print report text
  python report.py --send --type weekly    # send to all allowed chat IDs
  python report.py --check-schedule       # send only if schedule.json says it's time
"""
import argparse
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests as http

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

SKILL_DIR = Path(__file__).parent.parent
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
ALLOWED_CHAT_IDS = [
    int(x.strip()) for x in os.environ.get("ALLOWED_CHAT_IDS", "").split(",") if x.strip()
]
SCHEDULE_FILE = SKILL_DIR / "schedule.json"

sys.path.insert(0, str(Path(__file__).parent))
import actual_client as ac


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _ils(amount: float) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}₪{abs(amount):,.0f}"


def _bar(used: float, budget: float, width: int = 10) -> str:
    if budget <= 0:
        return "░" * width
    pct = min(used / budget, 1.0)
    filled = round(pct * width)
    return "█" * filled + "░" * (width - filled)


# ---------------------------------------------------------------------------
# Report builders
# ---------------------------------------------------------------------------

def build_monthly_report() -> str:
    today = date.today()
    month_name = today.strftime("%B %Y")

    try:
        balances = ac.get_balances()
        budget_rows = ac.get_monthly_budget()
        summary = ac.get_income_vs_expense()
        anomalies = ac.get_anomalies()
    except Exception as e:
        return f"❌ שגיאה בשליפת נתונים: {e}"

    lines = [f"📊 *דוח חודשי — {month_name}*\n"]

    lines.append("*💰 יתרות חשבונות*")
    for acc in balances:
        prefix = "💳" if acc["type"] == "credit" else "🏦"
        lines.append(f"{prefix} {acc['name']}: {_ils(acc['balance'])}")

    lines.append(f"\n*📈 הכנסות vs. הוצאות*")
    lines.append(f"הכנסות:  {_ils(summary['income'])}")
    lines.append(f"הוצאות:  {_ils(summary['expenses'])}")
    lines.append(f"נטו:     {_ils(summary['net'])}")

    if budget_rows:
        lines.append("\n*📋 תקציב לפי קטגוריה*")
        current_group = None
        for row in budget_rows:
            if row["group"] != current_group:
                current_group = row["group"]
                lines.append(f"\n_{current_group}_")
            bar = _bar(abs(row["actual"]), row["budgeted"])
            remaining_str = f"נותר {_ils(row['remaining'])}" if row["budgeted"] > 0 else "אין תקציב"
            lines.append(f"  {row['category']}: {bar} {_ils(abs(row['actual']))} / {_ils(row['budgeted'])} ({remaining_str})")

    if anomalies:
        lines.append(f"\n⚠️ *הוצאות חריגות החודש*")
        for a in anomalies[:5]:
            lines.append(f"  • {a['payee']} — {_ils(abs(a['amount']))} (ממוצע: {_ils(a['avg_for_category'])})")

    return "\n".join(lines)


def build_weekly_report() -> str:
    today = date.today()
    week_start = today - timedelta(days=today.weekday())  # Monday
    week_end = week_start + timedelta(days=6)

    try:
        txns = ac.get_transactions(since=week_start, until=week_end, limit=100)
        anomalies = ac.get_anomalies()
    except Exception as e:
        return f"❌ שגיאה בשליפת נתונים: {e}"

    lines = [f"📅 *דוח שבועי — {week_start.strftime('%d/%m')}–{week_end.strftime('%d/%m/%Y')}*\n"]

    total_expense = sum(abs(t["amount"]) for t in txns if t["amount"] < 0)
    total_income = sum(t["amount"] for t in txns if t["amount"] > 0)
    lines.append(f"הכנסות: {_ils(total_income)}  |  הוצאות: {_ils(total_expense)}\n")

    # Top expenses by category
    by_cat: dict[str, float] = {}
    for t in txns:
        if t["amount"] < 0 and t["category"]:
            by_cat[t["category"]] = by_cat.get(t["category"], 0) + abs(t["amount"])
    if by_cat:
        lines.append("*הוצאות לפי קטגוריה:*")
        for cat, total in sorted(by_cat.items(), key=lambda x: -x[1])[:8]:
            lines.append(f"  {cat}: {_ils(total)}")

    week_anomalies = [a for a in anomalies if a["date"] >= week_start.isoformat()]
    if week_anomalies:
        lines.append(f"\n⚠️ *הוצאות חריגות השבוע:*")
        for a in week_anomalies[:3]:
            lines.append(f"  • {a['payee']} — {_ils(abs(a['amount']))}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sending + scheduling
# ---------------------------------------------------------------------------

def send_to_all(text: str) -> None:
    for chat_id in ALLOWED_CHAT_IDS:
        try:
            http.post(
                f"{API_BASE}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=15,
            )
        except Exception as e:
            print(f"[report] send failed for {chat_id}: {e}", file=sys.stderr)


def _load_schedule() -> dict:
    if SCHEDULE_FILE.exists():
        return json.loads(SCHEDULE_FILE.read_text())
    return {"weekly": {"enabled": False}, "monthly": {"enabled": False}}


def check_and_send_schedule() -> None:
    now = time.localtime()
    current_day_name = time.strftime("%A").lower()
    current_day_num = now.tm_mday
    current_time = f"{now.tm_hour:02d}:{now.tm_min:02d}"

    sched = _load_schedule()

    w = sched.get("weekly", {})
    if w.get("enabled") and w.get("day", "").lower() == current_day_name and w.get("time", "") == current_time:
        send_to_all(build_weekly_report())

    m = sched.get("monthly", {})
    if m.get("enabled") and m.get("day") == current_day_num and m.get("time", "") == current_time:
        send_to_all(build_monthly_report())


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=["weekly", "monthly"], default="monthly")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--check-schedule", action="store_true")
    args = parser.parse_args()

    if args.check_schedule:
        check_and_send_schedule()
        sys.exit(0)

    text = build_weekly_report() if args.type == "weekly" else build_monthly_report()

    if args.send:
        send_to_all(text)
    else:
        print(text)
