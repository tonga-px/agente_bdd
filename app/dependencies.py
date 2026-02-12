from typing import Annotated

from fastapi import Depends, Request

from app.services.enrichment import EnrichmentService


def get_enrichment_service(request: Request) -> EnrichmentService:
    return request.app.state.enrichment_service


EnrichmentDep = Annotated[EnrichmentService, Depends(get_enrichment_service)]
