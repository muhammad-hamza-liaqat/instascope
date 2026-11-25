# InstaScope

A self-hosted Instagram profile scraper API built on FastAPI. Give it a handle and it returns the
**bio, profile picture, follower/following/post counts, bio links, business info, posts and
reels** (with likes, comments, views, plays, audio, hashtags and mentions).

Unlike a pay-per-run actor, every live scrape is **stored**: profile data, Instagram's
original JSON, follower history, and local copies of profile pictures and covers (Instagram's
CDN links expire).

## Quick start

```bash
cp .env.example .env      # optional: add IG_ACCOUNTS / PROXY_URL / API_KEYS
make install              # uv sync
make dev                  # http://localhost:8000/docs
```

Docker: `make docker`. Data is kept in `./storage`.

## API

There is **one data endpoint**, and it returns everything in a single response:

```
GET /api/v1/profiles/{username}?posts_limit=12&reels_limit=12&refresh=false
```

| Field | Contents |
|---|---|
| `profile` | bio, full name, profile picture (`profile_pic_url_hd` and local `profile_pic_local`), followers, following, posts count, bio links, verified/business/private flags, category, contact info |
| `posts` | grid posts: caption, hashtags, mentions, likes, comments, views, date, location, images/videos, carousel items |
| `reels` | reels: plays, views, likes, comments, duration, audio, video URL |
| `history` | follower/following/posts snapshots from earlier scrapes (oldest first) |
| `source` | `live` (just scraped) or `cache` (served from the database) |
| `warnings` | partial-data notes, e.g. "only 12 posts available logged-out" |

`{username}` accepts `natgeo` or `@natgeo`. Set `posts_limit=0` or `reels_limit=0` to skip
a section. Other routes: `/health`, `/health/ready`, and `/media/{username}/...` for saved images.

```bash
curl localhost:8000/api/v1/profiles/natgeo | jq '.profile | {username, followers, biography}'
```

## It runs itself

Configure it once; the service handles the rest.

| You provide (once) | The system does automatically |
|---|---|
| Spare Instagram accounts as `username:password` (`IG_ACCOUNTS` or `IG_ACCOUNTS_FILE`) | Logs them in, stores sessions encrypted, spreads load (hourly budget per account), rests rate-limited accounts with growing cooldowns, logs in again when a session expires, re-reads the accounts file when it changes |
| A rotating residential proxy gateway (`PROXY_URL`, optional `PROXY_STICKY_URL`) | New IP for every logged-out request; each account keeps its own stable IP (accounts that hop IPs get flagged) |
| A Slack/Discord webhook (`ALERT_WEBHOOK_URL`) | Alerts when an account hits a checkpoint or wrong password, the pool runs low, or scraping starts failing (Instagram changed something) |

Things that need a person: approving a login when Instagram flags an account (open the
Instagram app and tap "This was me"), and turning off 2FA on scraping accounts. The account
is then picked up again automatically (re-tried every 6h). Wrong passwords are re-tried as soon
as you fix them in the config.

**Status page:** `GET /health/ready` shows how many accounts are usable, each account's state,
proxy mode, the recent Instagram success rate, and recent alerts. Account details are shown only
with a valid `X-API-Key` when `API_KEYS` is set.

### What accounts unlock

| Data | No accounts | With accounts |
|---|---|---|
| Profile fields, counts, picture, bio links | yes | yes |
| Posts | first 12 | paginated up to `posts_limit` |
| Reels tab | best effort (falls back to reels in the grid) | yes |
| Private profiles | profile fields only | profile fields only |

When Instagram refuses a logged-out request, the scraper automatically repeats it with an
account. If no account is usable, you still get the logged-out data, plus a warning.

## Development

```bash
make check   # ruff + mypy + pytest
```

Tests mock every Instagram call (`respx`), so they run offline. Fixtures in `tests/fixtures/`
mirror Instagram's response formats.

> Scraping Instagram is against Meta's Terms of Use. Collect only public data, respect rate
> limits, and comply with privacy law (GDPR/CCPA) for what you store.
