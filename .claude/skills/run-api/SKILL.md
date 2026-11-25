---
name: run-api
description: Start the InstaScope FastAPI server locally (or via Docker) and smoke-test it. Use when asked to run, start, try or demo the API, or to confirm a change works in the running app.
---

# Run InstaScope

1. Install deps if `.venv` is missing: `uv sync`
2. Start: `uv run uvicorn app.main:app --reload --port 8000` (run in background)
3. Wait for `/health`, then smoke-test without touching Instagram:
   ```bash
   curl -s localhost:8000/health
   curl -s localhost:8000/health/ready          # DB ok + sessions/proxies configured
   curl -s localhost:8000/openapi.json | python3 -c "import json,sys;print(*sorted(json.load(sys.stdin)['paths']),sep='\n')"
   curl -s "localhost:8000/api/v1/profiles/bad%20name"   # expect 422 invalid_username
   ```
4. A **live** scrape (`/api/v1/profiles/<handle>`) calls real Instagram. Run it only when the
   user asks, with their chosen handle.
5. Stop the server when done: `pkill -f "uvicorn app.main:app"`

Docker: `docker compose up --build` (needs `.env`; data is mounted at `./storage`).

Notes:
- If `API_KEYS` is set, send `-H "X-API-Key: <key>"` on `/api/v1/*`.
- Stored data: `storage/instascope.db` (SQLite) and `storage/media/<username>/`.
