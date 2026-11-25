"""Orchestrates: cache lookup -> live scrape -> media download -> persistence."""

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import (
    Blocked,
    LoginRequired,
    NoAccountAvailable,
    NotServed,
    RateLimited,
    ScraperError,
)
from app.core.utils import as_utc, normalize_username, utcnow
from app.db.repository import Repository
from app.schemas.instagram import (
    HistoryPoint,
    MediaData,
    ProfileData,
    ScrapeResult,
)
from app.scraper import web
from app.scraper.accounts import Lease
from app.scraper.client import InstagramClient
from app.scraper.parsers import (
    parse_clips_page,
    parse_feed_page,
    parse_graph_post,
    parse_profile,
    parse_profile_v1,
    timeline_has_more,
    timeline_nodes,
)
from app.services.media_store import MediaStore

log = logging.getLogger(__name__)

HISTORY_LIMIT = 500  # most recent snapshots returned with each profile
LEGACY_RETRY_SECONDS = 6 * 3600  # after the old API answers 'not served', skip it this long

PageFetcher = Callable[[str | None], Awaitable[dict[str, Any]]]
PageParser = Callable[[dict[str, Any]], tuple[list[MediaData], str | None, bool]]


def _dedupe(items: list[MediaData]) -> list[MediaData]:
    """Keep first-seen order, but let later (richer) payloads win."""
    by_id: dict[str, MediaData] = {}
    for item in items:
        by_id[item.id] = item
    return list(by_id.values())


