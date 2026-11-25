# InstaScope — Claude guide

Our own Instagram profile scraper (we don't wrap Apify): FastAPI + async httpx + SQLAlchemy 2.0.
Input is a handle; output is bio, profile picture, counts, bio links, business info, posts and
reels. Every live scrape is persisted.

## Commands
- `make dev`: run the API on :8000 (`/docs` for Swagger)
- `make check`: ruff + mypy + pytest (must pass before finishing any change)
- `make test` / `make fmt` / `make typecheck`
- Tests are offline: `respx` mocks all Instagram/CDN traffic. Never hit real Instagram in tests.

## Architecture (request flow)
`api/v1/profiles.py` → `services/profile_service.py` (cache → live scrape → media → persist)
→ `scraper/client.py` (HTTP, retries, rotation) → `scraper/parsers.py` (raw JSON → models)
→ `db/repository.py` (all DB I/O) → `db/models.py`.

| Path | Role |
|---|---|
| `app/core/config.py` | `Settings` (pydantic-settings, `.env`; comma-separated values become lists) |
| `app/core/errors.py` | `ScraperError` hierarchy, each mapped to an HTTP status and `{"error", "detail"}` |
| `app/core/utils.py` | `normalize_username`, `utcnow`, `as_utc`, logging |
| `app/scraper/endpoints.py` | **All Instagram URLs**: change here first when Instagram breaks |
| `app/scraper/client.py` | Headers/cookies, proxy+session rotation, throttle, backoff, status→error mapping, `download()` |
| `app/scraper/parsers.py` | Two media shapes: GraphQL `node` (`parse_graph_post`) and v1 `media` (`parse_v1_media`) |
| `app/services/profile_service.py` | Orchestration, cache freshness per section, pagination, fallbacks, warnings |
| `app/services/media_store.py` | Saves `profile.jpg` and `{shortcode}.jpg` to `storage/media/{username}/`, served at `/media` |
| `app/services/job_service.py` | Batch jobs (DB-persisted, BackgroundTasks, marked `interrupted` on restart) |
| `app/db/models.py` | `profiles`, `profile_snapshots` (history), `media`, `scrape_jobs` |
| `app/schemas/` | Public response models (`ScrapeResult`, `MediaData`, `JobRead`, …) |
| `tests/fixtures/*.json` | Sample Instagram payloads; keep them matching real response formats |

## Instagram strategy
- Profile: `GET /api/v1/users/web_profile_info/?username=` with `X-IG-App-ID`. Logged-out OK, returns the first 12 grid posts.
- More posts: `GET /api/v1/feed/user/{id}/` needs `IG_SESSION_IDS` (`auth=True`).
- Reels: `POST /api/v1/clips/user/`; on failure, fall back to reels in the grid and add a warning.
- A logged-out call refused with `LoginRequired`/`Blocked` is retried once with a session, if one is configured.
- An HTML 404 on an API URL means a block (not "user missing"); a JSON 404 means the profile is not found.
- Status: as of 2026-11-25 these endpoints are **not verified against live Instagram** (sandbox couldn't reach it). First live run: check them and update fixtures if formats differ.

## Conventions
- Python 3.11+, fully typed, async everywhere; ruff (format owns line length) + mypy clean.
- DB rows = queryable columns + full model in `data` JSON + original payload in `raw` JSON.
- No Alembic yet: `init_db` runs `create_all`. A column change needs a migration plan, or deleting `storage/instascope.db` in dev.
- Errors: raise a `ScraperError` subclass; never return Instagram's raw error text as a 500.
- Partial data is fine: append to `warnings` instead of failing the whole scrape.
- Every new behaviour needs a test in `tests/test_api.py` or `tests/test_parsers.py`.

## Keep Claude context in sync (required)
After **every** change that adds or alters behaviour, endpoints, settings or structure:
1. Update this file (architecture table, strategy, conventions).
2. Update the affected skill(s) in `.claude/skills/`, or add one for a new repeatable workflow.
3. Update project memory (`~/.claude/projects/-Users-muhammad-hamza-Desktop-untitled-folder/memory/`).
4. Update `README.md` and `.env.example` when user-facing.

The `sync-docs` skill has the checklist. The Stop hook `.claude/hooks/docs_sync_check.py` reminds
you once whenever code is newer than these docs.

## Skills
- `run-api`: start the server and smoke-test it
- `update-ig-endpoints`: fix the scraper when Instagram changes its API or response formats
- `add-endpoint`: add a route the house way (schema → service → route → test → docs)
- `sync-docs`: the after-change documentation checklist
