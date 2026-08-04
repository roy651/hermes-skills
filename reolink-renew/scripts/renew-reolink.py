#!/usr/bin/env python3
"""
Reolink Cloud subscription renewal script.

Automates renewal of the free Basic Plan (1GB/7-day/1-cam) via direct API calls.
No browser required.

The Reolink account now enforces email MFA (8-digit code), so login is a two-step,
human-in-the-loop flow driven by the agent (see SKILL.md):

    python3 renew-reolink.py --login-init            # step 1: auth; if MFA needed, trigger the email code
    python3 renew-reolink.py --login-complete --code 12345678   # step 2: submit code, then renew
    python3 renew-reolink.py --check-only            # status only (uses cached trusted token if valid)
    python3 renew-reolink.py --status                # print the persisted flow state (for the agent)

A successful MFA login mints a ~30-day "trusted token" (cached in data/trusted.json), so the
NEXT monthly run can often skip MFA entirely: --login-init will authenticate silently and renew
in one shot, printing STATUS: renewed/active without ever needing a code.

STATUS lines the agent parses:
    STATUS: renewed | active | expired | code_sent | mfa_required | error
"""

import argparse
import json
import os
import random
import string
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

# MFA (email OTP) — reverse-engineered from the my.reolink.com account-center app and
# verified live. Send-code returns {id, expiringAt} and emails an 8-digit code; the code
# is then submitted on the oauth2/token call as x-verify-* headers under this scenario.
MFA_SEND_URL    = f"{API_BASE}/v2/auth/mfa/codes"
MFA_SCENARIO    = "users.login_with_password"

CLIENT_ID       = "REO-.AJ,HO/L6_TG44T78KB7"
RETENTION_DAYS  = 7

CHROME_VERSION = "148"

# ---------------------------------------------------------------------------
# Persistent state (gitignored: data/)
# ---------------------------------------------------------------------------

STATE_DIR    = Path(__file__).resolve().parent.parent / "data"
TRUSTED_FILE = STATE_DIR / "trusted.json"
FLOW_FILE    = STATE_DIR / "flow.json"

# Flow states
IDLE           = "idle"
AWAITING_READY = "awaiting_ready"
AWAITING_CODE  = "awaiting_code"


def _make_prid():
    """Generate an x-prid matching Reolink's client format: YYMMDDHHmmssSSS-{hex8}-{alnum7}."""
    now = datetime.now()
    ts = now.strftime("%y%m%d%H%M%S") + f"{now.microsecond // 1000:03d}"
    hex_part  = "".join(random.choices(string.hexdigits[:16], k=8))
    alnum_part = "".join(random.choices(string.ascii_letters + string.digits, k=7))
    return f"{ts}-{hex_part}-{alnum_part}"

