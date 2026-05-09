#!/usr/bin/env python3
"""CLI for managing holdings and watchlist. Called by Hermes."""
from __future__ import annotations
import sys
import json
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import holdings as portfolio

_CONFIG = Path(__file__).parent.parent / "config.yaml"


def _load_cfg() -> dict:
    return yaml.safe_load(_CONFIG.read_text())


def _save_cfg(cfg: dict) -> None:
    import yaml as _yaml
    _CONFIG.write_text(_yaml.dump(cfg, allow_unicode=True, default_flow_style=False))


def cmd_holdings_list():
    h = portfolio.load()
    if not h:
        print("No holdings.")
        return
    for ticker, pos in h.items():
        print(f"{ticker}: qty={pos['qty']} avg_cost=${pos['avg_cost']}")


def cmd_holdings_add(ticker: str, qty: str, avg_cost: str):
    portfolio.add(ticker, float(qty), float(avg_cost))
    print(f"Added {ticker}: {qty} @ ${avg_cost}")


def cmd_holdings_remove(ticker: str):
    if portfolio.remove(ticker):
        print(f"Removed {ticker}")
    else:
        print(f"{ticker} not found in holdings")
        sys.exit(1)


def cmd_watchlist_list():
    cfg = _load_cfg()
    for t in cfg.get("watchlist", []):
        print(t)


def cmd_watchlist_add(ticker: str):
    cfg = _load_cfg()
    wl = cfg.setdefault("watchlist", [])
    t = ticker.upper()
    if t not in wl:
        wl.append(t)
        _save_cfg(cfg)
        print(f"Added {t} to watchlist")
    else:
        print(f"{t} already on watchlist")


def cmd_watchlist_remove(ticker: str):
    cfg = _load_cfg()
    wl = cfg.get("watchlist", [])
    t = ticker.upper()
    if t in wl:
        wl.remove(t)
        _save_cfg(cfg)
        print(f"Removed {t} from watchlist")
    else:
        print(f"{t} not on watchlist")
        sys.exit(1)


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: manage.py <command> [args]")
        sys.exit(1)

    cmd = args[0]
    if cmd == "holdings-list":
        cmd_holdings_list()
    elif cmd == "holdings-add" and len(args) == 4:
        cmd_holdings_add(args[1], args[2], args[3])
    elif cmd == "holdings-remove" and len(args) == 2:
        cmd_holdings_remove(args[1])
    elif cmd == "watchlist-list":
        cmd_watchlist_list()
    elif cmd == "watchlist-add" and len(args) == 2:
        cmd_watchlist_add(args[1])
    elif cmd == "watchlist-remove" and len(args) == 2:
        cmd_watchlist_remove(args[1])
    else:
        print(f"Unknown command or wrong args: {' '.join(args)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
