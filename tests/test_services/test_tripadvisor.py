import pytest
import respx
from httpx import AsyncClient, Response

from app.exceptions.custom import RateLimitError, TripAdvisorError
from app.services.tripadvisor import TripAdvisorService


@pytest.fixture
def service():
    client = AsyncClient()
    return TripAdvisorService(client, "test-key")


@respx.mock
@pytest.mark.asyncio
async def test_search_returns_location_id(service):
    respx.get("https://api.content.tripadvisor.com/api/v1/location/search").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {"location_id": "123456", "name": "Hotel Test"},
                ]
            },
        )
    )

    result = await service.search("Hotel Test Santiago")
    assert result == "123456"


@respx.mock
@pytest.mark.asyncio
async def test_search_no_results(service):
    respx.get("https://api.content.tripadvisor.com/api/v1/location/search").mock(
        return_value=Response(200, json={"data": []})
    )

    result = await service.search("Nonexistent Hotel")
    assert result is None


@respx.mock
@pytest.mark.asyncio
async def test_search_rate_limit(service):
    respx.get("https://api.content.tripadvisor.com/api/v1/location/search").mock(
        return_value=Response(429, text="Rate limit exceeded")
    )

    with pytest.raises(RateLimitError):
        await service.search("Hotel Test")


@respx.mock
@pytest.mark.asyncio
async def test_search_api_error(service):
    respx.get("https://api.content.tripadvisor.com/api/v1/location/search").mock(
        return_value=Response(500, text="Internal Server Error")
    )

    with pytest.raises(TripAdvisorError) as exc_info:
        await service.search("Hotel Test")
    assert exc_info.value.status_code == 500


@respx.mock
@pytest.mark.asyncio
async def test_get_details(service):
    respx.get(
        "https://api.content.tripadvisor.com/api/v1/location/123456/details"
    ).mock(
        return_value=Response(
            200,
            json={
                "location_id": "123456",
                "name": "Hotel Test",
                "rating": "4.5",
                "num_reviews": "1234",
                "ranking_data": {"ranking_string": "#3 of 245 hotels in Santiago"},
                "price_level": "$$",
                "category": {"name": "Hotel"},
                "subcategory": [{"name": "Boutique"}],
                "web_url": "https://www.tripadvisor.com/Hotel_Review-123456",
            },
        )
    )

    loc = await service.get_details("123456")
    assert loc is not None
    assert loc.location_id == "123456"
    assert loc.rating == "4.5"
    assert loc.num_reviews == "1234"
    assert loc.price_level == "$$"
    assert loc.web_url == "https://www.tripadvisor.com/Hotel_Review-123456"


@respx.mock
@pytest.mark.asyncio
async def test_search_and_get_details(service):
    respx.get("https://api.content.tripadvisor.com/api/v1/location/search").mock(
        return_value=Response(
            200,
            json={"data": [{"location_id": "789", "name": "Hotel ABC"}]},
        )
    )
    respx.get(
        "https://api.content.tripadvisor.com/api/v1/location/789/details"
    ).mock(
        return_value=Response(
            200,
            json={
                "location_id": "789",
                "name": "Hotel ABC",
                "rating": "4.0",
                "num_reviews": "500",
            },
        )
    )

    loc = await service.search_and_get_details("Hotel ABC Santiago")
    assert loc is not None
    assert loc.location_id == "789"
    assert loc.rating == "4.0"


@respx.mock
@pytest.mark.asyncio
async def test_search_and_get_details_no_results(service):
    respx.get("https://api.content.tripadvisor.com/api/v1/location/search").mock(
        return_value=Response(200, json={"data": []})
    )

    loc = await service.search_and_get_details("Nonexistent")
    assert loc is None
