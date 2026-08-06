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
    "Provider":     ("#e87ba4", "#d55181"),
    "Unattributed": ("#8a8a85", "#9a9a95"),
}


def collect(days: int) -> dict[str, dict[str, tuple[int, int]]]:
    """-> {date: {bucket: (seconds, episodes)}} for the last `days` complete days."""
    start = (datetime.now(tz=timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    per_day: dict[str, list] = defaultdict(list)
    if not _CSV.exists():
        return {}
    with open(_CSV) as f:
        for row in csv.reader(f):
            if len(row) < 5 or row[0][:10] < start or not row[0][:4].isdigit():
                continue
            per_day[row[0][:10]].append(tuple(c.strip().upper() == "LOSS" for c in row[1:5]))
    return {day: _monitor.attribute(samples) for day, samples in sorted(per_day.items())}


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


def render(data: dict, days_n: int) -> str:
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
<h1>Network root-cause — last {days_n} days</h1>
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
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--send", action="store_true", help="post the HTML to Telegram")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    data = collect(args.days)
    if not data:
        print("[root_cause] no data in window", file=sys.stderr)
        return 1

    out = Path(args.out) if args.out else _LOGS / f"root-cause-{datetime.now():%Y%m%d}.html"
    out.write_text(render(data, args.days), encoding="utf-8")
    print(f"[root_cause] {len(data)} days -> {out}")

    if args.send:
        total = sum(v[0] for d in data.values() for v in d.values())
        send_document(out, f"📊 Network root-cause — last {args.days} days · {total}s attributed")
        print("[root_cause] sent to Telegram")
    return 0


if __name__ == "__main__":
    sys.exit(main())
