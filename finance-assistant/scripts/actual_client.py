"""
Thin wrapper around actualpy for querying ActualBudget.

All amounts returned as float ILS (ActualBudget stores in milliunits: divide by 1000).
Balances: positive = money owed to you / in account; negative = you owe (credit card balance).
"""
import os
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

SERVER_URL = os.environ.get("ACTUAL_SERVER_URL", "http://localhost:5006")
PASSWORD = os.environ.get("ACTUAL_PASSWORD", "")
BUDGET_NAME = os.environ.get("ACTUAL_BUDGET_NAME", "")

_MILLI = 1000  # ActualBudget milliunits → ILS


def _amt(milliunits: int | None) -> float:
    return (milliunits or 0) / _MILLI


@contextmanager
def _actual():
    from actual import Actual
    with Actual(base_url=SERVER_URL, password=PASSWORD, file=BUDGET_NAME) as a:
        yield a


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_balances() -> list[dict]:
    """Return [{name, type, balance_ils}] for all open accounts, sorted by name."""
    from actual.queries import get_accounts
    with _actual() as a:
        accounts = get_accounts(a.session)
        return sorted(
            [
                {
                    "name": acc.name,
                    "type": acc.type,
                    "balance": _amt(acc.balance),
                }
                for acc in accounts
                if not acc.closed
            ],
            key=lambda x: x["name"],
        )


def get_monthly_budget(month: date | None = None) -> list[dict]:
    """
    Return [{category, group, budgeted, actual, remaining}] for the given month.
    Defaults to the current month.
    """
    from actual.queries import get_budgets, get_categories
    if month is None:
        today = date.today()
        month = date(today.year, today.month, 1)
    with _actual() as a:
        budgets = get_budgets(a.session, month=month)
        categories = {c.id: c for c in get_categories(a.session)}
        rows = []
        for b in budgets:
            cat = categories.get(b.category_id)
            if cat is None or cat.is_income:
                continue
            rows.append(
                {
                    "category": cat.name,
                    "group": cat.group.name if cat.group else "",
                    "budgeted": _amt(b.budgeted),
                    "actual": _amt(b.activity),
                    "remaining": _amt((b.budgeted or 0) - abs(b.activity or 0)),
                }
            )
        return sorted(rows, key=lambda x: (x["group"], x["category"]))


def get_transactions(
    account_name: str | None = None,
    since: date | None = None,
    until: date | None = None,
    limit: int = 200,
) -> list[dict]:
    """Return recent transactions as dicts. Amounts in ILS (negative = expense)."""
    from actual.queries import get_transactions as _get_tx
    if since is None:
        since = date.today() - timedelta(days=30)
    with _actual() as a:
        txns = _get_tx(a.session, since_date=since)
        rows = []
        for t in txns:
            if t.is_child or t.is_parent:
                continue
            if account_name and (t.account is None or t.account.name != account_name):
                continue
            if until and t.date > until:
                continue
            rows.append(
                {
                    "date": t.date.isoformat() if t.date else "",
                    "account": t.account.name if t.account else "",
                    "payee": t.payee.name if t.payee else "",
                    "category": t.category.name if t.category else "",
                    "amount": _amt(t.amount),
                    "notes": t.notes or "",
                }
            )
        rows.sort(key=lambda x: x["date"], reverse=True)
        return rows[:limit]


def get_anomalies(threshold_multiplier: float = 2.0) -> list[dict]:
    """
    Return transactions in the current month whose amount (abs) is more than
    threshold_multiplier × the 3-month category average. Excludes transfers.
    """
    today = date.today()
    month_start = date(today.year, today.month, 1)
    three_months_ago = date(today.year, today.month, 1) - timedelta(days=90)

    recent = get_transactions(since=three_months_ago)
    current = [t for t in recent if t["date"] >= month_start.isoformat() and t["category"]]

    # Build category averages over the 3-month window (excluding current month)
    history = [t for t in recent if t["date"] < month_start.isoformat() and t["category"]]
    cat_totals: dict[str, list[float]] = {}
    for t in history:
        cat_totals.setdefault(t["category"], []).append(abs(t["amount"]))
    cat_avg = {cat: sum(vals) / max(len(vals), 1) for cat, vals in cat_totals.items()}

    anomalies = []
    for t in current:
        avg = cat_avg.get(t["category"], 0)
        if avg > 0 and abs(t["amount"]) > avg * threshold_multiplier:
            anomalies.append({**t, "avg_for_category": avg})
    anomalies.sort(key=lambda x: abs(x["amount"]), reverse=True)
    return anomalies


def get_income_vs_expense(month: date | None = None) -> dict[str, float]:
    """Return {income, expenses, net} for the given month (default: current)."""
    if month is None:
        today = date.today()
        month = date(today.year, today.month, 1)
    next_month = date(month.year + (month.month == 12), (month.month % 12) + 1, 1)
    txns = get_transactions(since=month, until=next_month - timedelta(days=1))
    income = sum(t["amount"] for t in txns if t["amount"] > 0)
    expenses = sum(abs(t["amount"]) for t in txns if t["amount"] < 0)
    return {"income": income, "expenses": expenses, "net": income - expenses}
