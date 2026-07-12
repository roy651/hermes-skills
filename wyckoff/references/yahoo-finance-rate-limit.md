# Yahoo Finance Rate Limiting

## Symptom
Scripts hang indefinitely or timeout when fetching price data. Error messages show:
```
Edge: Too Many Requests
```

## Root Cause
Yahoo Finance's free "Edge" API returns rate limit errors as a **string in the response body** rather than standard HTTP status codes. This makes it invisible to `requests.raise_for_status()`:
- Returns `200 OK` even when rate-limited
- Response body contains `"Edge: Too Many Requests"`
- No standard `429 Too Many Requests` HTTP code

## Detection Pattern
```python
resp = requests.get(url, params=params, headers=_HEADERS, timeout=30)
response_text = resp.text

# Check for rate limit in response body (CRITICAL!)
if "Too Many Requests" in response_text:
    # Rate limited — handle backoff
```

## Fix Pattern: Exponential Backoff
```python
import time
import random

MAX_RETRIES = 5
BASE_DELAY = 2  # seconds

def fetch_ohlcv(ticker: str, days: int = 120) -> TickerData:
    for attempt in range(MAX_RETRIES):
        resp = requests.get(base_url, params=params, headers=_HEADERS, timeout=30)
        
        if "Too Many Requests" in resp.text:
            if attempt < MAX_RETRIES - 1:
                # Exponential backoff with jitter: 2s → 4s → 8s → 16s → 32s
                delay = BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                print(f"[data] {ticker}: rate limit (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {delay:.1f}s")
                time.sleep(delay)
                continue
            else:
                raise RuntimeError(f"Yahoo Finance rate limit exceeded after {MAX_RETRIES} attempts")
        
        resp.raise_for_status()
        # ... process response
```

## Rate Limit Reality
- **Not a fixed per-day quota** (e.g., 200/day)
- **Sliding window limit** — typically requires several hours of cooldown after ~30-50 rapid requests
- **Gradual reset** — not a midnight rollover
- **Risk of cascading failures** — when rate limited, retrying too aggressively can extend the block period

## Operational Recommendations
1. **Always implement backoff** in scripts that fetch Yahoo Finance data
2. **Add inter-batch delays** (1-2 seconds between ticker batches in `exit.py`)
3. **Monitor timing** — if runs consistently exceed 10 min, consider:
   - Increasing `MAX_RETRIES` to 7
   - Starting `BASE_DELAY` at 3-5 seconds
   - Adding 12-hour cache layer to reduce daily calls by ~50%
4. **Log rate-limit events** — track how often they occur to tune parameters

## When to Escalate
- Rate limits occur **multiple times per week** even with backoff
- Runs still time out with `MAX_RETRIES=7` and `BASE_DELAY=3`
- Consider switching to a paid data provider or adding a caching layer

## Reference
- See `scripts/verify_yahoo_limits.py` to test rate limit behavior
- Main implementation: `scripts/data.py::fetch_ohlcv()`
