import asyncio
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.dependencies import CalificarLeadDep, JobStoreDep
from app.jobs import JobStore
from app.schemas.responses import JobSubmittedResponse
from app.services.calificar_lead import CalificarLeadService

logger = logging.getLogger(__name__)

router = APIRouter()


class CalificarLeadRequest(BaseModel):
    company_id: str | None = None


async def _run_calificar_lead(
    job_id: str,
    service: CalificarLeadService,
    store: JobStore,
    company_id: str | None,
) -> None:
    store.mark_running(job_id)
    try:
        result = await service.run(company_id=company_id)
        store.mark_completed(job_id, result)
    except Exception as exc:
        logger.exception("CalificarLead job %s failed", job_id)
        store.mark_failed(job_id, str(exc))


@router.post("/calificar_lead", response_model=JobSubmittedResponse, status_code=202)
async def calificar_lead(
    service: CalificarLeadDep,
    store: JobStoreDep,
    request: CalificarLeadRequest | None = None,
) -> JobSubmittedResponse:
    if service is None:
        raise HTTPException(status_code=503, detail="Anthropic not configured")

    company_id = request.company_id if request else None

    # Resolve company upfront so duplicate detection uses the actual company ID
    if company_id is None:
        company_id = await service.resolve_next_company_id()

    existing = store.has_active_job("calificar_lead", company_id)
    if existing:
        return JSONResponse(content={
            "job_id": existing.job_id,
            "status": "already_running",
            "message": "Ya existe un job activo para esta tarea",
        })

    recent = store.recently_completed_job("calificar_lead", company_id)
    if recent:
        return JSONResponse(content={
            "job_id": recent.job_id,
            "status": "recently_completed",
            "message": "Esta empresa fue procesada recientemente",
            "finished_at": recent.finished_at.isoformat() if recent.finished_at else None,
        })

    job = store.create_job(company_id=company_id, task_type="calificar_lead")
    asyncio.create_task(_run_calificar_lead(job.job_id, service, store, company_id))
    return JobSubmittedResponse(
        job_id=job.job_id,
        status=job.status,
        message="CalificarLead job submitted",
    )
