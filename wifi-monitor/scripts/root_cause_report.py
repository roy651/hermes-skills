#!/usr/bin/env python3
"""Periodic root-cause report — where network downtime actually came from, day by day.

The daily digest answers "what broke yesterday". This answers "what keeps breaking", by charting the
same root-cause buckets over a month: one line per bucket, one chart for seconds lost and a second
for episode counts (a 10s outage and two 5s ones are different problems).

Renders a self-contained HTML file — inline SVG, no libraries, no network — and optionally sends it
to Telegram as a document. Attribution is imported from monitor.py so the monthly view and the daily
digest can never disagree.

    python root_cause_report.py --days 30              # write HTML, print the path
    python root_cause_report.py --days 30 --send       # also post it to Telegram
"""
from __future__ import annotations
import argparse
import csv
import importlib.util
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

_HERE = Path(__file__).resolve().parent
_LOGS = _HERE.parent / "logs"
_CSV = _LOGS / "wifi_monitor.csv"

# Reuse the daily digest's rule ladder rather than restating it — one definition of "root cause".
_spec = importlib.util.spec_from_file_location("monitor", _HERE / "monitor.py")
_monitor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_monitor)
_monitor._load_dotenv()          # same .env the daemon uses — that is where the Telegram token lives
BUCKETS = [name for name, _ in _monitor._BUCKETS]

# Categorical slots 1-5 of the validated default palette, in fixed order — colour follows the
# bucket, never its rank, so a quiet week never repaints the survivors.
# Validated (adjacent pairlist): light worst CVD ΔE 9.1 / normal 19.6; dark 8.4 / 19.3.
# Light mode trips the <3:1 contrast warn, so the relief rule applies — hence direct labels
# AND the table view below the charts.
PALETTE = {
    "WiFi hop":     ("#2a78d6", "#3987e5"),
    "Wired hop":    ("#eb6834", "#d95926"),
    "pfSense/host": ("#1baf7a", "#199e70"),
    "Modem link":   ("#eda100", "#c98500"),
    # Not an outage — a muted tint of the Modem link hue, so it reads as related but recessive.
    "Modem unresponsive": ("#c9a55c", "#a88b4e"),
    "Provider":     ("#e87ba4", "#d55181"),
    "Scheduled":    ("#8a8a85", "#9a9a95"),   # known maintenance — deliberately recessive grey
    "Unattributed": ("#6f6f6a", "#7f7f7a"),
}


def collect(since: str, until: str) -> dict[str, dict[str, tuple[int, int]]]:
    """-> {date: {bucket: (seconds, episodes)}} for dates in [since, until] (inclusive, ISO)."""
    per_day: dict[str, list] = defaultdict(list)
    if not _CSV.exists():
        return {}
    with open(_CSV) as f:
        for row in csv.reader(f):
            if len(row) < 5 or not row[0][:4].isdigit():
                continue
            day = row[0][:10]
            if day < since or day > until:
                continue
            # 6th column (alt / bypass probe) added 2026-08-06; absent or empty = not measured.
            alt_raw = row[5].strip() if len(row) > 5 else ""
            per_day[day].append((row[0][11:16],
                                 *(c.strip().upper() == "LOSS" for c in row[1:5]),
                                 None if alt_raw == "" else alt_raw.upper() == "LOSS"))
    return {day: _monitor.attribute(samples) for day, samples in sorted(per_day.items())}


def resolve_window(args) -> tuple[str, str, str]:
    """-> (since, until, label). --month wins over --since/--until, which win over --days."""
    if args.month:
        start = datetime.strptime(args.month, "%Y-%m").replace(tzinfo=timezone.utc)
        nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start.strftime("%Y-%m-%d"), (nxt - timedelta(days=1)).strftime("%Y-%m-%d"), \
            start.strftime("%B %Y")
    until = args.until or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    if args.since:
        return args.since, until, f"{args.since} → {until}"
    since = (datetime.strptime(until, "%Y-%m-%d") - timedelta(days=args.days - 1)).strftime("%Y-%m-%d")
    return since, until, f"last {args.days} days"


