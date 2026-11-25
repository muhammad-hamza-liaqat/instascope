"""App factory. Run: `uv run uvicorn app.main:app --reload`."""

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import __version__
from app.api.health import router as health_router
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.utils import setup_logging
from app.db.session import create_engine, init_db
from app.scraper.client import InstagramClient
from app.services.media_store import MEDIA_URL_PREFIX, MediaStore
from app.services.profile_service import ProfileService

log = logging.getLogger("app")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    settings.media_dir.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        await init_db(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        client = InstagramClient(settings)
        profiles = ProfileService(client, sessions, MediaStore(client, settings), settings)

        app.state.engine = engine
        app.state.profile_service = profiles
        log.info(
            "%s %s ready (sessions=%d, proxies=%d, auth=%s)",
            settings.app_name,
            __version__,
            len(settings.ig_session_ids),
            len(settings.proxies),
            "on" if settings.api_keys else "off",
        )
        try:
            yield
        finally:
            await client.aclose()
            await engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Self-hosted Instagram profile scraper: bio, profile picture, followers, "
            "posts and reels. Every live scrape is stored with follower history."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid4().hex[:12]
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-ms"] = f"{elapsed_ms:.1f}"
        log.info(
            "%s %s -> %d (%.0fms) rid=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
        return response

    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router, prefix="/api/v1")
    app.mount(MEDIA_URL_PREFIX, StaticFiles(directory=settings.media_dir), name="media")
    return app


app = create_app()
