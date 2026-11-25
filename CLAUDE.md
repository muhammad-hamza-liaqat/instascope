# InstaScope — Claude guide

Our own Instagram profile scraper (we don't wrap Apify): FastAPI + async httpx + SQLAlchemy 2.0.
Input is a handle; output is bio, profile picture, counts, bio links, business info, posts, reels
and follower history. Every live scrape is persisted.

**Single-endpoint rule (user decision, 2026-11-25):** all data comes from ONE route,
`GET /api/v1/profiles/{username}`, which returns everything in one `ScrapeResult`. Don't add
separate posts/reels/history/list/jobs endpoints; add fields or query params to this one instead.
(The removed routes are in git history at commit `68f8cec` if ever needed.)

## Commands
- `make dev`: run the API on :8000 (`/docs` for Swagger)
- `make check`: ruff + mypy + pytest (must pass before finishing any change)
- `make test` / `make fmt` / `make typecheck`
- Tests are offline: `respx` mocks all Instagram/CDN traffic. Never hit real Instagram in tests.

## Architecture (request flow)
`api/v1/profiles.py` (the one route) → `services/profile_service.py` (cache → live scrape → media → persist → attach history)
→ `scraper/client.py` (HTTP, retries, rotation) → `scraper/parsers.py` (raw JSON → models)
→ `db/repository.py` (all DB I/O) → `db/models.py`.

| Path | Role |
|---|---|
| `app/core/config.py` | `Settings` (pydantic-settings, `.env`; comma-separated values become lists) |
| `app/core/errors.py` | `ScraperError` hierarchy, each mapped to an HTTP status and `{"error", "detail"}` |
| `app/core/utils.py` | `normalize_username`, `utcnow`, `as_utc`, logging |
| `app/scraper/endpoints.py` | **All Instagram URLs**: change here first when Instagram breaks |
| `app/scraper/client.py` | Headers/cookies, borrows accounts from the pool (`acquire`/`release`), proxy routing, throttle, backoff, status→error mapping, `RollingStats`, `download()` |
| `app/scraper/accounts.py` | **Self-managing `AccountPool`**: config/DB sync (accounts file hot-reload), LRU pick + hourly budget, cooldown escalation, auto re-login, checkpoint handling, pool alerts |
| `app/scraper/login.py` | `web_login` (bootstrap csrf + encryption key, then login form) and `encrypt_password` (browser `#PWD_INSTAGRAM_BROWSER:10`) |
| `app/scraper/proxies.py` | `ProxyRouter`: rotating gateway (no keep-alive = new IP each call), sticky per-account template, or a list with failure resting |
| `app/core/crypto.py` | `SecretBox`: Fernet encryption of stored sessions, keyed password fingerprints, auto `storage/.secret_key` |
| `app/core/alerts.py` | `Alerter`: log + Slack/Discord webhook, de-duplicated per key |
| `app/services/supervisor.py` | Background loop (every `MAINTENANCE_INTERVAL_SECONDS`): `pool.maintain()` + scrape-failure alert |
| `app/scraper/parsers.py` | Two media shapes: GraphQL `node` (`parse_graph_post`) and v1 `media` (`parse_v1_media`) |
| `app/services/profile_service.py` | Orchestration, cache freshness per section, pagination, fallbacks, warnings |
| `app/services/media_store.py` | Saves `profile.jpg` and `{shortcode}.jpg` to `storage/media/{username}/`, served at `/media` |
| `app/db/models.py` | `profiles`, `profile_snapshots` (history), `media`, `ig_accounts` (pool state, encrypted sessions, no passwords). An old dev DB may still have an unused `scrape_jobs` table |
| `app/schemas/instagram.py` | Public response models (`ScrapeResult` with `profile`/`posts`/`reels`/`history`/`warnings`, `MediaData`, …) |
| `tests/fixtures/*.json` | Sample Instagram payloads; keep them matching real response formats |

## Instagram strategy
- Profile: `GET /api/v1/users/web_profile_info/?username=` with `X-IG-App-ID`. Logged-out OK, returns the first 12 grid posts.
- More posts: `GET /api/v1/feed/user/{id}/` needs a pool account (`auth=True`).
- Reels: `POST /api/v1/clips/user/`; on failure, fall back to reels in the grid and add a warning.
- A logged-out call refused with `LoginRequired`/`Blocked` is repeated with a pool account (if the pool `can_authenticate`).
- Account-level failures during a logged-in call (`RateLimited`, `LoginRequired`, `Blocked`) are retried immediately with a different account; the pool updates the failing one (cooling / needs_login / checkpoint).
- Login: bootstrap from `GET /data/shared_data/`, falling back to `GET /accounts/login/` (live 2026-11-25: shared_data gives no csrf, the login page works). Read csrf + `encryption.key_id/public_key/version` from the JSON, the cookie, the `ig-set-password-encryption-web-*` headers or the embedded HTML JSON. Login was verified live up to `checkpoint_required` → `POST /api/v1/web/accounts/login/ajax/`. Checkpoints and 2FA are **never bypassed**: flag, alert, re-try every `ACCOUNT_CHECKPOINT_RETRY_SECONDS`.
- An HTML 404 on an API URL means a block (not "user missing"); a JSON 404 means the profile is not found.
- **Live finding 2026-11-25:** logged-out `web_profile_info` returned an **HTML 404** from the user's Mac (home connection) and from the sandbox, so logged-out access is gated. Confirmed with `make diagnose` through 3 US proxies: all logged-out API variants return HTML 404 and the profile page 302-redirects to login. Pool accounts are **required**; the logged-in path (and login itself) is still unverified.
- Status: as of 2026-11-25 these endpoints, **including login**, are **not verified against live Instagram** (sandbox couldn't reach it). First live run: check them and update fixtures if formats differ.

## Conventions
- Python 3.11+, fully typed, async everywhere; ruff (format owns line length) + mypy clean.
- DB rows = queryable columns + full model in `data` JSON + original payload in `raw` JSON.
- No Alembic yet: `init_db` runs `create_all`. A column change needs a migration plan, or deleting `storage/instascope.db` in dev.
- Errors: raise a `ScraperError` subclass; never return Instagram's raw error text as a 500.
- Partial data is fine: append to `warnings` instead of failing the whole scrape.
- Every new behaviour needs a test in `tests/test_api.py`, `tests/test_parsers.py` or `tests/test_self_managing.py` (pool/login/proxy/alerts; `mock_login`/`login_ok` helpers).
- Self-management principle (user decision, 2026-11-25: "everything handled by itself, it's a product"): no manual steps in normal operation. New failure modes get automatic handling, a `/health/ready` signal and an alert, not a docs note telling a human what to do.
- Secrets: passwords only in config (env / accounts file, git-ignored); sessions only encrypted in the DB; never log them. `/health/ready` details only with a valid API key.
- Settings: every `Settings` field must appear in `.env.example` (check in `sync-docs`).

## Keep Claude context in sync (required)
After **every** change that adds or alters behaviour, endpoints, settings or structure:
1. Update this file (architecture table, strategy, conventions).
2. Update the affected skill(s) in `.claude/skills/`, or add one for a new repeatable workflow.
3. Update project memory (`~/.claude/projects/-Users-muhammad-hamza-Desktop-untitled-folder/memory/`), then run `make memory-sync` so the repo copy in `.claude/memory/` matches (user rule, 2026-11-25).
4. Update the matching `docs/0N-*.md` section (simple beginner words), `README.md` (short index, author: Hamza) and `.env.example` when user-facing.

The `sync-docs` skill has the checklist. The Stop hook `.claude/hooks/docs_sync_check.py` reminds
you once whenever code is newer than these docs.

## Project memory (in the repo)
`.claude/memory/` mirrors Claude's memory for this project (decisions, user preferences, open issues).
Read `.claude/memory/MEMORY.md` at the start of a session, especially on a new machine. Never put secrets there.
Standing user rule: chat replies short, in simple beginner-friendly words.

## Skills
- `run-api`: start the server and smoke-test it
- `manage-accounts`: operate the account pool and proxies (add/fix accounts, read the status page, act on alerts)
- `update-ig-endpoints`: fix the scraper when Instagram changes its API or response formats
- `add-endpoint`: add a field or parameter to the single endpoint (schema → service → route → test → docs)
- `sync-docs`: the after-change documentation checklist
