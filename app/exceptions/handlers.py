import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from .custom import ElevenLabsError, GooglePlacesError, HubSpotError, RateLimitError, TripAdvisorError

logger = logging.getLogger(__name__)


async def hubspot_error_handler(_request: Request, exc: HubSpotError) -> JSONResponse:
    logger.error("HubSpot error: %s (status=%s)", exc.message, exc.status_code)
    return JSONResponse(
        status_code=502,
        content={"detail": f"HubSpot error: {exc.message}"},
    )


async def google_places_error_handler(_request: Request, exc: GooglePlacesError) -> JSONResponse:
    logger.error("Google Places error: %s (status=%s)", exc.message, exc.status_code)
    return JSONResponse(
        status_code=502,
        content={"detail": f"Google Places error: {exc.message}"},
    )


async def tripadvisor_error_handler(_request: Request, exc: TripAdvisorError) -> JSONResponse:
    logger.error("TripAdvisor error: %s (status=%s)", exc.message, exc.status_code)
    return JSONResponse(
        status_code=502,
        content={"detail": f"TripAdvisor error: {exc.message}"},
    )


async def elevenlabs_error_handler(_request: Request, exc: ElevenLabsError) -> JSONResponse:
    logger.error("ElevenLabs error: %s (status=%s)", exc.message, exc.status_code)
    return JSONResponse(
        status_code=502,
        content={"detail": f"ElevenLabs error: {exc.message}"},
    )


async def rate_limit_error_handler(_request: Request, exc: RateLimitError) -> JSONResponse:
    logger.warning("Rate limit hit for %s", exc.service)
    return JSONResponse(
        status_code=429,
        content={"detail": f"Rate limit exceeded for {exc.service}"},
    )
