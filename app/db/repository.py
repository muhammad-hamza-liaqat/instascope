"""All database reads/writes live here."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JobRow, MediaRow, ProfileRow, SnapshotRow
from app.schemas.instagram import MediaData, ProfileData


class Repository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    # --------------------------------------------------------------- profiles
    async def get_profile(self, username: str) -> ProfileRow | None:
        return await self.s.scalar(select(ProfileRow).where(ProfileRow.username == username))

    async def upsert_profile(
        self,
        profile: ProfileData,
        *,
        raw: dict[str, Any] | None,
        scraped_at: datetime,
        posts_done: bool,
        reels_done: bool,
    ) -> ProfileRow:
        # Another account previously used this username (renamed since): free it up.
        clash = await self.s.scalar(
            select(ProfileRow).where(
                ProfileRow.username == profile.username, ProfileRow.id != profile.id
            )
        )
        if clash is not None:
            clash.username = f"{clash.username}~{clash.id}"[:80]
            await self.s.flush()

        row = await self.s.get(ProfileRow, profile.id)
        if row is None:
            row = ProfileRow(id=profile.id, first_seen_at=scraped_at)
            self.s.add(row)
        row.username = profile.username
        row.full_name = profile.full_name
        row.followers = profile.followers
        row.following = profile.following
        row.posts_count = profile.posts_count
        row.is_private = profile.is_private
        row.is_verified = profile.is_verified
        row.data = profile.model_dump(mode="json")
        if raw is not None:
            row.raw = raw
        row.last_scraped_at = scraped_at
        if posts_done:
            row.posts_scraped_at = scraped_at
        if reels_done:
            row.reels_scraped_at = scraped_at

        self.s.add(
            SnapshotRow(
                profile_id=profile.id,
                followers=profile.followers,
                following=profile.following,
                posts_count=profile.posts_count,
                scraped_at=scraped_at,
            )
        )
        await self.s.flush()
        return row

    async def list_profiles(self, offset: int, limit: int) -> tuple[Sequence[ProfileRow], int]:
        total = await self.s.scalar(select(func.count()).select_from(ProfileRow)) or 0
        rows = await self.s.scalars(
            select(ProfileRow)
            .order_by(ProfileRow.last_scraped_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return rows.all(), total

    async def delete_profile(self, profile_id: str) -> None:
        await self.s.execute(delete(MediaRow).where(MediaRow.profile_id == profile_id))
        await self.s.execute(delete(SnapshotRow).where(SnapshotRow.profile_id == profile_id))
        await self.s.execute(delete(ProfileRow).where(ProfileRow.id == profile_id))

    async def history(self, profile_id: str, limit: int) -> Sequence[SnapshotRow]:
        rows = await self.s.scalars(
            select(SnapshotRow)
            .where(SnapshotRow.profile_id == profile_id)
            .order_by(SnapshotRow.scraped_at.desc())
            .limit(limit)
        )
        return list(reversed(rows.all()))

    # ------------------------------------------------------------------ media
    async def upsert_media(
        self, profile_id: str, items: list[MediaData], *, in_feed: bool, now: datetime
    ) -> None:
        if not items:
            return
        ids = [m.id for m in items]
        existing = {
            r.id: r
            for r in (await self.s.scalars(select(MediaRow).where(MediaRow.id.in_(ids)))).all()
        }
        for media in items:
            row = existing.get(media.id)
            if row is None:
                row = MediaRow(id=media.id, profile_id=profile_id, first_seen_at=now, in_feed=False)
                self.s.add(row)
                existing[media.id] = row
            data = media.model_dump(mode="json")
            # Keep a previously downloaded thumbnail if this payload has none.
            if not data.get("thumbnail_local") and row.data and row.data.get("thumbnail_local"):
                data["thumbnail_local"] = row.data["thumbnail_local"]
            row.shortcode = media.shortcode
            row.media_type = media.media_type.value
            row.is_reel = media.is_reel
            row.in_feed = bool(row.in_feed) or in_feed
            row.taken_at = media.taken_at
            row.likes = media.likes
            row.comments = media.comments
            row.views = media.views
            row.plays = media.plays
            row.data = data
            if media.raw is not None:
                row.raw = media.raw
            row.updated_at = now

    async def list_media(self, profile_id: str, *, reels: bool, limit: int) -> list[MediaData]:
        query = select(MediaRow).where(MediaRow.profile_id == profile_id)
        query = query.where(MediaRow.is_reel.is_(True) if reels else MediaRow.in_feed.is_(True))
        query = query.order_by(MediaRow.taken_at.desc().nulls_last()).limit(limit)
        rows = await self.s.scalars(query)
        return [MediaData.model_validate(r.data) for r in rows.all()]

    # ------------------------------------------------------------------- jobs
    async def add_job(self, job: JobRow) -> JobRow:
        self.s.add(job)
        await self.s.flush()
        return job

    async def get_job(self, job_id: str) -> JobRow | None:
        return await self.s.get(JobRow, job_id)

    async def jobs_with_status(self, *statuses: str) -> Sequence[JobRow]:
        rows = await self.s.scalars(select(JobRow).where(JobRow.status.in_(statuses)))
        return rows.all()