HEADERS = {
    "User-Agent":         f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{CHROME_VERSION}.0.0.0 Safari/537.36",
    "Accept":             "application/json, text/plain, */*",
    "Accept-Language":    "en-US,en;q=0.9",
    "Accept-Encoding":    "gzip, deflate, br, zstd",
    "sec-ch-ua":          f'"Chromium";v="{CHROME_VERSION}", "Google Chrome";v="{CHROME_VERSION}", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile":   "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest":     "empty",
    "Sec-Fetch-Mode":     "cors",
    "Sec-Fetch-Site":     "cross-site",
    "dnt":                "1",
    "sec-gpc":            "1",
    "origin":             "https://cloud.reolink.com",
    "referer":            "https://cloud.reolink.com/",
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
# Output & state helpers
# ---------------------------------------------------------------------------

def bail(step, message):
    print("STATUS: error")
    print(f"STEP: {step}")
    print(f"MESSAGE: {message}")
    set_flow(IDLE, note=f"error at {step}")
    sys.exit(1)


def ts_to_date(ms_timestamp):
    """Convert millisecond epoch timestamp to YYYY-MM-DD string."""
    return datetime.fromtimestamp(ms_timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def log(verbose, *args):
    if verbose:
        print(*args, file=sys.stderr)


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _write_json(path, obj):
    STATE_DIR.mkdir(exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    tmp.replace(path)


def set_flow(state, expiry=None, note=None):
    data = _read_json(FLOW_FILE)
    data["state"] = state
    data["updated_at"] = int(time.time())
    if expiry is not None:
        data["expiry"] = expiry
    if note is not None:
        data["note"] = note
    _write_json(FLOW_FILE, data)


def get_flow():
    data = _read_json(FLOW_FILE)
    data.setdefault("state", IDLE)
    return data


def save_trusted(token):
    if token:
        _write_json(TRUSTED_FILE, {"trusted_token": token, "saved_at": int(time.time())})


def load_trusted():
    return _read_json(TRUSTED_FILE).get("trusted_token")


# ---------------------------------------------------------------------------
# Auth (two-step MFA)
# ---------------------------------------------------------------------------

def _is_mfa_required(body):
    if not isinstance(body, dict):
        return False
    err = body.get("error")
    if isinstance(err, dict):
        return err.get("code") == 8208 or err.get("symbol") == "mfa_required"
    return body.get("code") == 8208 or err == "mfa_required"


def _post_login(session, email, password, code=None, verify_id=None, trusted_token=None, verbose=False):
    """Single OAuth2 password-grant POST to the account-center token endpoint.

    Mirrors my.reolink.com's login() exactly:
      - always sends header x-verify-scenario: users.login_with_password
      - a cached trusted token rides in the form field mfa_trust_token (skips MFA)
      - an email code is submitted as headers x-verify-id / x-verify-code
    """
    data = {
        "username":     email,
        "password":     password,
        "grant_type":   "password",
        "session_mode": "true",
        "client_id":    CLIENT_ID,
        "mfa_trusted":  "true",
    }
    if trusted_token:
        data["mfa_trust_token"] = trusted_token
    headers = {
        "origin": "https://my.reolink.com", "referer": "https://my.reolink.com/",
        "Sec-Fetch-Site": "same-site", "x-prid": _make_prid(),
        "x-verify-scenario": MFA_SCENARIO,
    }
    if code and verify_id:
        headers["x-verify-id"]   = str(verify_id)
        headers["x-verify-code"] = str(code)
    resp = session.post(LOGIN_URL, data=data, headers=headers, timeout=15)
    log(verbose, f"[login] status={resp.status_code} body={resp.text[:400]}")
    try:
        return resp, resp.json()
    except Exception:
        return resp, {}


def _extract_trusted(body):
    """Pull the ~30-day trusted token out of a successful login body, if present."""
    for key in ("mfa_trust_token", "mfa_trusted_token", "trusted_token", "trustedToken"):
        if body.get(key):
            return body[key]
    return None


def send_mfa_code(session, email, password, verbose=False):
    """Ask Reolink to email the 8-digit OTP. Returns the verification id (to submit with the code)."""
    log(verbose, f"[mfa] POST {MFA_SEND_URL}")
    resp = session.post(
        MFA_SEND_URL,
        json={"clientId": CLIENT_ID, "scenario": MFA_SCENARIO, "method": "email",
              "data": {"emailAddress": email}},
        headers={"origin": "https://my.reolink.com", "referer": "https://my.reolink.com/",
                 "Sec-Fetch-Site": "same-site", "x-prid": _make_prid()},
        timeout=15)
    log(verbose, f"[mfa] status={resp.status_code} body={resp.text[:300]}")
    if resp.status_code not in (200, 201):
        bail("mfa_send", f"Could not trigger the email code (HTTP {resp.status_code}): {resp.text[:200]}")
    return resp.json().get("id")


def authenticate(session, email, password, code=None, trigger_email=True, verbose=False):
    """Return an access token, or None if an email code is still needed.

    - With a cached trusted token: try silent login first (skips MFA for ~30 days).
    - With a user-supplied code: submit it against the stored verify id.
    - Otherwise: password grant; on mfa_required — if trigger_email, send the code, stash the
      verify id and return None; if not (a pure check), return None WITHOUT emailing anything.
    """
    # 1. Silent path via a cached trusted token.
    if code is None:
        trusted = load_trusted()
        if trusted:
            resp, body = _post_login(session, email, password, trusted_token=trusted, verbose=verbose)
            if resp.status_code == 200 and body.get("access_token"):
                log(verbose, "[login] trusted-token login OK — MFA skipped")
                save_trusted(_extract_trusted(body) or trusted)
                return body["access_token"]
            log(verbose, "[login] trusted token rejected/expired — falling back to MFA")

    # 2. Submit a user-supplied code against the verify id from --login-init.
    if code is not None:
        verify_id = get_flow().get("mfa_id")
        if not verify_id:
            bail("login", "No pending MFA request — run --login-init first to get a fresh code.")
        resp, body = _post_login(session, email, password, code=code, verify_id=verify_id, verbose=verbose)
        if resp.status_code == 200 and body.get("access_token"):
            save_trusted(_extract_trusted(body))
            return body["access_token"]
        sym = (body.get("error") or {}).get("symbol") if isinstance(body.get("error"), dict) else None
        if sym in ("mfa_code_incorrect", "mfa_session_not_found") or _is_mfa_required(body):
            bail("login", "The code was rejected (wrong or expired). Reply 'ready' to get a fresh one.")
        bail("login", f"Login failed (HTTP {resp.status_code}): {json.dumps(body)[:200]}")

    # 3. No code and no trusted token: password grant to see whether MFA is demanded.
    resp, body = _post_login(session, email, password, verbose=verbose)
    if resp.status_code == 200 and body.get("access_token"):
        save_trusted(_extract_trusted(body))
        return body["access_token"]
    if _is_mfa_required(body):
        if not trigger_email:
            return None  # pure check — caller reports mfa_required without spamming an email
        verify_id = send_mfa_code(session, email, password, verbose=verbose)
        set_flow(AWAITING_CODE, note="init: code emailed")
        data = _read_json(FLOW_FILE); data["mfa_id"] = verify_id; _write_json(FLOW_FILE, data)
        return None
    bail("login", f"Login failed (HTTP {resp.status_code}): {json.dumps(body)[:200]}")


# ---------------------------------------------------------------------------
# Subscription API (unchanged)
# ---------------------------------------------------------------------------

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
# Post-auth flow: check status, renew if needed, verify, associate
# ---------------------------------------------------------------------------

def _select_subscription(session, token, verbose):
    active = get_active_subscription(session, token, verbose)
    if active:
        return active, True
    inactive = get_inactive_subscriptions(session, token, verbose)
    if not inactive:
        bail("subscription_check", "No active or inactive subscriptions found.")
    inactive.sort(key=lambda s: s.get("expiredAt", 0), reverse=True)
    return inactive[0], False


def report_status(session, token, verbose):
    """--check-only: report without renewing."""
    sub, active = _select_subscription(session, token, verbose)
    if active:
        expiry = ts_to_date(sub["endingAt"])
        set_flow(IDLE, expiry=expiry, note="check-only: active")
        print("STATUS: active")
        print(f"EXPIRY: {expiry}")
        print("MESSAGE: Subscription is active.")
    else:
        expiry = ts_to_date(sub["expiredAt"])
        set_flow(IDLE, expiry=expiry, note="check-only: expired")
        print("STATUS: expired")
        print(f"EXPIRY: {expiry}")
        print("MESSAGE: Subscription expired. Run the renewal flow to restore it.")


RENEW_WINDOW_DAYS = 2  # renew only when expired or within this many days of expiry


def check_and_maybe_renew(session, token, force, verbose):
    """Decide, then act: renew only if expired or within RENEW_WINDOW_DAYS of expiry.

    This makes every buffered reminder idempotent — a reminder that fires early just
    reports 'active' and places no order, so renewals never stack redundantly.
    """
    active = get_active_subscription(session, token, verbose)
    if active and not force:
        expiry_ms = active["endingAt"]
        days_left = (expiry_ms / 1000 - time.time()) / 86400
        if days_left > RENEW_WINDOW_DAYS:
            expiry = ts_to_date(expiry_ms)
            set_flow(IDLE, expiry=expiry, note="active, no renewal needed")
            print("STATUS: active")
            print(f"EXPIRY: {expiry}")
            print(f"MESSAGE: Active with {days_left:.1f} days left (> {RENEW_WINDOW_DAYS}). No renewal needed.")
            return
    do_renew(session, token, verbose)


def do_renew(session, token, verbose):
    """Place the renewal order, verify, re-associate the camera, report new expiry."""
    sub, active = _select_subscription(session, token, verbose)
    sub_id  = sub["id"]
    plan_id = sub["plan"]
    country = sub.get("country", "IL")
    log(verbose, f"[check] selected subscription id={sub_id} plan={plan_id} status={sub.get('status')}")

    place_order(session, token, sub_id, plan_id, country, verbose)

    renewed = get_subscription(session, token, sub_id, verbose)
    if renewed.get("status") != "active":
        bail("verify", f"Renewal appeared to succeed but subscription status is '{renewed.get('status')}'.")
    expiry = ts_to_date(renewed["endingAt"])

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

    set_flow(IDLE, expiry=expiry, note="renewed")
    print("STATUS: renewed")
    print(f"EXPIRY: {expiry}")
    print("MESSAGE: Successfully renewed free plan (1GB/7-day/1-cam).")


# ---------------------------------------------------------------------------
# Command entry points
# ---------------------------------------------------------------------------

def cmd_login_init(check_only, force, verbose):
    import requests
    email, password = get_credentials()
    session = requests.Session()
    session.headers.update(HEADERS)

    token = authenticate(session, email, password, code=None, verbose=verbose)
    if token is None:
        # MFA required — email code triggered (flow set to AWAITING_CODE by authenticate()).
        print("STATUS: code_sent")
        print("MESSAGE: An 8-digit code was emailed by Reolink. Provide it to complete login.")
        return

    # Trusted-token silent login worked — no code needed.
    if check_only:
        report_status(session, token, verbose)
    else:
        check_and_maybe_renew(session, token, force, verbose)


def cmd_login_complete(code, force, verbose):
    import requests
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if not code:
        bail("login", "No numeric code provided.")
    email, password = get_credentials()
    session = requests.Session()
    session.headers.update(HEADERS)

    token = authenticate(session, email, password, code=code, verbose=verbose)
    if token is None:
        bail("login", "Login still requires MFA after submitting the code.")
    check_and_maybe_renew(session, token, force, verbose)


def cmd_check_only(verbose):
    import requests
    email, password = get_credentials()
    session = requests.Session()
    session.headers.update(HEADERS)

    token = authenticate(session, email, password, code=None, trigger_email=False, verbose=verbose)
    if token is None:
        set_flow(IDLE, note="check: MFA needed (no email sent)")
        print("STATUS: mfa_required")
        print("MESSAGE: Login needs an email code — no code was sent. Start the renewal flow when ready.")
        return
    report_status(session, token, verbose)


def cmd_status():
    flow = get_flow()
    print(f"STATE: {flow.get('state')}")
    if flow.get("expiry"):
        print(f"EXPIRY: {flow['expiry']}")
    if flow.get("updated_at"):
        print(f"UPDATED: {ts_to_date(flow['updated_at'] * 1000)}")
    if flow.get("note"):
        print(f"NOTE: {flow['note']}")
    trusted = _read_json(TRUSTED_FILE)
    if trusted.get("saved_at"):
        age_days = (time.time() - trusted["saved_at"]) / 86400
        print(f"TRUSTED_TOKEN: cached, age {age_days:.1f}d (Reolink trust window ~30d)")
    else:
        print("TRUSTED_TOKEN: none")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Reolink Cloud subscription renewal")
    parser.add_argument("--login-init", action="store_true",
                        help="Step 1: authenticate; if MFA is required, trigger the email code")
    parser.add_argument("--login-complete", action="store_true",
                        help="Step 2: submit the emailed --code, then renew")
    parser.add_argument("--code", help="The 8-digit MFA code (with --login-complete)")
    parser.add_argument("--force", action="store_true",
                        help="Renew even if the plan is active with more than 2 days left")
    parser.add_argument("--check-only", action="store_true",
                        help="Check status only, do not renew")
    parser.add_argument("--status", action="store_true",
                        help="Print the persisted flow state and exit (no network)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print debug info to stderr")
    args = parser.parse_args()

    if args.status:
        cmd_status()
    elif args.login_complete:
        cmd_login_complete(args.code, args.force, args.verbose)
    elif args.login_init:
        cmd_login_init(args.check_only, args.force, args.verbose)
    elif args.check_only:
        cmd_check_only(args.verbose)
    else:
        # Bare invocation = start the interactive renewal (step 1).
        cmd_login_init(check_only=False, force=args.force, verbose=args.verbose)


if __name__ == "__main__":
    main()
