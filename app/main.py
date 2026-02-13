import logging
import sys
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.config import Settings
from app.exceptions.custom import GooglePlacesError, HubSpotError, RateLimitError, TripAdvisorError
from app.jobs import JobStore
from app.exceptions.handlers import (
    google_places_error_handler,
    hubspot_error_handler,
    rate_limit_error_handler,
    tripadvisor_error_handler,
)
from app.routers.enrichment import router as enrichment_router
from app.services.enrichment import EnrichmentService
from app.services.google_places import GooglePlacesService
from app.services.hubspot import HubSpotService
from app.services.tripadvisor import TripAdvisorService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()

    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    async with httpx.AsyncClient(timeout=30.0) as client:
        hubspot = HubSpotService(client, settings.hubspot_access_token)
        google_places = GooglePlacesService(client, settings.google_places_api_key)

        tripadvisor: TripAdvisorService | None = None
        if settings.tripadvisor_api_key:
            tripadvisor = TripAdvisorService(client, settings.tripadvisor_api_key)

        enrichment = EnrichmentService(
            hubspot, google_places, tripadvisor=tripadvisor, overwrite=settings.overwrite_existing
        )

        app.state.enrichment_service = enrichment
        app.state.job_store = JobStore()
        yield


app = FastAPI(title="Agente BDD", lifespan=lifespan)

app.add_exception_handler(HubSpotError, hubspot_error_handler)
app.add_exception_handler(GooglePlacesError, google_places_error_handler)
app.add_exception_handler(TripAdvisorError, tripadvisor_error_handler)
app.add_exception_handler(RateLimitError, rate_limit_error_handler)

app.include_router(enrichment_router)
