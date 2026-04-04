#!/usr/bin/env python3
"""
Reolink Cloud subscription renewal script.

Automates renewal of the free Basic Plan (1GB/7-day/1-cam) via direct API calls.
No browser required.

Usage:
    python3 renew-reolink.py              # check + renew if needed
    python3 renew-reolink.py --check-only # check status only, no renewal
    python3 renew-reolink.py --verbose    # print debug info to stderr
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# API constants
# ---------------------------------------------------------------------------

API_BASE        = "https://apis.reolink.com"
LOGIN_URL       = f"{API_BASE}/v1.0/oauth2/token/"
SUBS_URL        = f"{API_BASE}/v2/cloud/subscriptions/"
ORDERS_URL      = f"{API_BASE}/v2/shop/orders/"
DEVICES_URL     = f"{API_BASE}/v2/cloud/subscriptions/devices"
ASSOCIATE_URL   = f"{API_BASE}/v2/cloud/subscriptions/devices/associate"

CLIENT_ID       = "REO-.AJ,HO/L6_TG44T78KB7"
RETENTION_DAYS  = 7

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "origin":     "https://cloud.reolink.com",
    "referer":    "https://cloud.reolink.com/",
}

# ---------------------------------------------------------------------------
# Credential loading
# ---------------------------------------------------------------------------

def load_env():
    search_paths = [
        Path.home() / ".hermes" / "skills" / "reolink-renew" / ".env",
        Path(__file__).parent.parent / ".env",
    ]
    for path in search_paths:
        if path.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(path, override=False)
            except ImportError:
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, _, v = line.partition("=")
                            os.environ.setdefault(k.strip(), v.strip())
            return


def get_credentials():
    load_env()
    email    = os.environ.get("REOLINK_EMAIL")
    password = os.environ.get("REOLINK_PASSWORD")
    if not email or not password:
        bail("credentials", "REOLINK_EMAIL and REOLINK_PASSWORD must be set.")
    return email, password


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def bail(step, message):
    print("STATUS: error")
    print(f"STEP: {step}")
    print(f"MESSAGE: {message}")
    sys.exit(1)


def ts_to_date(ms_timestamp):
    """Convert millisecond epoch timestamp to YYYY-MM-DD string."""
    return datetime.fromtimestamp(ms_timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def log(verbose, *args):
    if verbose:
        print(*args, file=sys.stderr)


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

def login(session, email, password, verbose):
    log(verbose, f"[login] POST {LOGIN_URL}")
    resp = session.post(LOGIN_URL, data={
        "username":     email,
        "password":     password,
        "grant_type":   "password",
        "session_mode": "true",
        "client_id":    CLIENT_ID,
        "mfa_trusted":  "false",
    }, headers={"origin": "https://my.reolink.com", "referer": "https://my.reolink.com/"}, timeout=15)

    log(verbose, f"[login] status={resp.status_code} body={resp.text[:300]}")

    if resp.status_code != 200:
        bail("login", f"Login failed (HTTP {resp.status_code}) — check credentials.")

    token = resp.json().get("access_token")
    if not token:
        bail("login", f"No access_token in login response: {resp.text[:200]}")

    log(verbose, "[login] token acquired")
    return token


def get_active_subscription(session, token, verbose):
    log(verbose, "[check] GET active subscriptions")
    resp = session.get(SUBS_URL, params={"status": "active", "checkAutoRenewSwitch": "true"},
                       headers={"authorization": f"Bearer {token}"}, timeout=15)
    log(verbose, f"[check] status={resp.status_code} body={resp.text[:500]}")

    if resp.status_code != 200:
        bail("subscription_check", f"Active subscription check failed (HTTP {resp.status_code}).")

    items = resp.json().get("items", [])
    return items[0] if items else None


def get_inactive_subscriptions(session, token, verbose):
    not_before = int((time.time() - 365 * 24 * 3600) * 1000)
    log(verbose, f"[check] GET inactive subscriptions (not_before={not_before})")
    resp = session.get(SUBS_URL, params={"status": "inactive", "not_before": not_before},
                       headers={"authorization": f"Bearer {token}"}, timeout=15)
    log(verbose, f"[check] status={resp.status_code} body={resp.text[:800]}")

    if resp.status_code != 200:
        bail("subscription_check", f"Inactive subscription check failed (HTTP {resp.status_code}).")

    return resp.json().get("items", [])


def place_order(session, token, sub_id, plan_id, country, verbose):
    payload = {
        "items": [{
            "productId":   plan_id,
            "productType": "cloud_storage_plan",
            "qty":         1,
            "context": {
                "action":            "renew",
                "lang":              "en",
                "associateDevices":  [],
                "unassociateDevices": [],
                "associateSimCards": [],
                "subscription":      sub_id,
            },
        }],
        "amount":   "0.00",
        "currency": "USD",
        "context": {
            "country":  country,
            "timezone": "Asia/Jerusalem",
        },
    }
    log(verbose, f"[renew] POST {ORDERS_URL} payload={payload}")
    resp = session.post(ORDERS_URL, json=payload,
                        headers={"authorization": f"Bearer {token}"}, timeout=15)
    log(verbose, f"[renew] status={resp.status_code} body={resp.text[:500]}")

    if resp.status_code not in (200, 201):
        bail("renew", f"Order placement failed (HTTP {resp.status_code}): {resp.text[:200]}")

    return resp.json()


def get_subscription(session, token, sub_id, verbose):
    log(verbose, f"[verify] GET subscription/{sub_id}")
    resp = session.get(f"{SUBS_URL}{sub_id}", headers={"authorization": f"Bearer {token}"}, timeout=15)
    log(verbose, f"[verify] status={resp.status_code} body={resp.text[:500]}")

    if resp.status_code != 200:
        bail("verify", f"Subscription fetch failed (HTTP {resp.status_code}).")

    return resp.json()


def associate_device(session, token, sub_id, device_uid, verbose):
    payload = {
        "subscription": sub_id,
        "devices": [{"uid": device_uid, "retentionDays": RETENTION_DAYS}],
    }
    log(verbose, f"[associate] POST {ASSOCIATE_URL} payload={payload}")
    resp = session.post(ASSOCIATE_URL, json=payload,
                        headers={"authorization": f"Bearer {token}"}, timeout=15)
    log(verbose, f"[associate] status={resp.status_code} body={resp.text[:300]}")

    if resp.status_code not in (200, 201):
        bail("associate", f"Device association failed (HTTP {resp.status_code}): {resp.text[:200]}")


def get_devices(session, token, verbose):
    log(verbose, f"[associate] GET {DEVICES_URL}")
    resp = session.get(DEVICES_URL, headers={"authorization": f"Bearer {token}"}, timeout=15)
    log(verbose, f"[associate] status={resp.status_code} body={resp.text[:500]}")

    if resp.status_code != 200:
        bail("associate", f"Devices fetch failed (HTTP {resp.status_code}).")

    return resp.json().get("items", [])


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def run(check_only, verbose):
    import requests

    email, password = get_credentials()
    session = requests.Session()
    session.headers.update(HEADERS)

    # 1. Login
    token = login(session, email, password, verbose)

    # 2. Check for active subscription
    active = get_active_subscription(session, token, verbose)
    if active:
        expiry = ts_to_date(active["endingAt"])
        print("STATUS: active")
        print(f"EXPIRY: {expiry}")
        print("MESSAGE: Subscription is active, no action needed.")
        return

    # 3. Get most recently expired subscription
    inactive = get_inactive_subscriptions(session, token, verbose)
    if not inactive:
        bail("subscription_check", "No active or inactive subscriptions found.")

    # Sort by expiredAt descending — most recently expired first
    inactive.sort(key=lambda s: s.get("expiredAt", 0), reverse=True)
    sub = inactive[0]
    sub_id  = sub["id"]
    plan_id = sub["plan"]
    country = sub.get("country", "IL")

    log(verbose, f"[check] selected subscription id={sub_id} plan={plan_id} expiredAt={sub.get('expiredAt')}")

    if check_only:
        expiry = ts_to_date(sub["expiredAt"])
        print("STATUS: expired")
        print(f"EXPIRY: {expiry}")
        print("MESSAGE: Subscription expired. Run without --check-only to renew.")
        return

    # 4. Place renewal order
    place_order(session, token, sub_id, plan_id, country, verbose)

    # 5. Verify renewal and get new expiry
    renewed = get_subscription(session, token, sub_id, verbose)
    if renewed.get("status") != "active":
        bail("verify", f"Renewal appeared to succeed but subscription status is '{renewed.get('status')}'.")

    expiry = ts_to_date(renewed["endingAt"])

    # 6. Associate device if not already linked
    associations = renewed.get("associations", [])
    active_devices = [a for a in associations if a.get("type") == "device" and a.get("status") == "active"]

    if not active_devices:
        log(verbose, "[associate] No active device association — fetching device list")
        devices = get_devices(session, token, verbose)
        if devices:
            device_uid = devices[0]["uid"]
            log(verbose, f"[associate] Linking device uid={device_uid}")
            associate_device(session, token, sub_id, device_uid, verbose)
        else:
            log(verbose, "[associate] No devices found to associate — skipping")

    print("STATUS: renewed")
    print(f"EXPIRY: {expiry}")
    print("MESSAGE: Successfully renewed free plan (1GB/7-day/1-cam).")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Reolink Cloud subscription renewal")
    parser.add_argument("--check-only", action="store_true",
                        help="Check status only, do not renew")
    parser.add_argument("--verbose", action="store_true",
                        help="Print debug info to stderr")
    args = parser.parse_args()
    run(check_only=args.check_only, verbose=args.verbose)


if __name__ == "__main__":
    main()
