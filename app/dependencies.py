from typing import Annotated

from fastapi import Depends, Request

from app.jobs import JobStore
from app.services.enrichment import EnrichmentService


def get_enrichment_service(request: Request) -> EnrichmentService:
    return request.app.state.enrichment_service


def get_job_store(request: Request) -> JobStore:
    return request.app.state.job_store


EnrichmentDep = Annotated[EnrichmentService, Depends(get_enrichment_service)]
JobStoreDep = Annotated[JobStore, Depends(get_job_store)]
