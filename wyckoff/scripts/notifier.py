from __future__ import annotations
import os
import re
import socket
import sys
from datetime import datetime
from pathlib import Path
import requests
import urllib3.util.connection as _urllib3_conn
from dotenv import load_dotenv

load_dotenv(Path.home() / ".hermes" / ".env")

# api.telegram.org publishes both A and AAAA records, but this host's WiFi advertises an IPv6 default
# route with no working IPv6 egress. requests/urllib3 (unlike curl, which does Happy-Eyeballs) tries the
# IPv6 address and hangs until timeout. Force IPv4-only DNS resolution. Harmless if IPv6 egress is later
# restored — IPv4 to Telegram works either way.
_urllib3_conn.allowed_gai_family = lambda: socket.AF_INET


_MAX = 4096


def _post(token: str, chat_id: int, text: str) -> None:
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )
    if not resp.ok:
        # raise_for_status() reports only "400 Bad Request" and drops Telegram's own
        # description, which is the part that names the actual problem (an unbalanced
        # tag, a too-long message). Surface it before raising.
        try:
            detail = resp.json().get("description", "")
        except ValueError:
            detail = resp.text[:200]
        print(f"[notifier] telegram {resp.status_code}: {detail}", file=sys.stderr)
    resp.raise_for_status()
    # Log the id Telegram assigned + which bot sent it. A digest arriving twice is otherwise
    # unattributable: one id here but two messages in the chat means the second copy came from
    # somewhere else (another host holding this token), not from a double send on this box.
    try:
        print(f"[notifier] sent message_id={resp.json()['result']['message_id']} "
              f"chat={chat_id} bot={token.split(':')[0]}")
    except (ValueError, KeyError):
        pass


# Digests otherwise exist only inside Telegram, so a later review has nothing to read back. Archive
# every outbound message here — the one choke point every digest passes through. Gitignored (data/).
_ARCHIVE = Path(__file__).parent.parent / "data" / "reports"


def _archive(text: str) -> None:
    try:
        _ARCHIVE.mkdir(parents=True, exist_ok=True)
        headline = re.sub(r"<[^>]+>", "", text.split("\n")[0])
        slug = re.sub(r"[^a-z0-9]+", "-", headline.lower()).strip("-")[:40] or "digest"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (_ARCHIVE / f"{stamp}-{slug}.txt").write_text(text)
    except OSError as e:                      # a full/read-only disk must never block the alert itself
        print(f"[notifier] archive failed: {e}", file=sys.stderr)


# Telegram parses parse_mode=HTML per message and rejects the WHOLE message if a tag is left
# open, so a chunk boundary may not fall inside one. Track the open tags at each cut, close them
# at the end of the chunk and reopen them at the start of the next.
_TAG = re.compile(r"<(/?)([a-zA-Z]+)(?:\s[^>]*)?>")


def _balance(chunk: str, carry: list[str]) -> tuple[str, list[str]]:
    """Reopen `carry` at the top of the chunk, close whatever is still open at the bottom."""
    stack = list(carry)
    for closing, tag in _TAG.findall(chunk):
        tag = tag.lower()
        if closing:
            if tag in stack:
                stack.reverse()
                stack.remove(tag)
                stack.reverse()
        else:
            stack.append(tag)
    opener = "".join(f"<{t}>" for t in carry)
    closer = "".join(f"</{t}>" for t in reversed(stack))
    return opener + chunk + closer, stack


def _hardwrap(line: str, budget: int) -> list[str]:
    """Break a single line that is itself longer than the budget. Prefers a space, and never
    cuts inside a `<...>` tag. Real digests are line-broken, but an un-split line would be
    rejected outright as too long, so fall back rather than emit an oversized chunk."""
    out = []
    while len(line) > budget:
        cut = line.rfind(" ", 0, budget)
        if cut <= 0:
            cut = budget
        lt, gt = line.rfind("<", 0, cut), line.rfind(">", 0, cut)
        if lt > gt:                           # cut landed inside a tag — back off before it
            cut = lt if lt > 0 else cut
        out.append(line[:cut])
        line = line[cut:].lstrip(" ")
    out.append(line)
    return out


def _split(text: str, limit: int = _MAX) -> list[str]:
    """Chunk at line boundaries, preferring a blank line (between blocks) over a hard cut,
    and keep each chunk's HTML self-contained."""
    if len(text) <= limit:
        return [text]
    budget = limit - 96                       # headroom for the reopened/closed tags
    raw, current, size = [], [], 0
    lines = []
    for line in text.split("\n"):
        lines.extend(_hardwrap(line, budget) if len(line) > budget else [line])
    for line in lines:
        if current and size + len(line) + 1 > budget:
            cut = len(current)
            for i in range(len(current) - 1, int(len(current) * 0.5), -1):
                if not current[i].strip():    # split between blocks when one is near the cut
                    cut = i
                    break
            raw.append("\n".join(current[:cut]))
            current = current[cut:]
            while current and not current[0].strip():
                current.pop(0)
            size = sum(len(l) + 1 for l in current)
        current.append(line)
        size += len(line) + 1
    if current:
        raw.append("\n".join(current))

    chunks, carry = [], []
    for part in raw:
        balanced, carry = _balance(part, carry)
        chunks.append(balanced)
    return chunks


def send(text: str) -> None:
    _archive(text)
    # WYCKOFF_SILENT lets a producer keep writing to the archive while posting nothing. The
    # weekly digest reads those archives and delivers one consolidated message, so the slow
    # LLM jobs that feed it run unchanged and simply stop double-posting.
    if os.environ.get("WYCKOFF_SILENT") == "1":
        print(f"[notifier] silent mode — archived {len(text)} chars, not sent", file=sys.stderr)
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ["TELEGRAM_TOKEN"]
    chat_id = int(os.environ.get("TELEGRAM_CHAT_ID", "391626535"))
    failures = []
    for chunk in _split(text):
        # One rejected chunk must not swallow the rest of the digest: deliver what we can,
        # then report. Previously the first bad chunk aborted the send and the whole brief
        # was lost while the job still exited 0.
        try:
            _post(token, chat_id, chunk)
        except requests.RequestException as e:
            failures.append(e)
    if failures:
        raise failures[0]
