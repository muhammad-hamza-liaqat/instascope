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

**Proxies (2026-11-25):** the user bought 3 static US proxies (ip:port:user:pass format), stored in `.env` as `PROXIES` (list mode). All 3 tested OK via ipinfo (US). Credentials live only in `.env`; never copy them into memory or docs. The app now accepts the `ip:port:user:pass` format directly.

**Still blocked via proxies (2026-11-25):** the logged-out request was still an HTML 404 through the 3 US proxies, so the cause is probably the endpoint/request itself, not the IP. My own Instagram probes are denied by the auto-mode classifier, so I added `scripts/diagnose_instagram.py` (`make diagnose`) for the USER to run. It tries 5 request styles and prints status codes. First run (2026-11-25, via proxy): A/B/C API = HTML 404, but they accidentally tested `@muhammad-hamza` because the Makefile used `USER`, the macOS env var; renamed to `HANDLE`. D profile page = 302 to /accounts/login/; E login page = 200. Conclusion: Instagram forces login for logged-out visitors, and the login page is reachable, so spare accounts are the way forward. Rerun with natgeo (2026-11-25) gave the SAME result: every logged-out route is refused through the US proxy (API = HTML 404, profile page = 302 to login), so logged-out scraping is dead from these IPs. **Accounts are required.** Next: the user adds a spare account; that will be the first live test of the login flow.

**Login verified live (2026-11-25):** `/data/shared_data/` no longer gives a csrftoken, so bootstrap now falls back to `/accounts/login/` (cookie, headers or embedded JSON). With that, the real login reached Instagram and returned `checkpoint_required`, so the credentials and encryption format were accepted. The user must approve the login in the Instagram app. A server restart now retries needs_login/checkpoint accounts immediately.

**Open issue:** Instagram endpoints (web_profile_info, feed/user, clips/user) and the **login flow** (shared_data + login/ajax, #PWD_INSTAGRAM_BROWSER:10 encryption) were NOT verified live. The auto-mode classifier blocked probing Instagram from the sandbox on 2026-11-25. One early probe of i.instagram.com returned an HTML 404 (likely a logged-out block). The first live run needs the user's go-ahead, or for them to run it.

**Open issue:** `.claude/settings.json` (Stop hook + permission allowlist) was blocked by the classifier; the user needs to add it themselves. Hook script: `.claude/hooks/docs_sync_check.py`.

**How to apply:** Read CLAUDE.md first; follow [[feedback-keep-claude-docs-synced]] after every change.
