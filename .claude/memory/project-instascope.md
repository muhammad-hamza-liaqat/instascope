---
name: project-instascope
description: "InstaScope — the user's own FastAPI Instagram profile scraper (Apify alternative); status, key decisions, open issues"
metadata:
  node_type: memory
  type: project
  originSessionId: 694fd0d0-988a-4d6c-835e-66cca3c67fe6
  modified: 2026-11-25T10:47:48.610Z
---

InstaScope (in `~/Desktop/untitled folder`) is the user's own Instagram scraper API: handle in → bio, profile pic, followers/following, posts, reels out. Built 2026-11-25. The user explicitly wants **our own tool**, not an Apify wrapper.

Repo: https://github.com/muhammad-hamza-liaqat/instascope (branch `main`, first pushed 2026-11-25).

Stack: FastAPI, async httpx (HTTP/2), SQLAlchemy 2.0 async + SQLite (Postgres optional), uv, ruff/mypy/pytest+respx. 40 offline tests passing (after self-management build, 2026-11-25). Deps also: cryptography, pynacl.

Key decisions:
- **Single data endpoint** (user request 2026-11-25): only `GET /api/v1/profiles/{username}`, returning profile + posts + reels + follower history + warnings in one response. Separate posts/reels/history/list/delete/batch-jobs routes were removed (recoverable from commit `68f8cec`). Extend via fields/query params, not new routes.
- Every live scrape persisted (parsed `data` + original `raw` JSON + follower snapshots + local images).
- **Product goal / self-management** (user, 2026-11-25: "all things handled by itself, we want to make this a product"). The user's choices: **internal tool first** (no customer accounts or billing yet), **username+password account pool** with auto-login, **rotating residential proxy gateway**. Built: AccountPool (auto login, encrypted sessions, hourly budget, cooldown escalation, re-login, checkpoint flagging, accounts-file hot-reload), ProxyRouter (gateway / sticky per-account / list), Supervisor loop, Slack/Discord alerts, `/health/ready` status page. The user asked me to pick features ("recommend by yourself").
- Deferred (offer later): admin dashboard UI, scheduled monitoring of tracked profiles, async requests + webhooks, customer API keys/quotas/billing, Alembic, Postgres+Redis deployment.
- Hard line: no automatic account creation and no bypassing of checkpoints/2FA/captchas. Those go to a human via alerts.
- Logged-out first, escalate to a pool account when refused; warnings instead of failing.
- No Alembic yet (create_all).

**Live result 2026-11-25:** the user's first real request (natgeo, logged-out, home Mac) got `blocked`: `web_profile_info` answered with an HTML 404. Logged-out access is gated, so accounts (IG_ACCOUNTS) are needed. Next: the user adds a spare account and tests whether login and the logged-in profile call work.

**Open issue:** Instagram endpoints (web_profile_info, feed/user, clips/user) and the **login flow** (shared_data + login/ajax, #PWD_INSTAGRAM_BROWSER:10 encryption) were NOT verified live. The auto-mode classifier blocked probing Instagram from the sandbox on 2026-11-25. One early probe of i.instagram.com returned an HTML 404 (likely a logged-out block). The first live run needs the user's go-ahead, or for them to run it.

**Open issue:** `.claude/settings.json` (Stop hook + permission allowlist) was blocked by the classifier; the user needs to add it themselves. Hook script: `.claude/hooks/docs_sync_check.py`.

**How to apply:** Read CLAUDE.md first; follow [[feedback-keep-claude-docs-synced]] after every change.
