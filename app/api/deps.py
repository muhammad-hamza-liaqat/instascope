"""FastAPI dependencies: services from app.state and API-key auth."""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from app.services.profile_service import ProfileService

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(
    request: Request, api_key: Annotated[str | None, Security(_api_key_header)] = None
) -> None:
    keys: list[str] = request.app.state.settings.api_keys
    if not keys:
        return  # auth disabled
    if not api_key or not any(secrets.compare_digest(api_key, k) for k in keys):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )


def _profile_service(request: Request) -> ProfileService:
    return request.app.state.profile_service  # type: ignore[no-any-return]


ProfileSvc = Annotated[ProfileService, Depends(_profile_service)]
