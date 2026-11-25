"""Public API models (what clients receive)."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field

IG = "https://www.instagram.com"


class MediaType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    CAROUSEL = "carousel"
    REEL = "reel"


class BioLink(BaseModel):
    title: str | None = None
    url: str


class ProfileData(BaseModel):
    id: str
    username: str
    full_name: str | None = None
    biography: str | None = None
    bio_links: list[BioLink] = []
    external_url: str | None = None
    followers: int = 0
    following: int = 0
    posts_count: int = 0
    highlight_count: int | None = None
    is_private: bool = False
    is_verified: bool = False
    is_business: bool = False
    is_professional: bool = False
    category: str | None = None
    business_email: str | None = None
    business_phone: str | None = None
    pronouns: list[str] = []
    profile_pic_url: str | None = None
    profile_pic_url_hd: str | None = None
    profile_pic_local: str | None = Field(
        None, description="Locally saved copy, served under /media"
    )
    fbid: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        return f"{IG}/{self.username}/"


class CarouselItem(BaseModel):
    display_url: str | None = None
    video_url: str | None = None
    is_video: bool = False


class MediaData(BaseModel):
    id: str
    shortcode: str
    media_type: MediaType
    caption: str | None = None
    hashtags: list[str] = []
    mentions: list[str] = []
    likes: int | None = None
    comments: int | None = None
    views: int | None = None
    plays: int | None = None
    duration: float | None = None
    taken_at: datetime | None = None
    display_url: str | None = None
    video_url: str | None = None
    thumbnail_local: str | None = None
    location: str | None = None
    width: int | None = None
    height: int | None = None
    owner_username: str | None = None
    is_pinned: bool = False
    audio: str | None = None
    children: list[CarouselItem] = []
    raw: dict[str, Any] | None = Field(default=None, exclude=True, repr=False)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_reel(self) -> bool:
        return self.media_type == MediaType.REEL

    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        kind = "reel" if self.is_reel else "p"
        return f"{IG}/{kind}/{self.shortcode}/"


Source = Literal["live", "cache"]


class HistoryPoint(BaseModel):
    followers: int
    following: int
    posts_count: int
    scraped_at: datetime


class ScrapeResult(BaseModel):
    profile: ProfileData
    posts: list[MediaData] = []
    reels: list[MediaData] = []
    source: Source
    scraped_at: datetime
    history: list[HistoryPoint] = Field(
        default=[], description="Stored follower/following/posts snapshots, oldest first"
    )
    warnings: list[str] = []