def _svg(data: dict, days: list[str], idx: int, title: str, unit: str) -> str:
    """One line chart. idx 0 = seconds, 1 = episodes."""
    W, H, L, R, T, B = 860, 300, 54, 118, 26, 46
    pw, ph = W - L - R, H - T - B
    series = {b: [data[d].get(b, (0, 0))[idx] for d in days] for b in BUCKETS
              if any(data[d].get(b, (0, 0))[idx] for d in days)}
    if not series:
        return f'<p class="empty">{title}: nothing to plot — no loss recorded.</p>'

    top = max(max(v) for v in series.values()) or 1
    top = max(1, int(top * 1.15))
    x = lambda i: L + (pw * i / max(1, len(days) - 1))
    y = lambda v: T + ph - (ph * v / top)

    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{title}">',
           f'<text class="ttl" x="{L}" y="16">{title}</text>']

    for gv in range(0, 5):                                   # recessive gridlines + y ticks
        val = top * gv / 4
        out.append(f'<line class="grid" x1="{L}" y1="{y(val):.1f}" x2="{L+pw}" y2="{y(val):.1f}"/>')
        out.append(f'<text class="tick" x="{L-8}" y="{y(val)+4:.1f}" text-anchor="end">{val:.0f}</text>')

    step = max(1, len(days) // 8)
    for i, d in enumerate(days):
        if i % step == 0 or i == len(days) - 1:
            out.append(f'<text class="tick" x="{x(i):.1f}" y="{T+ph+18}" text-anchor="middle">{d[5:]}</text>')

    ranked = sorted(series, key=lambda b: -sum(series[b]))
    for b in ranked:
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(series[b]))
        out.append(f'<polyline class="ln" style="stroke:var(--c-{_slug(b)})" points="{pts}"/>')
    for b in ranked[:4]:                                     # direct-label the top 4 (relief rule)
        last = series[b][-1]
        out.append(f'<text class="lbl" style="fill:var(--c-{_slug(b)})" x="{L+pw+8}" '
                   f'y="{y(last)+4:.1f}">{b} {last:.0f}{unit}</text>')

    out.append(f'<g class="hov"><line class="cross" y1="{T}" y2="{T+ph}"/></g>')
    out.append(f'<rect class="cap" x="{L}" y="{T}" width="{pw}" height="{ph}" '
               f'data-n="{len(days)}" data-l="{L}" data-w="{pw}"/>')
    out.append("</svg>")
    return "".join(out)


def _slug(name: str) -> str:
    return name.lower().replace("/", "-").replace(" ", "-")


