# InstaScope

A self-hosted Instagram profile scraper API built on FastAPI. Give it a handle and it returns the
**bio, profile picture, follower/following/post counts, bio links, business info, posts and
reels** (with likes, comments, views, plays, audio, hashtags and mentions).

Unlike a pay-per-run actor, every live scrape is **stored**: profile data, Instagram's
original JSON, follower history, and local copies of profile pictures and covers (Instagram's
CDN links expire).

## Quick start

```bash
cp .env.example .env      # optional: add IG_SESSION_IDS / PROXIES / API_KEYS
make install              # uv sync
make dev                  # http://localhost:8000/docs
```

Docker: `make docker`. Data is kept in `./storage`.

## API

| Method | Path | What |
|---|---|---|
| GET | `/api/v1/profiles/{username}?posts_limit=12&reels_limit=12&refresh=false` | Full profile, posts and reels |
| GET | `/api/v1/profiles/{username}/posts?limit=` | Posts only |
| GET | `/api/v1/profiles/{username}/reels?limit=` | Reels only (play counts, audio) |
| GET | `/api/v1/profiles/{username}/history` | Follower history from stored snapshots |
| GET | `/api/v1/profiles` | Stored profiles (paginated) |
| DELETE | `/api/v1/profiles/{username}` | Delete stored data and media |
| POST | `/api/v1/jobs` | Batch scrape `{"usernames": [...]}` in the background |
| GET | `/api/v1/jobs/{id}` | Job progress and results |
| GET | `/health`, `/health/ready` | Probes |
| GET | `/media/{username}/...` | Locally saved images |

`{username}` accepts `natgeo`, `@natgeo` or a profile URL (the URL form works in batch jobs).
Responses report `source: "live" | "cache"` and include `warnings` (for example "only 12 posts
available logged-out").

```bash
curl localhost:8000/api/v1/profiles/natgeo | jq '.profile | {username, followers, biography}'
```

## What you get with and without a session

| Data | Logged-out | With `IG_SESSION_IDS` |
|---|---|---|
| Profile fields, counts, picture, bio links | yes | yes |
| Posts | first 12 | paginated up to `posts_limit` |
| Reels tab | best effort (falls back to reels found in the grid) | yes |
| Private profiles | profile fields only | profile fields only |

For volume, use **residential proxies** (`PROXIES`) and several throwaway sessions. Requests are
throttled (`MIN_REQUEST_INTERVAL`), retried with backoff, and rotated across proxies and sessions.
When Instagram refuses a logged-out call, the scraper retries it once with a session.

## Development

```bash
make check   # ruff + mypy + pytest
```

Tests mock every Instagram call (`respx`), so they run offline. Fixtures in `tests/fixtures/`
mirror Instagram's response formats.

> Scraping Instagram is against Meta's Terms of Use. Collect only public data, respect rate
> limits, and comply with privacy law (GDPR/CCPA) for what you store.
