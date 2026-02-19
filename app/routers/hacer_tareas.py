import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.dependencies import HacerTareasDep, JobStoreDep
from app.jobs import JobStore
from app.schemas.responses import JobSubmittedResponse
from app.services.hacer_tareas import HacerTareasService

logger = logging.getLogger(__name__)

router = APIRouter()


async def _run_hacer_tareas(
    job_id: str,
    service: HacerTareasService,
    store: JobStore,
) -> None:
    store.mark_running(job_id)
    try:
        result = await service.run()
        store.mark_completed(job_id, result)
    except Exception as exc:
        logger.exception("Hacer tareas job %s failed", job_id)
        store.mark_failed(job_id, str(exc))


@router.post("/hacer_tareas", response_model=JobSubmittedResponse, status_code=202)
async def hacer_tareas(
    service: HacerTareasDep,
    store: JobStoreDep,
) -> JobSubmittedResponse:
    existing = store.has_active_job("hacer_tareas", None)
    if existing:
        return JSONResponse(content={
            "job_id": existing.job_id,
            "status": "already_running",
            "message": "Ya existe un job activo para esta tarea",
        })

    job = store.create_job(company_id=None, task_type="hacer_tareas")
    asyncio.create_task(_run_hacer_tareas(job.job_id, service, store))
    return JobSubmittedResponse(
        job_id=job.job_id,
        status=job.status,
        message="Hacer tareas job submitted",
    )
