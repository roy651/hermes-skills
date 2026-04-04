# reolink-renew

Automates renewal of the free Reolink Cloud subscription (Basic Plan — 1GB/7-day/1-cam, $0).

## Setup

1. Copy credentials template and fill in:
   ```bash
   cp .env.example .env
   # edit .env with your REOLINK_EMAIL and REOLINK_PASSWORD
   ```

2. First run creates the venv and installs dependencies automatically:
   ```bash
   bash scripts/run.sh --check-only
   ```

## Usage

```bash
# Check status (no changes)
bash scripts/run.sh --check-only

# Check + renew if expired
bash scripts/run.sh

# Verbose API debug output
bash scripts/run.sh --verbose
```

## How it works

1. Logs in via OAuth2 (`apis.reolink.com`) — no browser, no Cloudflare bypass needed
2. Checks for an active subscription → exits clean if found
3. Finds the most recently expired subscription
4. Places a $0.00 renewal order
5. Verifies the subscription is active and reads the new expiry date
6. Re-associates the camera if the device link was dropped during expiry

## Scheduling

After each run, Hermes sets a reminder one day before the expiry date. The skill is self-scheduling — no manual cron setup needed.

## Credential resolution order

1. `REOLINK_EMAIL` / `REOLINK_PASSWORD` environment variables
2. `~/.hermes/skills/reolink-renew/.env`
3. `./reolink-renew/.env` (repo-local development)
