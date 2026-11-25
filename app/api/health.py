"""Liveness/readiness probes (no auth)."""

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import text

from app import __version__

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/health/ready", summary="Readiness probe (DB + scraper config)")
async def ready(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    async with request.app.state.engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {
        "status": "ready",
        "database": "ok",
        "sessions_configured": len(settings.ig_session_ids),
        "proxies_configured": len(settings.proxies),
    }
