"""Low-level async Instagram HTTP client.

Responsibilities: headers/cookies, proxy + session rotation, global throttling,
retries with exponential backoff, and mapping Instagram responses to domain errors.
It returns raw JSON; parsing lives in `parsers.py`.
"""

import asyncio
import itertools
import logging
import random
import string
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx

from app.core.config import Settings
from app.core.errors import (
    Blocked,
    LoginRequired,
    ProfileNotFound,
    RateLimited,
    ScraperError,
    UpstreamError,
)
from app.scraper import endpoints as ep

log = logging.getLogger(__name__)

_LOGIN_MARKERS = ("login_required", "require_login", "LoginAndSignupPage")
_BLOCK_MARKERS = ("checkpoint_required", "challenge_required", "feedback_required")
_REDIRECTS = {301, 302, 303, 307, 308}


class InstagramClient:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        proxies: list[str | None] = [*settings.proxies] or [None]
        self._clients = [self._build_client(p) for p in proxies]
        self._client_cycle = itertools.cycle(range(len(self._clients)))
        self._session_cycle = (
            itertools.cycle(settings.ig_session_ids) if settings.ig_session_ids else None
        )
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._throttle_lock = asyncio.Lock()
        self._next_allowed = 0.0

    # ------------------------------------------------------------------ setup
    def _build_client(self, proxy: str | None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            http2=True,
            proxy=proxy,
            timeout=self._s.request_timeout,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    @property
    def has_session(self) -> bool:
        return self._session_cycle is not None

    async def aclose(self) -> None:
        await asyncio.gather(*(c.aclose() for c in self._clients))

    def _next_client(self) -> httpx.AsyncClient:
        return self._clients[next(self._client_cycle)]

    def _headers(self, referer: str, session_id: str | None) -> dict[str, str]:
        csrf = "".join(random.choices(string.ascii_letters + string.digits, k=32))
        cookies = {"csrftoken": csrf, "ig_nrcb": "1"}
        if session_id:
            cookies["sessionid"] = session_id
            user_id = unquote(session_id).split(":", 1)[0]
            if user_id.isdigit():
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
        if auth and not self.has_session:
            raise LoginRequired("This data needs a logged-in session. Set IG_SESSION_IDS in .env.")

        use_session = auth or self._s.always_use_session
        attempts = self._s.max_retries + 1
        last_exc: ScraperError | None = None

        for attempt in range(attempts):
            session_id = next(self._session_cycle) if use_session and self._session_cycle else None
            try:
                async with self._semaphore:
                    await self._throttle()
                    resp = await self._next_client().request(
                        method,
                        url,
                        params=params,
                        data=data,
                        headers=self._headers(referer, session_id),
                    )
                return self._parse_response(resp)
            except httpx.TransportError as exc:
                last_exc = UpstreamError(f"Network error talking to Instagram: {exc!r}")
            except (LoginRequired, Blocked) as exc:
                if not use_session and self.has_session:
                    log.info("Instagram refused logged-out call to %s; retrying with session", url)
                    use_session, last_exc = True, exc
                    continue
                raise
            except ScraperError as exc:
                if not exc.retryable:
                    raise
                last_exc = exc

            if attempt < attempts - 1:
                base = self._s.retry_backoff_base
                delay = base * (2**attempt) + random.uniform(0, base)
                log.warning(
                    "Retry %d/%d for %s %s in %.1fs: %s",
                    attempt + 1,
                    attempts - 1,
                    method,
                    url,
                    delay,
                    last_exc,
                )
                await asyncio.sleep(delay)

        assert last_exc is not None
        raise last_exc

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
            raise Blocked(
                "Instagram answered an API call with an HTML 404 — logged-out access from this "
                "IP is refused. Configure IG_SESSION_IDS and/or PROXIES.",
                status=404,
            )
        if status in (401, 403) or any(marker in blob for marker in _LOGIN_MARKERS):
            raise LoginRequired(message, status=status)
        if status >= 500:
            raise UpstreamError(f"Instagram server error {status}", status=status)
        if status >= 400:
            raise ScraperError(f"Instagram returned {status}: {message}", status=status)
        if not isinstance(payload, dict):
            raise Blocked("Expected JSON but got HTML (probably a login wall)", status=status)
        if payload.get("status") == "fail":
            raise ScraperError(f"Instagram error: {message}", status=status)
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
                resp = await self._next_client().get(
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
