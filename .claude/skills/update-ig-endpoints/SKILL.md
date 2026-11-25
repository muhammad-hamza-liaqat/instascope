---
name: update-ig-endpoints
description: Diagnose and fix the scraper when Instagram changes endpoints, headers or JSON formats (missing fields, new errors, blocks, 404s on real users). Use when live scrapes fail or return empty or wrong data.
---

# Fix scraping after an Instagram change

## 1. Classify the failure (look at the `error` code / warnings)
| Symptom | Likely cause | Where |
|---|---|---|
| `blocked` with HTML 404 / login wall | IP or logged-out access refused | add `PROXIES` / `IG_SESSION_IDS`; `client.py::_parse_response` |
| `login_required` | endpoint now needs auth | set `auth=True` for that call, document it |
| `rate_limited` | too fast | raise `MIN_REQUEST_INTERVAL`, add proxies |
| 200 but fields null or zero | JSON format changed | `parsers.py` |
| `profile_not_found` for a real user | endpoint URL/params changed | `endpoints.py` |
| reels warning "Reels tab unavailable" | clips endpoint changed | `endpoints.CLIPS_USER`, `client.fetch_user_clips` |

## 2. Capture a real payload (only with the user's OK, against a handle they choose)
Run the server and enable debug logging (`LOG_LEVEL=DEBUG`), or read the stored original JSON:
`sqlite3 storage/instascope.db "select raw from profiles where username='X'"`.
Remove personal and session data, then save it as a new fixture in `tests/fixtures/`.

## 3. Fix in this order
1. `app/scraper/endpoints.py`: URLs.
2. `app/scraper/client.py`: headers (`X-IG-App-ID`, `X-ASBD-ID`, CSRF), status → error mapping.
   Header values that change often live in settings (`IG_APP_ID`, `IG_ASBD_ID`), so they can be changed without a deploy.
3. `app/scraper/parsers.py`: use `_dig`/`_first` so a missing key gives `None`, not a crash.
   `_first` keeps real zeros; never use `a or b` for counts.
4. Update fixtures and add or adjust tests; `make check` must pass.

## 4. Record it
Update the "Instagram strategy" section in `CLAUDE.md` (with the date) and project memory
(what changed, when, how it was fixed).
