#!/usr/bin/env python3
"""One report instead of eleven jobs.

The portfolio produced four Telegram messages on a normal weekday and three more across the
weekend — roughly twenty-three a week, most of them arriving within minutes of each other near
midnight. This assembles them into two: a DAILY brief that is purely defensive, and a WEEKLY
review that carries every judgement call.

The organising rule is that **cadence should match evidence**. Risk is the only thing that
genuinely needs daily attention; everything else is a decision, and decisions are weekly at most.

Two design choices worth knowing before editing:

1. **Delivery is consolidated, logic is not.** Each section is produced by the module that
   already owned it, called with ``as_section=True`` so it returns text instead of sending.
   A section that raises is reported in place and the rest of the report still goes out.
   One giant prompt would be slower, more fragile, and would lose everything at once.

2. **The LLM may add and caution; it may NEVER delete.** Mechanical sections are printed
   unchanged, always. If the read thinks a breach is noise it says so underneath — the breach
   still appears. Our own validator has been wrong before (it argued to hold FFIV two days
   before it broke its stop twice), so an advisory layer that can silently suppress a real
   signal would be worse than no layer at all.

Usage:  digest.py --daily | --weekly  [--dry-run] [--no-llm]
"""
from __future__ import annotations

import re
import sys
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv(Path.home() / ".hermes" / ".env")

import notifier

TZ = ZoneInfo("Asia/Jerusalem")

# What the research has already killed. The read runs with no memory of the programme, and the
# Wyckoff narrative is seductive enough that without this it will confidently argue from
# detectors we falsified. Numbers are from docs/signal-validation.md.
FALSIFIED = """\
EVIDENCE YOU MUST RESPECT (from our own testing, not opinion):
- The Wyckoff ACCUMULATION entry gate measured NEGATIVE (-0.36% vs +0.02% baseline) and it
  DEGRADED momentum when combined (+2.91% -> +1.10%). Never argue for a buy from it.
- markup_pullback scored t=0.57 - statistically indistinguishable from noise. It once drove a
  real losing trade. Never treat it as confirmation.
- The deterioration score is a DISTRESS indicator for already-damaged positions, not a harvest
  signal. In the deepest-drawdown quintile it is strong (t=-4.12); for a position NEAR ITS HIGH
  it has NO predictive power (t=0.30). Never use it to argue for selling a winner.
- The trailing stop is validated as TAIL CONTROL only: it roughly halves the 5th-percentile
  outcome and makes the median worse. It is not a profit-taking tool.
- We have NO validated signal for when to take profit on a winner. If that is the question,
  the honest answer is that we do not know.
- mom_12_1 is era-dependent and much weaker than first measured (t fell 4.43 -> 1.98 on a
  tripled sample). It is not a green light on its own.
"""

SYSTEM = """You are reviewing an automated portfolio report for its owner, a private investor.

Your job is INTERPRETATION and QUALITY CONTROL, not signal generation:
- Point out what actually matters in the numbers above, in 2-5 short bullets.
- Flag any mechanical signal you believe is FALSE or misleading, and say why.
- Flag anything that looks like a data error (an impossible price, a stop above the price for
  no reason, a stale figure).
- If nothing needs attention, say so plainly in one line. Do not manufacture observations.

Hard rules:
- You cannot cancel a signal. The mechanical sections stand as printed; you may only comment.
- Never recommend a buy from a falsified detector (see the evidence block).
- "No view" and "I don't know" are correct and expected answers. Do not fill space.
- Be specific and short. No preamble, no restating the report back.
- Plain text with simple HTML tags (<b>, <i>) only. No markdown, no headers."""


def _md_to_telegram(t: str) -> str:
    """Convert the markdown the model emits anyway into Telegram-safe HTML.

    The system prompt asks for plain text; models still reach for **bold**. Telegram renders in
    HTML parse mode, so asterisks would show literally and a stray '<' would break the send.
    Converting is more reliable than asking again.
    """
    t = re.sub(r"^#{1,6}\s*", "", t, flags=re.M)          # stray headers
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t, flags=re.S)
    t = re.sub(r"(?<![*\w])\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", t)
    t = re.sub(r"^\s*[-*]\s+", "• ", t, flags=re.M)        # bullets
    return t.strip()


def _section(title: str, fn, *args, **kwargs) -> str | None:
    """Run one section; a failure is reported in place rather than killing the report."""
    try:
        out = fn(*args, **kwargs)
        return out.strip() if out and out.strip() else None
    except Exception as e:
        print(f"[digest] section {title!r} failed: {e}", file=sys.stderr)
        traceback.print_exc()
        return f"⚠️ <b>{title}</b> — section failed: <i>{str(e)[:120]}</i>"


