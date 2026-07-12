#!/usr/bin/env python3
"""Verify Yahoo Finance rate limit behavior.

Usage:
    python scripts/verify_yahoo_limits.py [--count N] [--sleep SECONDS]

This script tests the rate limit behavior of Yahoo Finance by making N rapid requests
and logging responses. Useful for diagnosing why Wyckoff jobs are hanging.

Example:
    python scripts/verify_yahoo_limits.py --count 60 --sleep 1
"""
import argparse
import sys
import time
import random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import data as market_data


def main():
    parser = argparse.ArgumentParser(description="Verify Yahoo Finance rate limit behavior")
    parser.add_argument("--count", type=int, default=30, help="Number of tickers to fetch")
    parser.add_argument("--sleep", type=float, default=0.5, help="Seconds between requests")
    args = parser.parse_args()

    tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "JPM", "V", "JNJ"]
    tickers = tickers * (args.count // len(tickers)) + tickers[: args.count % len(tickers)]

    print(f"=== Yahoo Finance Rate Limit Test ({args.count} requests, {args.sleep}s interval) ===\n")

    failures = []
    for i, ticker in enumerate(tickers, 1):
        start = time.time()
        try:
            data = market_data.fetch_ohlcv(ticker, days=30)
            elapsed = time.time() - start
            print(f"[+] {ticker}: OK ({elapsed:.2f}s)")
        except RuntimeError as e:
            elapsed = time.time() - start
            if "rate limit" in str(e).lower():
                print(f"⚠️  {ticker}: RATE LIMITED (attempted {elapsed:.2f}s into test)")
                failures.append((ticker, "rate_limit"))
                # Don't sleep after rate limit — test recovery
                continue
            else:
                print(f"❌ {ticker}: {e}")
                failures.append((ticker, str(e)))
        except Exception as e:
            elapsed = time.time() - start
            print(f"❌ {ticker}: {type(e).__name__}: {e}")
            failures.append((ticker, str(e)))
        time.sleep(args.sleep)

    print(f"\n=== Summary ===")
    print(f"Total: {args.count}")
    print(f"Failures: {len(failures)}")
    if failures:
        print(f"Rate limit hits: {sum(1 for _, f in failures if f == 'rate_limit')}")
    else:
        print("No issues detected!")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())