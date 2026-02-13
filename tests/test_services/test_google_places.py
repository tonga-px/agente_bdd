import httpx
import pytest
import respx
from httpx import Response

from app.exceptions.custom import GooglePlacesError, RateLimitError
from app.services.google_places import DETAILS_URL, GooglePlacesService, build_search_query


def test_build_query_all_parts():
    assert build_search_query("Acme Corp", "Santiago", "Chile") == "Acme Corp, Santiago, Chile"


def test_build_query_name_only():
    assert build_search_query("Acme Corp") == "Acme Corp"


def test_build_query_skips_none():
    assert build_search_query("Acme Corp", None, "Chile") == "Acme Corp, Chile"


PLACE_ID = "ChIJN1t_tDeuEmsRUsoyG83frY4"
DETAILS_ENDPOINT = f"{DETAILS_URL}/{PLACE_ID}"


@respx.mock
@pytest.mark.asyncio
async def test_get_place_details_success():
    respx.get(DETAILS_ENDPOINT).mock(
        return_value=Response(
            200,
            json={
                "formattedAddress": "Av. Providencia 123, Santiago, Chile",
                "nationalPhoneNumber": "+56 2 1234 5678",
                "websiteUri": "https://acme.cl",
                "rating": 4.3,
                "userRatingCount": 1234,
                "googleMapsUri": "https://maps.google.com/?cid=123",
                "priceLevel": "PRICE_LEVEL_MODERATE",
                "businessStatus": "OPERATIONAL",
                "addressComponents": [
                    {"longText": "Santiago", "shortText": "Santiago", "types": ["locality"]},
                ],
            },
        )
    )

    async with httpx.AsyncClient() as client:
        service = GooglePlacesService(client, "test-key")
        place = await service.get_place_details(PLACE_ID)

    assert place is not None
    assert place.formattedAddress == "Av. Providencia 123, Santiago, Chile"
    assert place.nationalPhoneNumber == "+56 2 1234 5678"
    assert place.websiteUri == "https://acme.cl"
    assert place.rating == 4.3
    assert place.userRatingCount == 1234
    assert place.googleMapsUri == "https://maps.google.com/?cid=123"
    assert place.priceLevel == "PRICE_LEVEL_MODERATE"
    assert place.businessStatus == "OPERATIONAL"


@respx.mock
@pytest.mark.asyncio
async def test_get_place_details_rate_limit():
    respx.get(DETAILS_ENDPOINT).mock(return_value=Response(429, text="rate limited"))

    async with httpx.AsyncClient() as client:
        service = GooglePlacesService(client, "test-key")
        with pytest.raises(RateLimitError):
            await service.get_place_details(PLACE_ID)


@respx.mock
@pytest.mark.asyncio
async def test_get_place_details_error():
    respx.get(DETAILS_ENDPOINT).mock(return_value=Response(404, text="not found"))

    async with httpx.AsyncClient() as client:
        service = GooglePlacesService(client, "test-key")
        with pytest.raises(GooglePlacesError):
            await service.get_place_details(PLACE_ID)