def render(data: dict, label: str) -> str:
    days = list(data)
    totals = {b: (sum(data[d].get(b, (0, 0))[0] for d in days),
                  sum(data[d].get(b, (0, 0))[1] for d in days)) for b in BUCKETS}
    totals = {b: v for b, v in totals.items() if v[0]}
    worst = max(totals, key=lambda b: totals[b][0]) if totals else "—"
    grand = sum(v[0] for v in totals.values())

    vars_light = "".join(f"--c-{_slug(b)}:{c[0]};" for b, c in PALETTE.items())
    vars_dark = "".join(f"--c-{_slug(b)}:{c[1]};" for b, c in PALETTE.items())

    rows = "".join(
        f"<tr><td><span class='sw' style='background:var(--c-{_slug(b)})'></span>{b}</td>"
        f"<td>{v[0]}s</td><td>{v[1]}</td>"
        f"<td>{100*v[0]/grand:.0f}%</td></tr>" for b, v in sorted(totals.items(), key=lambda kv: -kv[1][0]))

    legend = "".join(f"<span class='lg'><i style='background:var(--c-{_slug(b)})'></i>{b}</span>"
                     for b in totals)

    return f"""<style>
.viz{{color-scheme:light;{vars_light}--s1:#fcfcfb;--tp:#0b0b0b;--ts:#52514e;--gr:#e6e5e2;
font:14px/1.5 ui-sans-serif,system-ui,sans-serif;background:var(--s1);color:var(--tp);padding:24px;max-width:960px;margin:0 auto}}
@media (prefers-color-scheme:dark){{:root:where(:not([data-theme=light])) .viz{{color-scheme:dark;{vars_dark}
--s1:#1a1a19;--tp:#fff;--ts:#c3c2b7;--gr:#333330}}}}
:root[data-theme=dark] .viz{{color-scheme:dark;{vars_dark}--s1:#1a1a19;--tp:#fff;--ts:#c3c2b7;--gr:#333330}}
.viz h1{{font-size:19px;margin:0 0 2px}} .viz .sub{{color:var(--ts);margin:0 0 20px}}
.viz svg{{width:100%;height:auto;overflow:visible;margin:8px 0 4px}}
.ttl{{font-size:13px;font-weight:600;fill:var(--tp)}} .tick{{font-size:11px;fill:var(--ts)}}
.grid{{stroke:var(--gr);stroke-width:1}} .ln{{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}}
.lbl{{font-size:11px;font-weight:600}} .cross{{stroke:var(--ts);stroke-width:1;stroke-dasharray:3 3;opacity:0}}
.cap{{fill:transparent}} .empty{{color:var(--ts)}}
.viz table{{border-collapse:collapse;width:100%;margin-top:18px;font-size:13px}}
.viz th,.viz td{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--gr)}}
.viz th{{color:var(--ts);font-weight:600}} .viz td:nth-child(n+2){{text-align:right;font-variant-numeric:tabular-nums}}
.sw{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:8px;vertical-align:middle}}
.lg{{display:inline-flex;align-items:center;gap:6px;margin-right:16px;font-size:12px;color:var(--ts)}}
.lg i{{width:10px;height:10px;border-radius:2px;display:inline-block}}
#tip{{position:fixed;pointer-events:none;background:var(--s1);border:1px solid var(--gr);border-radius:6px;
padding:6px 9px;font-size:12px;opacity:0;box-shadow:0 2px 10px rgba(0,0,0,.14);white-space:nowrap}}
</style>
<div class="viz">
<h1>Network root-cause — {label}</h1>
<p class="sub">{grand}s of loss attributed across {len(days)} days · dominant cause: <b>{worst}</b>.
Each sample is assigned to exactly one bucket, most-upstream first.</p>
<div>{legend}</div>
{_svg(data, days, 0, "Seconds lost per day", "s")}
{_svg(data, days, 1, "Episodes per day", "")}
<table><thead><tr><th>Root cause</th><th>Total</th><th>Episodes</th><th>Share</th></tr></thead>
<tbody>{rows}</tbody></table>
<div id="tip"></div>
</div>
<script>
const days={days!r};
document.querySelectorAll('.cap').forEach(cap=>{{
  const svg=cap.closest('svg'), cross=svg.querySelector('.cross'), tip=document.getElementById('tip');
  const L=+cap.dataset.l, W=+cap.dataset.w, N=+cap.dataset.n;
  cap.addEventListener('mousemove',e=>{{
    const r=svg.getBoundingClientRect(), vb=svg.viewBox.baseVal;
    const sx=(e.clientX-r.left)*vb.width/r.width;
    const i=Math.max(0,Math.min(N-1,Math.round((sx-L)/W*(N-1))));
    const gx=L+W*i/(N-1);
    cross.setAttribute('x1',gx); cross.setAttribute('x2',gx); cross.style.opacity=1;
    const rows=[...svg.querySelectorAll('.lbl')].map(t=>t.textContent).join(' · ');
    tip.innerHTML='<b>'+days[i]+'</b>'+(rows?'<br>'+rows:'');
    tip.style.left=(e.clientX+14)+'px'; tip.style.top=(e.clientY+14)+'px'; tip.style.opacity=1;
  }});
  cap.addEventListener('mouseleave',()=>{{cross.style.opacity=0;tip.style.opacity=0;}});
}});
</script>"""


