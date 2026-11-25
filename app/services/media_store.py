"""Saves profile pictures and post/reel covers to disk (Instagram CDN URLs expire)."""

import asyncio

from app.core.config import Settings
from app.schemas.instagram import MediaData, ProfileData
from app.scraper.client import InstagramClient

MEDIA_URL_PREFIX = "/media"


class MediaStore:
    def __init__(self, client: InstagramClient, settings: Settings) -> None:
        self._client = client
        self._s = settings
        self.root = settings.media_dir

    async def _save(self, url: str, username: str, filename: str, *, overwrite: bool) -> str | None:
        dest = self.root / username / filename
        public = f"{MEDIA_URL_PREFIX}/{username}/{filename}"
        if not overwrite and dest.exists():
            return public
        return public if await self._client.download(url, dest) else None

    async def save_all(
        self,
        profile: ProfileData,
        posts: list[MediaData],
        reels: list[MediaData],
        warnings: list[str],
    ) -> None:
        pic = profile.profile_pic_url_hd or profile.profile_pic_url
        if pic:
            profile.profile_pic_local = await self._save(
                pic, profile.username, "profile.jpg", overwrite=True
            )
            if profile.profile_pic_local is None:
                warnings.append("Profile picture could not be downloaded.")

        if not self._s.download_thumbnails:
            return
        unique: dict[str, MediaData] = {}
        for media in [*posts, *reels]:
            if media.display_url:
                unique.setdefault(media.id, media)
        saved = await asyncio.gather(
            *(
                self._save(
                    m.display_url or "", profile.username, f"{m.shortcode}.jpg", overwrite=False
                )
                for m in unique.values()
            )
        )
        local_by_id = dict(zip(unique.keys(), saved, strict=True))
        for media in [*posts, *reels]:
            media.thumbnail_local = local_by_id.get(media.id)
        failed = sum(1 for path in saved if path is None)
        if failed:
            warnings.append(f"{failed} thumbnail(s) could not be downloaded.")
