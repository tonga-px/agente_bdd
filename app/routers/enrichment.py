from fastapi import APIRouter
from pydantic import BaseModel

from app.dependencies import EnrichmentDep
from app.schemas.responses import EnrichmentResponse

router = APIRouter()


class EnrichmentRequest(BaseModel):
    company_id: str | None = None


@router.post("/datos", response_model=EnrichmentResponse)
async def enrich_companies(
    service: EnrichmentDep,
    request: EnrichmentRequest | None = None,
) -> EnrichmentResponse:
    return await service.run(
        company_id=request.company_id if request else None
    )
