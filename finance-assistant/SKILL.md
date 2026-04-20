# finance-assistant

> **Status: Work in progress — infrastructure deployed, not yet fully tested. See `.claude/handoff.md` in hermes-skills for next steps.**

Personal finance assistant for Roy & family, powered by ActualBudget.

## Architecture

```
ActualBudget (Docker :5006) ← MoneyMan (daily cron) ← encrypted config.enc
        ↑
   actualpy SDK
        ↑
   bot_handler.py  ←→  Telegram (standalone bot + Hermes tool)
```

## Setup

### 1. ActualBudget

```bash
docker run -d --name actual-budget --restart unless-stopped \
  -p 5006:5006 -v actual_data:/data \
  actualbudget/actual-server:latest-alpine
```

Open http://192.168.1.17:5006, create budget, set password.  
Create accounts: Bank Leumi (checking), Max – Roy, Cal – Roy, Isracard – Roy, Max – Wife, Cal – Wife (all credit card type).

### 2. MoneyMan Importer

```bash
cd ~/.hermes/skills/finance-assistant/importer
git clone https://github.com/daniel-hauser/moneyman .
npm install

cp config.json.example config.json
# Edit config.json with credentials

# Encrypt (do once, then delete plaintext)
age-keygen -o ~/.finance-key   # keep this key safe
age --encrypt -i ~/.finance-key config.json > config.enc
rm config.json

chmod 600 ~/.finance-key
```

Cron for daily credit-card import:
```cron
0 6 * * * bash ~/.hermes/skills/finance-assistant/importer/run-import.sh >> ~/.hermes/skills/finance-assistant/logs/import.log 2>&1
```

Cron for scheduled reports (runs every hour, sends only when schedule.json matches):
```cron
0 * * * * cd ~/.hermes/skills/finance-assistant && .venv/bin/python scripts/report.py --check-schedule >> logs/report.log 2>&1
```

### 3. Finance Assistant Bot

```bash
cd ~/.hermes/skills/finance-assistant
cp .env.example .env
# Edit .env with all credentials

bash scripts/run.sh
# Or as a daemon:
nohup bash scripts/run.sh >> logs/daemon.log 2>&1 & echo $! > finance-assistant.pid
```

## Bank Leumi Import

**Never stored.** Two ways to trigger:

- Via SSH: `bash importer/run-bank-import.sh` — prompts for password in terminal
- Via Telegram: `/sync_bank` — bot asks for password in chat, passes in memory only

Set `LEUMI_ID` in `.env` for the ID field (not sensitive — it's a national ID, not a password).

## Commands

| Command | Description |
|---------|-------------|
| `/balance` | Account balances |
| `/budget` | Month budget vs. actual |
| `/report [weekly\|monthly]` | Full report |
| `/sync` | Run credit card import now |
| `/sync_bank` | Run Bank Leumi import |
| `/ask <question>` | LLM free-form query |
| `/schedule` | View/modify report schedule |
| `/cancel` | Cancel pending action |

## Credential Security

- Credit card passwords: encrypted at rest in `importer/config.enc` with `age`
- Bank Leumi password: never written to disk, entered at runtime only
- `~/.finance-key`: the age decryption key — back this up securely, don't commit it

## 2FA / OTP

MoneyMan sends OTP requests via Telegram. When prompted, reply with the SMS code you received. Long-term tokens are enabled by default for banks that support them.

## Pitfalls

- MoneyMan's config format may differ from `config.json.example` — verify against MoneyMan README after cloning
- `run-import.sh` assumes MoneyMan's entry point at `node_modules/moneyman/dist/index.js` — adjust if different
- actualpy queries are run inside a `with Actual(...)` context — each command opens a fresh connection to download the budget file
- Credit card amounts in ActualBudget are negative (expense); bank checking is positive (asset). The `_ils()` helper handles sign display.
