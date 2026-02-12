from fastapi import APIRouter

from app.dependencies import EnrichmentDep
from app.schemas.responses import EnrichmentResponse

router = APIRouter()


@router.post("/enrich", response_model=EnrichmentResponse)
async def enrich_companies(service: EnrichmentDep) -> EnrichmentResponse:
    return await service.run()
