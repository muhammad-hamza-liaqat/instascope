"""Batch scrape job models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

JobStatus = Literal["queued", "running", "completed", "partial", "failed", "interrupted"]


class JobCreate(BaseModel):
    usernames: list[str] = Field(min_length=1, max_length=100)
    posts_limit: int = Field(12, ge=0, le=500)
    reels_limit: int = Field(12, ge=0, le=500)
    refresh: bool = False


class JobItemResult(BaseModel):
    username: str
    status: Literal["ok", "error"]
    error: str | None = None
    source: str | None = None
    followers: int | None = None
    posts: int | None = None
    reels: int | None = None


class JobRead(BaseModel):
    id: str
    status: JobStatus
    total: int
    done: int
    created_at: datetime
    finished_at: datetime | None = None
    request: JobCreate
    results: list[JobItemResult] = []
