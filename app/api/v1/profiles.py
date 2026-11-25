"""The one data endpoint: everything about a profile in a single response."""

from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.deps import ProfileSvc
from app.schemas.instagram import ScrapeResult

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.get(
    "/{username}",
    response_model=ScrapeResult,
    summary="Everything about a profile: bio, picture, counts, posts, reels and history",
)
async def get_profile(
    username: Annotated[
        str, Path(min_length=1, max_length=64, description="Instagram handle, with or without @")
    ],
    svc: ProfileSvc,
    refresh: Annotated[bool, Query(description="Ignore the cache and scrape live")] = False,
    posts_limit: Annotated[int, Query(ge=0, le=500, description="0 skips posts")] = 12,
    reels_limit: Annotated[int, Query(ge=0, le=500, description="0 skips reels")] = 12,
) -> ScrapeResult:
    return await svc.get_profile(
        username, refresh=refresh, posts_limit=posts_limit, reels_limit=reels_limit
    )
