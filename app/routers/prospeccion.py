import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.dependencies import JobStoreDep, ProspeccionDep
from app.jobs import JobStore
from app.schemas.responses import JobSubmittedResponse
from app.services.prospeccion import ProspeccionService

logger = logging.getLogger(__name__)

router = APIRouter()


class ProspeccionRequest(BaseModel):
    company_id: str | None = None


async def _run_prospeccion(
    job_id: str,
    service: ProspeccionService,
    store: JobStore,
    company_id: str | None,
) -> None:
    store.mark_running(job_id)
    try:
        result = await service.run(company_id=company_id)
        store.mark_completed(job_id, result)
    except Exception as exc:
        logger.exception("Prospeccion job %s failed", job_id)
        store.mark_failed(job_id, str(exc))


@router.post("/llamada_prospeccion", response_model=JobSubmittedResponse, status_code=202)
async def llamada_prospeccion(
    service: ProspeccionDep,
    store: JobStoreDep,
    request: ProspeccionRequest | None = None,
) -> JobSubmittedResponse:
    if service is None:
        raise HTTPException(status_code=503, detail="ElevenLabs not configured")

    company_id = request.company_id if request else None

    existing = store.has_active_job("prospeccion", company_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Ya existe un job activo para esta tarea (job_id={existing.job_id})",
        )

    job = store.create_job(company_id=company_id, task_type="prospeccion")
    asyncio.create_task(_run_prospeccion(job.job_id, service, store, company_id))
    return JobSubmittedResponse(
        job_id=job.job_id,
        status=job.status,
        message="Prospeccion job submitted",
    )
