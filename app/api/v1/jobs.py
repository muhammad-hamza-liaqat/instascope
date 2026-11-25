"""Batch scrape jobs."""

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from app.api.deps import JobSvc
from app.schemas.jobs import JobCreate, JobRead

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post(
    "",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Scrape many profiles in the background",
)
async def create_job(body: JobCreate, background: BackgroundTasks, svc: JobSvc) -> JobRead:
    job = await svc.create(body)
    background.add_task(svc.run, job.id)
    return job


@router.get("/{job_id}", response_model=JobRead, summary="Job status and per-profile results")
async def get_job(job_id: str, svc: JobSvc) -> JobRead:
    job = await svc.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
