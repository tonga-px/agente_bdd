from typing import Annotated

from fastapi import Depends, Request

from app.jobs import JobStore
from app.services.calificar_lead import CalificarLeadService
from app.services.enrichment import EnrichmentService
from app.services.hacer_tareas import HacerTareasService
from app.services.prospeccion import ProspeccionService


def get_enrichment_service(request: Request) -> EnrichmentService:
    return request.app.state.enrichment_service


def get_job_store(request: Request) -> JobStore:
    return request.app.state.job_store


def get_prospeccion_service(request: Request) -> ProspeccionService | None:
    return getattr(request.app.state, "prospeccion_service", None)


def get_hacer_tareas_service(request: Request) -> HacerTareasService:
    return request.app.state.hacer_tareas_service


EnrichmentDep = Annotated[EnrichmentService, Depends(get_enrichment_service)]
JobStoreDep = Annotated[JobStore, Depends(get_job_store)]
ProspeccionDep = Annotated[ProspeccionService | None, Depends(get_prospeccion_service)]
HacerTareasDep = Annotated[HacerTareasService, Depends(get_hacer_tareas_service)]


def get_calificar_lead_service(request: Request) -> CalificarLeadService | None:
    return getattr(request.app.state, "calificar_lead_service", None)


CalificarLeadDep = Annotated[CalificarLeadService | None, Depends(get_calificar_lead_service)]
