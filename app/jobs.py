from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel

from app.schemas.responses import EnrichmentResponse, ProspeccionResponse

JobResult = EnrichmentResponse | ProspeccionResponse


class JobStatus(StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class Job(BaseModel):
    job_id: str
    status: JobStatus
    created_at: datetime
    finished_at: datetime | None = None
    company_id: str | None = None
    result: JobResult | None = None
    error: str | None = None


class JobStore:
    def __init__(self, max_jobs: int = 1000) -> None:
        self._jobs: dict[str, Job] = {}
        self._max_jobs = max_jobs

    def _evict(self) -> None:
        if len(self._jobs) <= self._max_jobs:
            return
        # Remove oldest completed/failed jobs first
        candidates = sorted(
            (j for j in self._jobs.values() if j.status in (JobStatus.completed, JobStatus.failed)),
            key=lambda j: j.created_at,
        )
        while len(self._jobs) > self._max_jobs and candidates:
            self._jobs.pop(candidates.pop(0).job_id, None)

    def create_job(self, company_id: str | None = None) -> Job:
        job = Job(
            job_id=uuid.uuid4().hex[:12],
            status=JobStatus.pending,
            created_at=datetime.now(timezone.utc),
            company_id=company_id,
        )
        self._jobs[job.job_id] = job
        self._evict()
        return job

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def mark_running(self, job_id: str) -> None:
        if job := self._jobs.get(job_id):
            job.status = JobStatus.running

    def mark_completed(self, job_id: str, result: JobResult) -> None:
        if job := self._jobs.get(job_id):
            job.status = JobStatus.completed
            job.result = result
            job.finished_at = datetime.now(timezone.utc)

    def mark_failed(self, job_id: str, error: str) -> None:
        if job := self._jobs.get(job_id):
            job.status = JobStatus.failed
            job.error = error
            job.finished_at = datetime.now(timezone.utc)
