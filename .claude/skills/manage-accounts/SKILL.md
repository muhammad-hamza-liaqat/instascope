---
name: manage-accounts
description: Operate InstaScope's self-managing Instagram account pool and proxies. Use for adding, fixing or removing scraping accounts, reading /health/ready, reacting to alerts (checkpoint, bad password, pool low, scraping failing), or configuring proxy gateways.
---

# Operate the account pool

Everything is automatic once configured. A person is only needed for the cases in step 3.

## 1. Add accounts
- Use spare accounts only, with 2FA OFF. Never the user's personal account.
- Many accounts: put them in a git-ignored file, set `IG_ACCOUNTS_FILE=accounts.txt`, one
  `username:password` per line. The file is **hot-reloaded**: no restart needed.
- A few accounts: `IG_ACCOUNTS=user1:pw1,user2:pw2` in `.env` (needs a restart).
- `IG_SESSION_IDS` = session-only accounts. They can't re-login, so prefer username:password.
- Don't print, log or commit passwords or session values.

## 2. Check status
`curl -s -H "X-API-Key: $KEY" localhost:8000/health/ready | python3 -m json.tool`
- `status: degraded` = accounts configured but none usable right now.
- `accounts.by_status` statuses: `active`, `cooling` (rate limited, resting), `needs_login`
  (logs in by itself), `checkpoint`, `bad_credentials`, `disabled` (removed from config).
- `scraping.failure_ratio` / `last_error`: rolling health of recent Instagram calls.
- `recent_alerts`: the last 20 alerts.

## 3. Act on alerts
| Alert | Action |
|---|---|
| `checkpoint:<user>` | The user opens Instagram on that account and approves the login ("This was me") or completes the challenge, and turns off 2FA. Retried automatically every `ACCOUNT_CHECKPOINT_RETRY_SECONDS`. |
| `bad_credentials:<user>` | Fix the password in the accounts file / `IG_ACCOUNTS`. Retried as soon as it changes. |
| `session_expired:<user>` | Session-only account: add it with its password instead. |
| `pool_low` | Add accounts, or wait for cooldowns (see `cooldown_until`). Consider a higher `ACCOUNT_COOLDOWN_SECONDS` or a lower `ACCOUNT_MAX_REQUESTS_PER_HOUR`. |
| `scrape_failing` | Many calls failing: check proxies first, then follow the `update-ig-endpoints` skill (Instagram may have changed). |

## 4. Proxies
- Preferred: a residential **rotating gateway** in `PROXY_URL`, plus `PROXY_STICKY_URL` with
  `{session}` in the provider's sticky-session username format, so each account keeps one IP.
- Plain list `PROXIES=...`: failing proxies are rested for 10 minutes automatically.

## 5. Tuning (in `.env`)
`ACCOUNT_MAX_REQUESTS_PER_HOUR` (120), `ACCOUNT_COOLDOWN_SECONDS` (600, doubles per strike up to
`ACCOUNT_COOLDOWN_MAX_SECONDS`), `MIN_REQUEST_INTERVAL` (1.5s global), `ALERT_MIN_HEALTHY_ACCOUNTS`.
Code lives in `app/scraper/accounts.py`, `login.py`, `proxies.py` and `app/services/supervisor.py`.