class ProfileService:
    def __init__(
        self,
        client: InstagramClient,
        sessions: async_sessionmaker[AsyncSession],
        media: MediaStore,
        settings: Settings,
    ) -> None:
        self._client = client
        self._sessions = sessions
        self._media = media
        self._s = settings
        self._legacy_dead_until = 0.0

    # ------------------------------------------------------------- public API
    async def get_profile(
        self,
        username: str,
        *,
        refresh: bool = False,
        posts_limit: int = 12,
        reels_limit: int = 12,
    ) -> ScrapeResult:
        """Everything about a profile in one result: fields, posts, reels and history."""
        username = normalize_username(username)
        result = None
        if not refresh:
            result = await self._from_cache(username, posts_limit, reels_limit)
        if result is None:
            result = await self._scrape_live(username, posts_limit, reels_limit)
        result.history = await self._history(result.profile.id)
        return result

    async def _history(self, profile_id: str) -> list[HistoryPoint]:
        async with self._sessions() as session:
            points = await Repository(session).history(profile_id, HISTORY_LIMIT)
        return [
            HistoryPoint(
                followers=p.followers,
                following=p.following,
                posts_count=p.posts_count,
                scraped_at=as_utc(p.scraped_at),
            )
            for p in points
        ]

    # ------------------------------------------------------------------ cache
    def _fresh(self, stamp: datetime | None) -> bool:
        if stamp is None:
            return False
        return utcnow() - as_utc(stamp) < timedelta(seconds=self._s.cache_ttl_seconds)

    async def _from_cache(
        self, username: str, posts_limit: int, reels_limit: int
    ) -> ScrapeResult | None:
        async with self._sessions() as session:
            repo = Repository(session)
            row = await repo.get_profile(username)
            if row is None or not self._fresh(row.last_scraped_at):
                return None
            if posts_limit and not self._fresh(row.posts_scraped_at):
                return None
            if reels_limit and not self._fresh(row.reels_scraped_at):
                return None
            posts = (
                await repo.list_media(row.id, reels=False, limit=posts_limit) if posts_limit else []
            )
            reels = (
                await repo.list_media(row.id, reels=True, limit=reels_limit) if reels_limit else []
            )
            return ScrapeResult(
                profile=ProfileData.model_validate(row.data),
                posts=posts,
                reels=reels,
                source="cache",
                scraped_at=as_utc(row.last_scraped_at),
            )

    # ------------------------------------------------------------------- live
    async def _scrape_live(self, username: str, posts_limit: int, reels_limit: int) -> ScrapeResult:
        """Old JSON API first (cheap); Instagram's web-app GraphQL when it is not served."""
        if time.monotonic() >= self._legacy_dead_until:
            try:
                return await self._scrape_legacy(username, posts_limit, reels_limit)
            except NotServed:
                if not self._client.can_authenticate:
                    raise
                self._legacy_dead_until = time.monotonic() + LEGACY_RETRY_SECONDS
                log.info("Old profile API not served; using the web-app method for the next 6h")
        return await self._scrape_web(username, posts_limit, reels_limit)

    async def _scrape_legacy(
        self, username: str, posts_limit: int, reels_limit: int
    ) -> ScrapeResult:
        warnings: list[str] = []
        user = await self._client.fetch_profile(username)
        profile = parse_profile(user)
        grid = [parse_graph_post(node, profile.username) for node in timeline_nodes(user)]

        posts: list[MediaData] = []
        reels: list[MediaData] = []
        if profile.is_private:
            if posts_limit or reels_limit:
                warnings.append("Profile is private: only public profile fields are available.")
        else:
            if posts_limit:
                posts = await self._collect_posts(
                    profile, grid, timeline_has_more(user), posts_limit, warnings
                )
            if reels_limit:
                reels = await self._collect_reels(profile, grid, reels_limit, warnings)
        return await self._finish(profile, user, posts, reels, warnings, posts_limit, reels_limit)

    # --------------------------------------------------------- web (GraphQL)
    async def _scrape_web(self, username: str, posts_limit: int, reels_limit: int) -> ScrapeResult:
        """Instagram's own web-app flow, with one account held for the whole job."""
        for attempt in range(2):
            try:
                async with self._client.borrow_account() as lease:
                    return await self._scrape_web_with(lease, username, posts_limit, reels_limit)
            except NoAccountAvailable:
                raise
            except (LoginRequired, Blocked, RateLimited):
                # That account had a problem (the pool has already rested/flagged it): retry
                # once with another account if there is one.
                if attempt == 1 or not self._client.can_authenticate:
                    raise
                log.info("Retrying @%s with another account", username)
        raise AssertionError("unreachable")

    async def _scrape_web_with(
        self, lease: Lease, username: str, posts_limit: int, reels_limit: int
    ) -> ScrapeResult:
        warnings: list[str] = []
        client = self._client
        referer = f"https://www.instagram.com/{username}/"
        tokens = await client.web_tokens(username, lease)

        async def posts_page(cursor: str | None, first: int = 12) -> dict[str, Any]:
            return await client.graphql(
                web.POSTS_QUERY,
                self._s.ig_doc_posts,
                web.posts_variables(username, cursor, first),
                lease=lease,
                tokens=tokens,
                referer=referer,
            )

        # The profile query needs the numeric id: from the page, or from the first posts page.
        first_items: list[MediaData] = []
        first_cursor: str | None = None
        first_more = False
        user_id = tokens.profile_id
        if user_id is None:
            first_items, first_cursor, first_more = web.parse_connection(await posts_page(None))
            user_id = web.owner_id(first_items)
        if user_id is None:
            raise ScraperError(f"Could not find the id of @{username} on its page.")

        async def profile_query(uid: str) -> tuple[dict[str, Any] | None, str]:
            payload = await client.graphql(
                web.PROFILE_QUERY,
                self._s.ig_doc_profile,
                web.profile_variables(uid),
                lease=lease,
                tokens=tokens,
                referer=referer,
            )
            data = payload.get("data") or {}
            found = data.get("user")
            errors = "; ".join(
                str((e or {}).get("message") or e)[:150] for e in payload.get("errors") or []
            )
            why = (
                f"reply keys={sorted(payload)}, data keys={sorted(data)}"
                + (f", errors={errors}" if errors else "")
                + f", page tokens: lsd={'yes' if tokens.lsd else 'no'}, "
                f"fb_dtsg={'yes' if tokens.fb_dtsg else 'no'}, "
                f"viewer={'yes' if tokens.viewer_id else 'no'}"
            )
            return (found if isinstance(found, dict) and found.get("username") else None), why

        user, why = await profile_query(user_id)
        if user is None or str(user.get("username", "")).lower() != username:
            # The id read from the page may be the wrong one: use the posts' owner id instead.
            if not first_items and not first_cursor:
                first_items, first_cursor, first_more = web.parse_connection(await posts_page(None))
            owner = web.owner_id(first_items)
            if owner and owner != user_id:
                log.info("Profile id from the page (%s) didn't match; using %s", user_id, owner)
                user_id = owner
                user, why = await profile_query(user_id)
        if user is None:
            raise ScraperError(
                f"Instagram returned no profile data for @{username} (id {user_id}; {why})."
            )
        profile = parse_profile_v1(user)
        if not profile.posts_count and user.get("media_count") is None:
            warnings.append("Post count is not provided by Instagram's web app for this profile.")

        posts: list[MediaData] = []
        reels: list[MediaData] = []
        if profile.is_private and not (user.get("friendship_status") or {}).get("following"):
            if posts_limit or reels_limit:
                warnings.append("Profile is private: only public profile fields are available.")
        else:
            if posts_limit:
                try:
                    if first_items or first_cursor:
                        collected = list(first_items)
                        cursor, more = first_cursor, first_more
                    else:
                        collected, cursor, more = [], None, True
                    while len(collected) < posts_limit and more:
                        items, cursor, more = web.parse_connection(
                            await posts_page(cursor, min(max(posts_limit - len(collected), 12), 50))
                        )
                        collected.extend(items)
                        if not items or not cursor:
                            break
                    posts = _dedupe(collected)[:posts_limit]
                except (LoginRequired, Blocked, RateLimited):
                    raise
                except ScraperError as exc:
                    posts = _dedupe(first_items)[:posts_limit]
                    warnings.append(f"Posts stopped early: {exc.message}")
            if reels_limit:
                try:
                    collected_reels: list[MediaData] = []
                    cursor = None
                    for _ in range(self._s.max_pages):
                        items, cursor, more = web.parse_connection(
                            await client.graphql(
                                web.REELS_QUERY,
                                self._s.ig_doc_reels,
                                web.reels_variables(user_id, cursor, min(reels_limit, 50)),
                                lease=lease,
                                tokens=tokens,
                                referer=referer + "reels/",
                            )
                        )
                        collected_reels.extend(items)
                        if len(collected_reels) >= reels_limit or not more or not cursor:
                            break
                    reels = _dedupe(collected_reels)[:reels_limit]
                except (LoginRequired, Blocked, RateLimited):
                    raise
                except ScraperError as exc:
                    reels = [m for m in posts if m.is_reel][:reels_limit]
                    warnings.append(
                        f"Reels tab unavailable ({exc.message}); returned {len(reels)} reel(s) "
                        "found in the posts instead."
                    )
        return await self._finish(profile, user, posts, reels, warnings, posts_limit, reels_limit)

    # ---------------------------------------------------------------- finish
    async def _finish(
        self,
        profile: ProfileData,
        raw_user: dict[str, Any],
        posts: list[MediaData],
        reels: list[MediaData],
        warnings: list[str],
        posts_limit: int,
        reels_limit: int,
    ) -> ScrapeResult:
        if self._s.download_media:
            await self._media.save_all(profile, posts, reels, warnings)

        scraped_at = utcnow()
        async with self._sessions() as session, session.begin():
            repo = Repository(session)
            await repo.upsert_profile(
                profile,
                raw=raw_user if self._s.store_raw_payloads else None,
                scraped_at=scraped_at,
                posts_done=posts_limit > 0,
                reels_done=reels_limit > 0,
            )
            if not self._s.store_raw_payloads:
                for media in [*posts, *reels]:
                    media.raw = None
            await repo.upsert_media(profile.id, posts, in_feed=True, now=scraped_at)
            await repo.upsert_media(profile.id, reels, in_feed=False, now=scraped_at)

        log.info(
            "Scraped @%s live: %d followers, %d posts, %d reels, %d warnings",
            profile.username,
            profile.followers,
            len(posts),
            len(reels),
            len(warnings),
        )
        return ScrapeResult(
            profile=profile,
            posts=posts,
            reels=reels,
            source="live",
            scraped_at=scraped_at,
            warnings=warnings,
        )

    async def _paginate(self, fetch: PageFetcher, parse: PageParser, limit: int) -> list[MediaData]:
        collected: list[MediaData] = []
        cursor: str | None = None
        for _ in range(self._s.max_pages):
            items, cursor, more = parse(await fetch(cursor))
            collected.extend(items)
            if len(collected) >= limit or not more or not cursor:
                break
        return collected

    async def _collect_posts(
        self,
        profile: ProfileData,
        grid: list[MediaData],
        has_more: bool,
        limit: int,
        warnings: list[str],
    ) -> list[MediaData]:
        posts = list(grid)
        if len(posts) < limit and has_more:
            if not self._client.can_authenticate:
                warnings.append(
                    f"Only the first {len(posts)} posts are available logged-out. "
                    f"Add Instagram accounts (IG_ACCOUNTS) to fetch up to {limit}."
                )
            else:
                try:
                    more = await self._paginate(
                        lambda cursor: self._client.fetch_user_feed(profile.id, max_id=cursor),
                        parse_feed_page,
                        limit,
                    )
                    posts = _dedupe(posts + more)
                except ScraperError as exc:
                    warnings.append(
                        f"Post pagination stopped after {len(posts)} posts: {exc.message}"
                    )
        return posts[:limit]

    async def _collect_reels(
        self, profile: ProfileData, grid: list[MediaData], limit: int, warnings: list[str]
    ) -> list[MediaData]:
        try:
            reels = await self._paginate(
                lambda cursor: self._client.fetch_user_clips(
                    profile.id, max_id=cursor, page_size=min(limit, 50)
                ),
                parse_clips_page,
                limit,
            )
            return _dedupe(reels)[:limit]
        except ScraperError as exc:
            fallback = [m for m in grid if m.is_reel][:limit]
            warnings.append(
                f"Reels tab unavailable ({exc.message}); returned {len(fallback)} reel(s) "
                "found in the post grid instead."
            )
            return fallback
