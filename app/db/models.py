"""SQLAlchemy ORM tables.

Pattern: a few queryable columns + the full parsed model in `data` (JSON)
+ Instagram's original payload in `raw` (JSON, optional).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.utils import utcnow

TZDateTime = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class ProfileRow(Base):
    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # Instagram user id
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255))
    followers: Mapped[int] = mapped_column(BigInteger, default=0)
    following: Mapped[int] = mapped_column(BigInteger, default=0)
    posts_count: Mapped[int] = mapped_column(BigInteger, default=0)
    is_private: Mapped[bool] = mapped_column(default=False)
    is_verified: Mapped[bool] = mapped_column(default=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    last_scraped_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, index=True)
    posts_scraped_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    reels_scraped_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class SnapshotRow(Base):
    """One row per live scrape — follower/following/posts history over time."""

    __tablename__ = "profile_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    followers: Mapped[int] = mapped_column(BigInteger)
    following: Mapped[int] = mapped_column(BigInteger)
    posts_count: Mapped[int] = mapped_column(BigInteger)
    scraped_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, index=True)


class MediaRow(Base):
    """Posts and reels. `in_feed` = seen in the post grid; `is_reel` = it is a reel."""

    __tablename__ = "media"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # Instagram media pk
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    shortcode: Mapped[str] = mapped_column(String(64), index=True)
    media_type: Mapped[str] = mapped_column(String(16))
    is_reel: Mapped[bool] = mapped_column(default=False, index=True)
    in_feed: Mapped[bool] = mapped_column(default=False, index=True)
    taken_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True, index=True)
    likes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    comments: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    views: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    plays: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class AccountRow(Base):
    """Instagram accounts in the self-managed pool. Passwords are NOT stored (config only)."""

    __tablename__ = "ig_accounts"

    username: Mapped[str] = mapped_column(String(80), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    session_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet-encrypted
    user_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    password_fp: Mapped[str | None] = mapped_column(String(64), nullable=True)  # keyed hash
    strikes: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    total_requests: Mapped[int] = mapped_column(Integer, default=0)
    total_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    next_login_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
