"""Low-level async Instagram HTTP client.

Responsibilities: headers/cookies, proxy routing, borrowing accounts from the pool,
global throttling, retries with exponential backoff, mapping Instagram responses to domain
errors, and rolling success stats. It returns raw JSON; parsing lives in `parsers.py`.
"""

import asyncio
import json
import logging
import random
import re
import string
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import (
    Blocked,
    LoginRequired,
    NotServed,
    ProfileNotFound,
    RateLimited,
    ScraperError,
    UpstreamError,
)
from app.scraper import endpoints as ep
from app.scraper.accounts import AccountPool, Lease, session_user_id
from app.scraper.proxies import ProxyRouter
from app.scraper.web import WebTokens, jazoest, loads_first, parse_tokens

log = logging.getLogger(__name__)

_LOGIN_MARKERS = ("login_required", "require_login", "LoginAndSignupPage")
_BLOCK_MARKERS = ("checkpoint_required", "challenge_required", "feedback_required")
_REDIRECTS = {301, 302, 303, 307, 308}


class RollingStats:
    """Outcome of the most recent Instagram calls, used for health and alerts."""

    def __init__(self, size: int = 100) -> None:
        self._events: deque[tuple[bool, str | None]] = deque(maxlen=size)
        self.last_error: str | None = None

    def record(self, ok: bool, error: ScraperError | None = None) -> None:
        self._events.append((ok, error.code if error else None))
        if error is not None and not ok:
            self.last_error = f"{error.code}: {error.message}"[:300]

    def snapshot(self) -> dict[str, Any]:
        total = len(self._events)
        failures = sum(1 for ok, _ in self._events if not ok)
        return {
            "recent_calls": total,
            "recent_failures": failures,
            "failure_ratio": round(failures / total, 3) if total else 0.0,
            "last_error": self.last_error,
        }


