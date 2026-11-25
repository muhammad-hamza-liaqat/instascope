"""Batch scrape jobs, persisted in the DB and run in the background."""

import asyncio
import logging
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ScraperError
from app.core.utils import normalize_username, utcnow
from app.db.models import JobRow
from app.db.repository import Repository
from app.schemas.jobs import JobCreate, JobItemResult, JobRead
from app.services.profile_service import ProfileService

log = logging.getLogger(__name__)


def _to_read(row: JobRow) -> JobRead:
    return JobRead.model_validate(
        {
            "id": row.id,
            "status": row.status,
            "total": row.total,
            "done": row.done,
            "created_at": row.created_at,
            "finished_at": row.finished_at,
            "request": row.request,
            "results": row.results or [],
        }
    )


class JobService:
    def __init__(
        self,
        profiles: ProfileService,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._profiles = profiles
        self._sessions = sessions
        self._s = settings

    async def create(self, body: JobCreate) -> JobRead:
        usernames = list(dict.fromkeys(normalize_username(u) for u in body.usernames))
        request = body.model_copy(update={"usernames": usernames})
        async with self._sessions() as session, session.begin():
            row = await Repository(session).add_job(
                JobRow(
                    id=str(uuid4()),
                    status="queued",
                    request=request.model_dump(mode="json"),
                    results=[],
                    total=len(usernames),
                    done=0,
                    created_at=utcnow(),
                )
            )
            return _to_read(row)

    async def get(self, job_id: str) -> JobRead | None:
        async with self._sessions() as session:
            row = await Repository(session).get_job(job_id)
            return _to_read(row) if row else None

    async def mark_interrupted(self) -> None:
        """Jobs still queued/running at startup were killed by a restart."""
        async with self._sessions() as session, session.begin():
            for row in await Repository(session).jobs_with_status("queued", "running"):
                row.status = "interrupted"
                row.finished_at = utcnow()

    async def run(self, job_id: str) -> None:
        async with self._sessions() as session, session.begin():
            row = await Repository(session).get_job(job_id)
            if row is None:
                return
            row.status = "running"
            request = JobCreate.model_validate(row.request)

        lock = asyncio.Lock()
        semaphore = asyncio.Semaphore(self._s.job_concurrency)
        outcomes: list[JobItemResult] = []

        async def scrape_one(username: str) -> None:
            async with semaphore:
                try:
                    res = await self._profiles.get_profile(
                        username,
                        refresh=request.refresh,
                        posts_limit=request.posts_limit,
                        reels_limit=request.reels_limit,
                    )
                    outcome = JobItemResult(
                        username=username,
                        status="ok",
                        source=res.source,
                        followers=res.profile.followers,
                        posts=len(res.posts),
                        reels=len(res.reels),
                    )
                except ScraperError as exc:
                    outcome = JobItemResult(username=username, status="error", error=exc.message)
                except Exception:
                    log.exception("Job %s failed on @%s", job_id, username)
                    outcome = JobItemResult(
                        username=username, status="error", error="internal error"
                    )
            async with lock:
                outcomes.append(outcome)
                await self._record(job_id, outcome)

        await asyncio.gather(*(scrape_one(u) for u in request.usernames))

        ok = sum(1 for o in outcomes if o.status == "ok")
        status = "completed" if ok == len(outcomes) else "partial" if ok else "failed"
        async with self._sessions() as session, session.begin():
            row = await Repository(session).get_job(job_id)
            if row is not None:
                row.status = status
                row.finished_at = utcnow()

    async def _record(self, job_id: str, outcome: JobItemResult) -> None:
        async with self._sessions() as session, session.begin():
            row = await Repository(session).get_job(job_id)
            if row is not None:
                row.results = [*(row.results or []), outcome.model_dump(mode="json")]
                row.done = (row.done or 0) + 1