def render_png(data: dict, label: str, out: Path) -> Path | None:
    """Same two charts as a PNG. Telegram renders an image inline; an .html attachment only offers a
    download, which on a phone is effectively unreadable. Returns None if matplotlib is unavailable
    (the report still ships as HTML) — the daemon's own interpreter does not need the dependency."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    days = list(data)
    series = {b: ([data[d].get(b, (0, 0))[0] for d in days],
                  [data[d].get(b, (0, 0))[1] for d in days]) for b in BUCKETS}
    series = {b: v for b, v in series.items() if any(v[0])}
    if not series:
        return None

    fig, axes = plt.subplots(2, 1, figsize=(11, 7.5), sharex=True, facecolor="#fcfcfb")
    for ax, idx, title in ((axes[0], 0, "Seconds lost per day"), (axes[1], 1, "Episodes per day")):
        ax.set_facecolor("#fcfcfb")
        for b, vals in series.items():
            ax.plot(days, vals[idx], linewidth=2, color=PALETTE[b][0], label=b,
                    solid_capstyle="round", solid_joinstyle="round")
        ax.set_title(title, fontsize=11, fontweight="600", color="#0b0b0b", loc="left")
        ax.grid(True, color="#e6e5e2", linewidth=1)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color("#e6e5e2")
        ax.tick_params(colors="#52514e", labelsize=9)

    step = max(1, len(days) // 10)
    axes[1].set_xticks(range(0, len(days), step))
    axes[1].set_xticklabels([days[i][5:] for i in range(0, len(days), step)], rotation=45, ha="right")
    axes[0].legend(loc="upper left", frameon=False, fontsize=9, ncol=min(len(series), 4),
                   labelcolor="#52514e")
    fig.suptitle(f"Network root-cause — {label}", fontsize=13, fontweight="600",
                 color="#0b0b0b", x=0.02, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    png = out.with_suffix(".png")
    fig.savefig(png, dpi=140, facecolor="#fcfcfb")
    plt.close(fig)
    return png


def send_photo(path: Path, caption: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "391626535")
    with open(path, "rb") as fh:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendPhoto",
                          data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                          files={"photo": (path.name, fh, "image/png")}, timeout=30)
    r.raise_for_status()


def send_document(path: Path, caption: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "391626535")
    with open(path, "rb") as fh:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendDocument",
                          data={"chat_id": chat_id, "caption": caption},
                          files={"document": (path.name, fh, "text/html")}, timeout=30)
    r.raise_for_status()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="rolling window ending today (default 30)")
    ap.add_argument("--month", help="a specific calendar month, e.g. 2026-07")
    ap.add_argument("--since", help="start date YYYY-MM-DD")
    ap.add_argument("--until", help="end date YYYY-MM-DD (default today)")
    ap.add_argument("--send", action="store_true", help="post the HTML to Telegram")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    since, until, label = resolve_window(args)
    data = collect(since, until)
    if not data:
        print(f"[root_cause] no data between {since} and {until}", file=sys.stderr)
        return 1

    out = Path(args.out) if args.out else _LOGS / f"root-cause-{since}_{until}.html"
    out.write_text(render(data, label), encoding="utf-8")
    print(f"[root_cause] {len(data)} days ({label}) -> {out}")

    png = render_png(data, label, out)
    if png:
        print(f"[root_cause] chart image -> {png}")

    if args.send:
        # Rank the buckets in the caption: the PNG's light palette trips the <3:1 contrast warn, so
        # the numbers must be legible without relying on colour alone (and it reads fine on a phone).
        totals: dict[str, int] = {}
        for day in data.values():
            for bucket, (secs, _) in day.items():
                totals[bucket] = totals.get(bucket, 0) + secs
        ranked = sorted(totals.items(), key=lambda kv: -kv[1])
        summary = " · ".join(f"{b} {s}s" for b, s in ranked[:4])
        caption = f"📊 <b>Network root-cause — {label}</b>\n{sum(totals.values())}s attributed\n{summary}"

        if png:
            send_photo(png, caption)                                  # renders inline
            send_document(out, "Interactive version (hover + table)")  # for the detail
        else:
            send_document(out, caption)
        print("[root_cause] sent to Telegram")
    return 0


if __name__ == "__main__":
    sys.exit(main())
