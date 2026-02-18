import asyncio
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.dependencies import EnrichmentDep, JobStoreDep
from app.jobs import JobStore
from app.schemas.responses import EnrichmentResponse, JobStatusResponse, JobSubmittedResponse
from app.services.enrichment import EnrichmentService

logger = logging.getLogger(__name__)

router = APIRouter()


class EnrichmentRequest(BaseModel):
    company_id: str | None = None


async def _run_enrichment(
    job_id: str,
    service: EnrichmentService,
    store: JobStore,
    company_id: str | None,
) -> None:
    store.mark_running(job_id)
    try:
        result = await service.run(company_id=company_id)
        store.mark_completed(job_id, result)
    except Exception as exc:
        logger.exception("Enrichment job %s failed", job_id)
        store.mark_failed(job_id, str(exc))


@router.post("/datos", response_model=JobSubmittedResponse, status_code=202)
async def enrich_companies(
    service: EnrichmentDep,
    store: JobStoreDep,
    request: EnrichmentRequest | None = None,
) -> JobSubmittedResponse:
    company_id = request.company_id if request else None

    # Resolve company upfront so duplicate detection uses the actual company ID
    if company_id is None:
        company_id = await service.resolve_next_company_id()

    existing = store.has_active_job("enrichment", company_id)
    if existing:
        return JSONResponse(content={
            "job_id": existing.job_id,
            "status": "already_running",
            "message": "Ya existe un job activo para esta tarea",
        })

    recent = store.recently_completed_job("enrichment", company_id)
    if recent:
        return JSONResponse(content={
            "job_id": recent.job_id,
            "status": "recently_completed",
            "message": "Esta empresa fue procesada recientemente",
            "finished_at": recent.finished_at.isoformat() if recent.finished_at else None,
        })

    job = store.create_job(company_id=company_id, task_type="enrichment")
    asyncio.create_task(_run_enrichment(job.job_id, service, store, company_id))
    return JobSubmittedResponse(
        job_id=job.job_id,
        status=job.status,
        message="Enrichment job submitted",
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str, store: JobStoreDep) -> JobStatusResponse:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(**job.model_dump())


@router.post("/datos/sync", response_model=EnrichmentResponse)
async def enrich_companies_sync(
    service: EnrichmentDep,
    request: EnrichmentRequest | None = None,
) -> EnrichmentResponse:
    return await service.run(
        company_id=request.company_id if request else None
    )
