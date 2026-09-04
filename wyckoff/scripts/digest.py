#!/usr/bin/env python3
"""One report instead of eleven jobs.

The portfolio produced four Telegram messages on a normal weekday and three more across the
weekend — roughly twenty-three a week, most of them arriving within minutes of each other near
midnight. This assembles them into two: a DAILY brief and a WEEKLY review that carries every
judgement call.

The organising rule is that **cadence should match evidence**. Risk needs daily attention, and
so does what changed: the day's market tape, news and events on the held names, and what the
silent engines produced. Everything that did NOT change (the concentration table, a flag from
three months ago) collapses to one line, because a report that looks the same every day stops
being read. Decisions stay weekly.

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

import json
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
CONFIG = Path(__file__).parent.parent / "config.yaml"
BRIEF_STATE = Path(__file__).parent.parent / "data" / "brief_state.json"
FRESH_FLAG_DAYS = 7          # a validated flag prints in full for a week, then as one carried line

# Default "views" sources; override with `brief.analysts` in config.yaml (names are search terms).
DEFAULT_ANALYSTS = ["Torsten Sløk (Apollo)", "Liz Ann Sonders (Schwab)", "Mike Wilson (Morgan Stanley)",
                    "Jurrien Timmer (Fidelity)", "Howard Marks (Oaktree memos)",
                    "Ben Carlson (A Wealth of Common Sense)", "Lyn Alden"]

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

SYSTEM_DAILY = """You are writing the end-of-day brief for a private investor's portfolio. You have web
search — use it (at most {max_searches} searches). The mechanical report is below; your job is to
add what it cannot: the tape, the news, and the events. Keep the whole reply under 1,800 characters.

Write exactly these parts, in this order, plain text with <b>/<i> tags only (no markdown, no headers):

<b>Market</b> — 2-3 bullets. Read the snapshot (indexes, rates, credit, dollar, gold, vol, sector
ranks): what moved today, what regime this looks like, and what it means for THIS book's tilts
(weights are in the concentration section). One search for today's market wrap is enough.

<b>Your book</b> — 2-4 bullets, the useful part. Search for: earnings or ex-dividend dates in the
next 14 days on held names; news since yesterday on the held names (heaviest weights first) and
on the sectors the book leans on; and ONE piece published this week by any of these sources, if
it says something a holder of this book should hear: {analysts}. Each bullet: the fact, a link,
and one clause on why it matters here. Skip anything routine. "Nothing material today" is a
fine section.

<b>Check</b> — 0-2 bullets, ONLY if a number looks wrong or a mechanical flag is misleading.
Omit the part entirely when there is nothing to say.

Hard rules:
- You cannot cancel a signal. The mechanical sections stand as printed; you may only comment.
- Never recommend a buy from a falsified detector (see the evidence block). A news item is not
  a buy case either; say "no view" when that is the truth.