class InstagramClient:
    def __init__(
        self,
        settings: Settings,
        *,
        pool: AccountPool | None = None,
        proxies: ProxyRouter | None = None,
    ) -> None:
        self._s = settings
        self._pool = pool
        self._proxies = proxies or ProxyRouter(settings)
        self._clients: dict[tuple[str | None, bool], httpx.AsyncClient] = {}
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._throttle_lock = asyncio.Lock()
        self._next_allowed = 0.0
        self.stats = RollingStats()

    # ------------------------------------------------------------------ setup
    def _http(self, proxy: str | None) -> httpx.AsyncClient:
        """One pooled client per proxy. Rotating gateways get no keep-alive (new IP per call)."""
        keepalive = not self._proxies.rotates(proxy)
        key = (proxy, keepalive)
        client = self._clients.get(key)
        if client is None:
            client = httpx.AsyncClient(
                http2=True,
                proxy=proxy,
                timeout=self._s.request_timeout,
                follow_redirects=False,
                limits=httpx.Limits(
                    max_connections=20, max_keepalive_connections=10 if keepalive else 0
                ),
            )
            self._clients[key] = client
        return client

    @property
    def can_authenticate(self) -> bool:
        return self._pool is not None and self._pool.can_authenticate

    async def aclose(self) -> None:
        await asyncio.gather(*(c.aclose() for c in self._clients.values()))

    def _headers(
        self, referer: str, lease: Lease | None, csrf: str | None = None
    ) -> dict[str, str]:
        csrf = csrf or "".join(random.choices(string.ascii_letters + string.digits, k=32))
        cookies = {"csrftoken": csrf, "ig_nrcb": "1"}
        session_id = lease.account.session_id if lease is not None else None
        if lease is not None and session_id:
            cookies["sessionid"] = session_id
            user_id = lease.account.user_id or session_user_id(session_id)
            if user_id:
                cookies["ds_user_id"] = user_id
        return {
            "User-Agent": self._s.user_agent,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "X-IG-App-ID": self._s.ig_app_id,
            "X-ASBD-ID": self._s.ig_asbd_id,
            "X-CSRFToken": csrf,
            "X-Requested-With": "XMLHttpRequest",
            "Origin": ep.BASE_URL,
            "Referer": referer,
            "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
        }

    async def _throttle(self) -> None:
        async with self._throttle_lock:
            wait = self._next_allowed - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_allowed = time.monotonic() + self._s.min_request_interval

    # --------------------------------------------------------------- requests
    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        referer: str = ep.BASE_URL + "/",
        auth: bool = False,
    ) -> dict[str, Any]:
        """Call an Instagram JSON endpoint with retries.

        `auth=True` requires a logged-in session. Otherwise the call goes out logged-out
        first and escalates to a session (if configured) when Instagram refuses.
        """
        if auth and not self.can_authenticate:
            raise LoginRequired(
                "This data needs a logged-in Instagram account. Add accounts to IG_ACCOUNTS."
            )

        use_session = auth or (self._s.always_use_session and self.can_authenticate)
        attempts = self._s.max_retries + 1
        error: ScraperError | None = None

        for attempt in range(attempts):
            lease: Lease | None = None
            proxy: str | None = None
            error = None
            try:
                if use_session:
                    assert self._pool is not None
                    lease = await self._pool.acquire()  # NoAccountAvailable if none usable
                    proxy = lease.proxy
                else:
                    proxy = self._proxies.anonymous()
                async with self._semaphore:
                    await self._throttle()
                    resp = await self._http(proxy).request(
                        method,
                        url,
                        params=params,
                        data=data,
                        headers=self._headers(referer, lease),
                    )
                self._proxies.report(proxy, ok=True)
                payload = self._parse_response(resp)
                self.stats.record(True)
                return payload
            except httpx.TransportError as exc:
                self._proxies.report(proxy, ok=False)
                error = UpstreamError(f"Network error talking to Instagram: {exc!r}")
            except ScraperError as exc:
                error = exc
            finally:
                if lease is not None:
                    assert self._pool is not None
                    await self._pool.release(lease, error)

            self.stats.record(isinstance(error, ProfileNotFound), error)
            if isinstance(error, (LoginRequired, Blocked, NotServed)) and not use_session:
                if self.can_authenticate:
                    log.info("Instagram refused logged-out call to %s; using an account", url)
                    use_session = True
                    continue
                raise error
            # The account was the problem: retry right away with another one.
            account_issue = lease is not None and isinstance(
                error, (LoginRequired, Blocked, RateLimited)
            )
            if not (error.retryable or account_issue):
                raise error
            if attempt < attempts - 1 and not account_issue:
                base = self._s.retry_backoff_base
                delay = base * (2**attempt) + random.uniform(0, base)
                log.warning(
                    "Retry %d/%d for %s %s in %.1fs: %s",
                    attempt + 1,
                    attempts - 1,
                    method,
                    url,
                    delay,
                    error,
                )
                await asyncio.sleep(delay)

        assert error is not None
        raise error

    @staticmethod
    def _parse_response(resp: httpx.Response) -> dict[str, Any]:
        status = resp.status_code
        is_json = "json" in resp.headers.get("content-type", "")

        if status in _REDIRECTS:
            location = resp.headers.get("location", "")
            if "login" in location or "challenge" in location:
                raise LoginRequired(f"Instagram redirected to {location}", status=status)
            raise UpstreamError(f"Unexpected redirect to {location}", status=status)
        if status == 429:
            raise RateLimited(status=429)

        payload: Any = None
        if is_json:
            try:
                payload = resp.json()
            except ValueError:
                payload = None
        message = str(payload.get("message") or "") if isinstance(payload, dict) else ""
        blob = message if is_json else resp.text[:5000]

        if "wait a few minutes" in message.lower():
            raise RateLimited(message, status=status)
        if any(marker in blob for marker in _BLOCK_MARKERS):
            raise Blocked(f"Instagram challenge/checkpoint: {message or status}", status=status)
        if status == 404:
            if is_json:
                raise ProfileNotFound(status=404)
            raise NotServed(
                "Instagram answered with an HTML 'Page Not Found' instead of data. Logged-out "
                "access is blocked; if this also happens logged in, the endpoint has changed "
                "(run `make diagnose`).",
                status=404,
            )
        if status in (401, 403) or any(marker in blob for marker in _LOGIN_MARKERS):
            raise LoginRequired(message, status=status)
        if status >= 500:
            raise UpstreamError(f"Instagram server error {status}", status=status)
        if status >= 400:
            raise ScraperError(f"Instagram returned {status}: {message}", status=status)
        if not isinstance(payload, dict):
            raise NotServed("Expected JSON but got an HTML page (login wall)", status=status)
        if payload.get("status") == "fail":
            raise ScraperError(f"Instagram error: {message}", status=status)
        return payload

    # ------------------------------------------------------- web app (GraphQL)
    @asynccontextmanager
    async def borrow_account(self) -> AsyncIterator[Lease]:
        """Hold one account for a multi-request job (page tokens are tied to the session)."""
        if self._pool is None or not self._pool.can_authenticate:
            raise LoginRequired("This needs a logged-in Instagram account. Add one to IG_ACCOUNTS.")
        lease = await self._pool.acquire()
        error: ScraperError | None = None
        try:
            yield lease
        except ScraperError as exc:
            error = exc
            raise
        finally:
            await self._pool.release(lease, error)

    async def _send(
        self,
        method: str,
        url: str,
        *,
        lease: Lease | None,
        referer: str,
        extra_headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
        csrf: str | None = None,
    ) -> httpx.Response:
        proxy = lease.proxy if lease is not None else self._proxies.anonymous()
        headers = {**self._headers(referer, lease, csrf), **(extra_headers or {})}
        try:
            async with self._semaphore:
                await self._throttle()
                resp = await self._http(proxy).request(method, url, data=data, headers=headers)
        except httpx.TransportError as exc:
            self._proxies.report(proxy, ok=False)
            error = UpstreamError(f"Network error talking to Instagram: {exc!r}")
            self.stats.record(False, error)
            raise error from exc
        self._proxies.report(proxy, ok=True)
        return resp

    def _fail(self, error: ScraperError) -> ScraperError:
        self.stats.record(isinstance(error, ProfileNotFound), error)
        return error

    def _check_status(self, resp: httpx.Response) -> None:
        status = resp.status_code
        if status in _REDIRECTS:
            location = resp.headers.get("location", "")
            if "login" in location or "challenge" in location:
                raise self._fail(
                    LoginRequired(f"Instagram redirected to {location}", status=status)
                )
            raise self._fail(UpstreamError(f"Unexpected redirect to {location}", status=status))
        if status == 429:
            raise self._fail(RateLimited(status=429))
        if status in (401, 403):
            raise self._fail(LoginRequired(status=status))
        if status >= 500:
            raise self._fail(UpstreamError(f"Instagram server error {status}", status=status))

    async def web_tokens(self, username: str, lease: Lease) -> WebTokens:
        """Open the profile page logged in, like a browser, and read the page tokens."""
        resp = await self._send(
            "GET",
            f"{ep.BASE_URL}/{username}/",
            lease=lease,
            referer=ep.BASE_URL + "/",
            extra_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        self._check_status(resp)
        if resp.status_code == 404:
            raise self._fail(ProfileNotFound(f"@{username} does not exist"))
        if resp.status_code != 200:
            raise self._fail(ScraperError(f"Profile page returned {resp.status_code}"))
        tokens = parse_tokens(resp.text, username, cookie_csrf=resp.cookies.get("csrftoken"))
        if not tokens.usable:
            raise self._fail(NotServed("The profile page had no web tokens (lsd/fb_dtsg)."))
        self.stats.record(True)
        return tokens

    async def graphql(
        self,
        friendly_name: str,
        doc_id: str,
        variables: dict[str, Any],
        *,
        lease: Lease,
        tokens: WebTokens,
        referer: str,
    ) -> dict[str, Any]:
        form = {
            "av": tokens.viewer_id or "0",
            "__d": "www",
            "__user": "0",
            "__a": "1",
            "__comet_req": "7",
            "lsd": tokens.lsd or "",
            "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": friendly_name,
            "server_timestamps": "true",
            "variables": json.dumps(variables, separators=(",", ":")),
            "doc_id": doc_id,
            "dpr": "2",
            **tokens.page_params,
        }
        if tokens.fb_dtsg:
            form["fb_dtsg"] = tokens.fb_dtsg
            form["jazoest"] = jazoest(tokens.fb_dtsg)
        resp = await self._send(
            "POST",
            ep.GRAPHQL,
            lease=lease,
            referer=referer,
            data=form,
            csrf=tokens.csrf,
            extra_headers={
                "X-FB-Friendly-Name": friendly_name,
                "X-FB-LSD": tokens.lsd or "",
                "X-IG-App-ID": tokens.app_id or self._s.ig_app_id,
                "Content-Type": "application/x-www-form-urlencoded",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
            },
        )
        self._check_status(resp)
        text = resp.text.strip()
        if resp.status_code == 404 or text.startswith("<"):
            raise self._fail(NotServed(f"GraphQL {friendly_name} answered with an HTML page"))
        try:
            payload = loads_first(text)
        except ValueError as exc:
            preview = re.sub(r"[A-Za-z0-9_:-]{24,}", "…", text[:90]).replace("\n", " ")
            raise self._fail(
                ScraperError(
                    f"GraphQL {friendly_name}: unreadable reply "
                    f"(HTTP {resp.status_code}, {len(text)} chars, starts with {preview!r})"
                )
            ) from exc
        if not isinstance(payload, dict):
            raise self._fail(ScraperError(f"GraphQL {friendly_name}: unexpected reply"))
        errors = payload.get("errors") or []
        if errors and not payload.get("data"):
            message = str((errors[0] or {}).get("message") or errors[0])[:300]
            lowered = message.lower()
            if "login" in lowered or "not authorized" in lowered:
                raise self._fail(LoginRequired(message))
            if "rate" in lowered or "wait" in lowered or "limit" in lowered:
                raise self._fail(RateLimited(message))
            raise self._fail(ScraperError(f"GraphQL {friendly_name} failed: {message}"))
        if payload.get("status") == "fail":
            raise self._fail(ScraperError(f"GraphQL {friendly_name}: {payload.get('message')}"))
        if payload.get("error") and not payload.get("data"):
            # Facebook-style error: {"error": 1357001, "errorSummary": ..., "errorDescription": ...}
            code = payload.get("error")
            summary = str(payload.get("errorSummary") or "")[:120]
            description = str(payload.get("errorDescription") or "")[:160]
            message = (
                f"Instagram error {code}: {summary} {description}".strip()
                + f" [tokens: {tokens.summary()}]"
            )
            if code in (1357001, 1357004) or "log in" in message.lower():
                raise self._fail(LoginRequired(message))
            raise self._fail(ScraperError(f"GraphQL {friendly_name}: {message}"))
        self.stats.record(True)
        return payload

    # ------------------------------------------------------------ high level
    async def fetch_profile(self, username: str) -> dict[str, Any]:
        """Profile + first 12 grid posts (GraphQL-style `user` object)."""
        payload = await self.request_json(
            "GET",
            ep.WEB_PROFILE_INFO,
            params={"username": username},
            referer=f"{ep.BASE_URL}/{username}/",
        )
        user = (payload.get("data") or {}).get("user")
        if not user:
            raise ProfileNotFound(f"@{username} does not exist")
        return dict(user)

    async def fetch_user_feed(
        self, user_id: str, *, max_id: str | None = None, count: int = 12
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"count": count}
        if max_id:
            params["max_id"] = max_id
        return await self.request_json(
            "GET", ep.USER_FEED.format(user_id=user_id), params=params, auth=True
        )

    async def fetch_user_clips(
        self, user_id: str, *, max_id: str | None = None, page_size: int = 12
    ) -> dict[str, Any]:
        data = {
            "target_user_id": user_id,
            "page_size": str(page_size),
            "include_feed_video": "true",
        }
        if max_id:
            data["max_id"] = max_id
        return await self.request_json("POST", ep.CLIPS_USER, data=data)

    async def download(self, url: str, dest: Path) -> bool:
        """Download a CDN file atomically. Never raises; returns success."""
        try:
            async with self._semaphore:
                resp = await self._http(self._proxies.anonymous()).get(
                    url,
                    headers={"User-Agent": self._s.user_agent, "Referer": ep.BASE_URL + "/"},
                    follow_redirects=True,
                )
            if resp.status_code != 200 or not resp.content:
                log.warning("Download failed (%s): %s", resp.status_code, url)
                return False
            await asyncio.to_thread(_write_atomic, dest, resp.content)
            return True
        except (httpx.HTTPError, OSError) as exc:
            log.warning("Download error for %s: %r", url, exc)
            return False


def _write_atomic(dest: Path, content: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(content)
    tmp.replace(dest)
