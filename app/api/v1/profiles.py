"""Profile scraping endpoints."""

from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status

from app.api.deps import ProfileSvc
from app.schemas.instagram import HistoryResponse, MediaList, ProfilePage, ScrapeResult

router = APIRouter(prefix="/profiles", tags=["profiles"])

Username = Annotated[
    str, Path(min_length=1, max_length=64, description="Instagram handle, with or without @")
]
Limit = Annotated[int, Query(ge=0, le=500)]
Refresh = Annotated[bool, Query(description="Ignore cache and scrape live")]


@router.get("", response_model=ProfilePage, summary="List stored profiles")
async def list_profiles(
    svc: ProfileSvc,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ProfilePage:
    return await svc.list_profiles(offset, limit)


@router.get(
    "/{username}",
    response_model=ScrapeResult,
    summary="Full profile: bio, avatar, counts, posts and reels",
)
async def get_profile(
    username: Username,
    svc: ProfileSvc,
    refresh: Refresh = False,
    posts_limit: Limit = 12,
    reels_limit: Limit = 12,
) -> ScrapeResult:
    return await svc.get_profile(
        username, refresh=refresh, posts_limit=posts_limit, reels_limit=reels_limit
    )


@router.get("/{username}/posts", response_model=MediaList, summary="Posts from the profile grid")
async def get_posts(
    username: Username, svc: ProfileSvc, refresh: Refresh = False, limit: Limit = 12
) -> MediaList:
    res = await svc.get_profile(username, refresh=refresh, posts_limit=limit, reels_limit=0)
    return MediaList(
        username=res.profile.username,
        source=res.source,
        scraped_at=res.scraped_at,
        items=res.posts,
        warnings=res.warnings,
    )


@router.get("/{username}/reels", response_model=MediaList, summary="Reels with play counts")
async def get_reels(
    username: Username, svc: ProfileSvc, refresh: Refresh = False, limit: Limit = 12
) -> MediaList:
    res = await svc.get_profile(username, refresh=refresh, posts_limit=0, reels_limit=limit)
    return MediaList(
        username=res.profile.username,
        source=res.source,
        scraped_at=res.scraped_at,
        items=res.reels,
        warnings=res.warnings,
    )


@router.get(
    "/{username}/history",
    response_model=HistoryResponse,
    summary="Follower/following/posts history from stored snapshots",
)
async def get_history(
    username: Username, svc: ProfileSvc, limit: Annotated[int, Query(ge=1, le=5000)] = 500
) -> HistoryResponse:
    return await svc.history(username, limit)


@router.delete(
    "/{username}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete everything stored for a profile",
)
async def delete_profile(username: Username, svc: ProfileSvc) -> Response:
    await svc.delete_profile(username)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