- Do not restate the report's numbers back. Do not manufacture observations or fill space.
- Links as plain URLs or <a href="...">text</a>. No markdown."""


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


def daily_sections(commit_state: bool = True) -> list[str]:
    import stop_check, portfolio_value, watchlist_scan, checkpoints, concentration, signals, market
    import holdings as portfolio
    out = []
    for title, fn, kw in [("Risk", stop_check.run, {"as_section": True}),
                          ("Value", portfolio_value.run, {"as_section": True}),
                          ("Market", market.build_section, {}),
                          ("Engines", engine_lines, {})]:
        s = _section(title, fn, **kw)
        if s:
            out.append(s)
    c = _section("Concentration", concentration.build_section)
    if c:
        out.append(_collapse_unchanged_concentration(c, commit_state))
    s = _section("Watchlist", watchlist_scan.run, as_section=True)
    if s:
        out.append(s)
    # Only imminent checkpoints belong in a daily brief, and only the ones that ask something
    # of a person. A recurring monthly report is a routine; a one-shot is a decision someone
    # deliberately parked on a date. Telling you to "decide" about a disk-space audit is noise.
    held = list(portfolio.load().keys())
    flags = _section("Signals", signals.build_section, held, fresh_days=FRESH_FLAG_DAYS)
    if flags:
        out.append(flags)
    rows = [r for r in checkpoints.pending(datetime.now(tz=ZoneInfo("UTC")),
                                           checkpoints.IMMINENT_DAYS) if r.get("one_shot")]
    if rows:
        out.append(checkpoints.build(rows, checkpoints.IMMINENT_DAYS))
    return out


REPORTS = Path(__file__).parent.parent / "data" / "reports"


def _latest_path(fragment: str) -> Path | None:
    if not REPORTS.exists():
        return None
    cands = sorted((f for f in REPORTS.glob(f"*{fragment}*.txt")),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def _latest(fragment: str, max_age_days: int = 8) -> str | None:
    """Most recent archived digest whose filename contains `fragment`.

    exit.py and entry.py are slow LLM jobs with their own watchdogs, and notifier.send() already
    archives everything it sends. Reading the archive is far safer than re-running them inside
    this report: nothing can hang the weekly, and their state handling is untouched.
    """
    newest = _latest_path(fragment)
    if newest is None:
        return None
    age = (datetime.now().timestamp() - newest.stat().st_mtime) / 86400
    if age > max_age_days:
        return f"⚠️ <i>Latest {fragment} report is {age:.0f} days old — the job may have stopped.</i>"
    return newest.read_text().strip()


# Engine reports are built from '— Label (n) —' blocks holding '🔴 <b>TICKER</b>' lines.
ENGINE_GROUP = re.compile(r"<b>— ([A-Z][A-Za-z\- ]+?)(?: ·[^(]*)?\((\d+)\) —</b>")
ENGINE_TICKER = re.compile(r"^[🔴🟡🟢🟣⚪] <b>([A-Z0-9.\-]+)</b>", re.M)
MLM_ROW = re.compile(r"^\s*(\d+)([*! ])\s+(\S+)\s", re.M)


def _groups(text: str) -> list[tuple[str, int, list[str]]]:
    """[(label, count, tickers)] per '— Label (n) —' block of an exit or entry report."""
    parts = ENGINE_GROUP.split(text)      # preamble, label, count, body, label, count, body ...
    out = []
    for i in range(1, len(parts) - 2, 3):
        out.append((parts[i].strip(), int(parts[i + 1]), ENGINE_TICKER.findall(parts[i + 2])))
    return out


def _report_date(path: Path) -> str:
    return datetime.strptime(path.name[:8], "%Y%m%d").strftime("%a %d %b")


def _engine_line(fragment: str, title: str, keep: tuple[str, ...], max_age_days: int = 9) -> str:
    path = _latest_path(fragment)
    if path is None:
        return f"• {title}: ❌ nothing in the archive"
    age = (datetime.now().timestamp() - path.stat().st_mtime) / 86400
    if age > max_age_days:
        return f"• {title} ({_report_date(path)}): ⚠️ {age:.0f} days old — the job may have stopped"
    groups = [(l, n, t) for l, n, t in _groups(path.read_text()) if l.upper().startswith(keep)]
    if not groups:
        return f"• {title} ({_report_date(path)}): no actions"
    bits = [f"{n} {l.lower()} — {', '.join(t[:4])}{'…' if len(t) > 4 else ''}" for l, n, t in groups]
    return f"• {title} ({_report_date(path)}): " + " · ".join(bits)


def _mlm_line() -> str:
    path = _latest_path("mlm-scan")
    if path is None:
        return "• MLM scan: ❌ nothing in the archive"
    age = (datetime.now().timestamp() - path.stat().st_mtime) / 86400
    if age > 4:
        return f"• MLM scan ({_report_date(path)}): ⚠️ {age:.0f} days old — the job may have stopped"
    text = path.read_text()
    cleared = re.search(r"(\d+) names cleared", text)
    rows = MLM_ROW.findall(text)
    top = ", ".join(t for _, _, t in rows[:3]) or "—"
    held = ", ".join(t for _, mark, t in rows if mark == "*") or "—"
    return (f"• MLM scan ({_report_date(path)}): {cleared.group(1) if cleared else '?'} cleared · "
            f"top {top} · held names in the list: {held} <i>(context, not a queue)</i>")


def engine_lines() -> str:
    """One line per engine, read from the archive. The engines post nothing themselves
    (WYCKOFF_SILENT), so until now their output surfaced only in the weekly."""
    return "\n".join(["⚙️ <b>Engines</b>",
                       _engine_line("exit-all", "Exit review", ("EXIT", "TRIM", "ADD")),
                       _engine_line("wyckoff-entry-", "Entry funnel", ("STRONG", "MARKUP")),
                       _mlm_line()])


CONC_ROW = re.compile(r"^(\S+)\s+([\d.]+)%", re.M)


def _load_state() -> dict:
    try:
        return json.loads(BRIEF_STATE.read_text())
    except Exception:
        return {}


def _collapse_unchanged_concentration(section: str, commit_state: bool) -> str:
    """Print the table when it moved (any weight by a full point, or the line-up) and on Mondays;
    otherwise one line. The bars were identical for weeks, which taught the eye to skip them.
    Any failure here returns the full table: a cosmetic step must never cost the report."""
    try:
        return _collapsed_or_full(section, commit_state, datetime.now(tz=TZ))
    except Exception as e:
        print(f"[digest] concentration collapse failed, printing the table: {e}", file=sys.stderr)
        return section


def _collapsed_or_full(section: str, commit_state: bool, now_dt: datetime) -> str:
    rows = CONC_ROW.findall(section)
    if not rows:
        return section
    now = {name: float(pct) for name, pct in rows}
    state = _load_state()
    prev = state.get("concentration") or {}
    moved = set(now) != set(prev) or any(abs(now[n] - prev[n]) >= 1.0 for n in now)
    if commit_state:
        state["concentration"] = now
        BRIEF_STATE.parent.mkdir(parents=True, exist_ok=True)
        BRIEF_STATE.write_text(json.dumps(state, indent=1))
    if moved or now_dt.weekday() == 0:
        return section
    top = " · ".join(f"{name} {now[name]:.0f}%" for name, _ in rows[:3])
    tail = re.search(r"effective <b>[\d.]+</b>.*?$", section, re.M)
    summary = f"; {re.sub('<[^>]+>', '', tail.group(0))}" if tail else ""
    return (f"⚖️ <b>Concentration</b> — unchanged: {top}{summary} "
            f"<i>(full table when it moves, and on Mondays)</i>")


def _brief_cfg() -> dict:
    try:
        import yaml
        return (yaml.safe_load(CONFIG.read_text()) or {}).get("brief") or {}
    except Exception:
        return {}


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
    import checkpoints, watchlist_scan, concentration, signals
    import holdings as portfolio
    out = []
    c = _section("Concentration", concentration.build_section)
    if c:
        out.append(c)
    for frag, title in [("exit-all", "Positions"), ("entry", "Entry funnel")]:
        got = _latest(frag)
        if got:
            out.append(got)
        else:
            out.append(f"⚠️ <b>{title}</b> — no recent report in the archive.")
    # MLM is CONTEXT, not an instruction. The portfolio test showed it does not beat SPY once
    # the moonshot tail and 2020 come out, so it is demoted from a daily entry queue to a
    # weekly momentum backdrop, and labelled as such.
    held = list(portfolio.load().keys())
    flags = _section("Signals", signals.build_section, held)
    if flags:
        out.append(flags)
    res = _section("Entry residue", signals.entry_residue, 12, held)
    if res:
        out.append(res)
    mlm = _latest("mlm-scan")
    if mlm:
        out.append("<i>— momentum backdrop (context only; not an entry queue) —</i>\n" + mlm)
    s = _section("Watchlist", watchlist_scan.run, as_section=True)
    if s:
        out.append(s)
    rows = checkpoints.pending(datetime.now(tz=ZoneInfo("UTC")), checkpoints.HORIZON_DAYS)
    out.append(checkpoints.build(rows, checkpoints.HORIZON_DAYS))
    out.append(engine_health())
    return out


def llm_read(body: str, system: str = SYSTEM) -> str:
    """The interpretation block. Degrades to a visible notice — never to a failed report."""
    try:
        import analysis
        txt = analysis._call_llm(system, [FALSIFIED, "\nTODAY'S REPORT:\n", body], raw=True)
        txt = _md_to_telegram(txt or "")
        if not txt:
            raise ValueError("empty response")
        return (f"\n\n🧠 <b>Read</b> <i>(interpretation — the detail below stands as measured)</i>\n"
                f"{txt}\n———")
    except Exception as e:
        print(f"[digest] llm read failed: {e}", file=sys.stderr)
        return "\n\n⚠️ <i>Read unavailable — the measured sections below are unaffected.</i>\n———"


def run(kind: str, dry_run: bool = False, use_llm: bool = True) -> None:
    now = datetime.now(tz=TZ)
    header = (f"📋 <b>Daily Brief</b> — {now:%a %d %b}" if kind == "daily"
              else f"📖 <b>Weekly Review</b> — {now:%d %b %Y}")
    sections = daily_sections(commit_state=not dry_run) if kind == "daily" else weekly_sections()
    if not sections:
        sections = ["<i>Nothing to report.</i>"]

    body = "\n\n".join(sections)
    # The read leads. The weekly runs to ~14k characters and Telegram splits at 4k, so putting
    # the interpretation last would bury it in the third message. Summary first, evidence after
    # — and the evidence is still printed in full, unaltered.
    if kind == "daily":
        cfg = _brief_cfg()
        system = SYSTEM_DAILY.format(analysts="; ".join(cfg.get("analysts") or DEFAULT_ANALYSTS),
                                     max_searches=cfg.get("max_web_searches", 8))
    else:
        system = SYSTEM
    read = llm_read(body, system) if use_llm else ""
    msg = f"{header}{read}\n\n{body}"

    print(msg) if dry_run else notifier.send(msg)
    print(f"[digest] {kind}: {len(sections)} section(s), {len(msg)} chars", file=sys.stderr)


if __name__ == "__main__":
    kind = "weekly" if "--weekly" in sys.argv else "daily"
    run(kind, dry_run="--dry-run" in sys.argv, use_llm="--no-llm" not in sys.argv)
