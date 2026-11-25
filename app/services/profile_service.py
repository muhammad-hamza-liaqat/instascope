"""Orchestrates: cache lookup -> live scrape -> media download -> persistence."""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ScraperError
from app.core.utils import as_utc, normalize_username, utcnow
from app.db.repository import Repository
from app.schemas.instagram import (
    HistoryPoint,
    MediaData,
    ProfileData,
    ScrapeResult,
)
from app.scraper.client import InstagramClient
from app.scraper.parsers import (
    parse_clips_page,
    parse_feed_page,
    parse_graph_post,
    parse_profile,
    timeline_has_more,
    timeline_nodes,
)
from app.services.media_store import MediaStore

log = logging.getLogger(__name__)

HISTORY_LIMIT = 500  # most recent snapshots returned with each profile

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

        if self._s.download_media:
            await self._media.save_all(profile, posts, reels, warnings)

        scraped_at = utcnow()
        async with self._sessions() as session, session.begin():
            repo = Repository(session)
            await repo.upsert_profile(
                profile,
                raw=user if self._s.store_raw_payloads else None,
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
            username,
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
            if not self._client.has_session:
                warnings.append(
                    f"Only the first {len(posts)} posts are available logged-out. "
                    f"Set IG_SESSION_IDS to fetch up to {limit}."
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
