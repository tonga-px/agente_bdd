from typing import Annotated

from fastapi import Depends, Request

from app.jobs import JobStore
from app.services.enrichment import EnrichmentService
from app.services.prospeccion import ProspeccionService


def get_enrichment_service(request: Request) -> EnrichmentService:
    return request.app.state.enrichment_service


def get_job_store(request: Request) -> JobStore:
    return request.app.state.job_store


def get_prospeccion_service(request: Request) -> ProspeccionService | None:
    return getattr(request.app.state, "prospeccion_service", None)


EnrichmentDep = Annotated[EnrichmentService, Depends(get_enrichment_service)]
JobStoreDep = Annotated[JobStore, Depends(get_job_store)]
ProspeccionDep = Annotated[ProspeccionService | None, Depends(get_prospeccion_service)]
