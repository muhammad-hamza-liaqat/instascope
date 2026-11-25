"""Liveness/readiness probes. Readiness doubles as the self-management status page."""

from typing import Annotated, Any

from fastapi import APIRouter, Request, Security
from sqlalchemy import text

from app import __version__
from app.api.deps import api_key_header, api_key_valid

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get(
    "/health/ready",
    summary="Readiness + self-management status (account details need the API key)",
)
async def ready(
    request: Request, api_key: Annotated[str | None, Security(api_key_header)] = None
) -> dict[str, Any]:
    state = request.app.state
    async with state.engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    details = api_key_valid(request, api_key)
    pool_summary = state.pool.summary(details=details)
    degraded = pool_summary["configured"] > 0 and pool_summary["usable_now"] == 0
    body: dict[str, Any] = {
        "status": "degraded" if degraded else "ready",
        "database": "ok",
        "accounts": pool_summary,
        "proxies": state.proxies.summary(),
        "scraping": state.client.stats.snapshot(),
    }
    if details:
        body["recent_alerts"] = list(state.alerts.recent)
    return body