def daily_sections() -> list[str]:
    import stop_check, portfolio_value, watchlist_scan, checkpoints
    out = []
    for title, fn, kw in [("Risk", stop_check.run, {"as_section": True}),
                          ("Value", portfolio_value.run, {"as_section": True}),
                          ("Watchlist", watchlist_scan.run, {"as_section": True})]:
        s = _section(title, fn, **kw)
        if s:
            out.append(s)
    # Only imminent checkpoints belong in a daily brief, and only the ones that ask something
    # of a person. A recurring monthly report is a routine; a one-shot is a decision someone
    # deliberately parked on a date. Telling you to "decide" about a disk-space audit is noise.
    rows = [r for r in checkpoints.pending(datetime.now(tz=ZoneInfo("UTC")),
                                           checkpoints.IMMINENT_DAYS) if r.get("one_shot")]
    if rows:
        out.append(checkpoints.build(rows, checkpoints.IMMINENT_DAYS))
    return out


REPORTS = Path(__file__).parent.parent / "data" / "reports"


def _latest(fragment: str, max_age_days: int = 8) -> str | None:
    """Most recent archived digest whose filename contains `fragment`.

    exit.py and entry.py are slow LLM jobs with their own watchdogs, and notifier.send() already
    archives everything it sends. Reading the archive is far safer than re-running them inside
    this report: nothing can hang the weekly, and their state handling is untouched.
    """
    if not REPORTS.exists():
        return None
    cands = sorted((f for f in REPORTS.glob(f"*{fragment}*.txt")),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    if not cands:
        return None
    newest = cands[0]
    age = (datetime.now().timestamp() - newest.stat().st_mtime) / 86400
    if age > max_age_days:
        return f"⚠️ <i>Latest {fragment} report is {age:.0f} days old — the job may have stopped.</i>"
    return newest.read_text().strip()


def engine_health() -> str:
    """What ran, what didn't. A silent failure is the failure mode that actually costs money."""
    expected = [("exit-all", "the weekly exit review"), ("entry", "the entry funnel"),
                ("mlm-scan", "the momentum scan"), ("stop-check", "the daily stop check")]
    lines = ["🩺 <b>Engine health</b>"]
    for frag, desc in expected:
        got = _latest(frag, max_age_days=9)
        if got is None:
            lines.append(f"• ❌ no {desc} found in the archive at all")
        elif got.startswith("⚠️"):
            lines.append(f"• ⚠️ {desc}: {got}")
        else:
            lines.append(f"• ✅ {desc} ran")
    return "\n".join(lines)


def weekly_sections() -> list[str]:
    import checkpoints, watchlist_scan
    out = []
    for frag, title in [("exit-all", "Positions"), ("entry", "Entry funnel")]:
        got = _latest(frag)
        if got:
            out.append(got)
        else:
            out.append(f"⚠️ <b>{title}</b> — no recent report in the archive.")
    s = _section("Watchlist", watchlist_scan.run, as_section=True)
    if s:
        out.append(s)
    rows = checkpoints.pending(datetime.now(tz=ZoneInfo("UTC")), checkpoints.HORIZON_DAYS)
    out.append(checkpoints.build(rows, checkpoints.HORIZON_DAYS))
    out.append(engine_health())
    return out


def llm_read(body: str) -> str:
    """The interpretation block. Degrades to a visible notice — never to a failed report."""
    try:
        import analysis
        txt = analysis._call_llm(SYSTEM, [FALSIFIED, "\nTODAY'S REPORT:\n", body], raw=True)
        txt = _md_to_telegram(txt or "")
        if not txt:
            raise ValueError("empty response")
        return f"\n\n———\n🧠 <b>Read</b> <i>(interpretation — the numbers above stand as printed)</i>\n{txt}"
    except Exception as e:
        print(f"[digest] llm read failed: {e}", file=sys.stderr)
        return "\n\n———\n⚠️ <i>Read unavailable — mechanical sections above are unaffected.</i>"


def run(kind: str, dry_run: bool = False, use_llm: bool = True) -> None:
    now = datetime.now(tz=TZ)
    header = (f"📋 <b>Daily Brief</b> — {now:%a %d %b}" if kind == "daily"
              else f"📖 <b>Weekly Review</b> — {now:%d %b %Y}")
    sections = daily_sections() if kind == "daily" else weekly_sections()
    if not sections:
        sections = ["<i>Nothing to report.</i>"]

    body = "\n\n".join(sections)
    msg = f"{header}\n\n{body}"
    if use_llm:
        msg += llm_read(body)

    print(msg) if dry_run else notifier.send(msg)
    print(f"[digest] {kind}: {len(sections)} section(s), {len(msg)} chars", file=sys.stderr)


if __name__ == "__main__":
    kind = "weekly" if "--weekly" in sys.argv else "daily"
    run(kind, dry_run="--dry-run" in sys.argv, use_llm="--no-llm" not in sys.argv)
