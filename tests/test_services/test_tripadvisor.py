import pytest
import respx
from httpx import AsyncClient, Response

from app.exceptions.custom import RateLimitError, TripAdvisorError
from app.services.tripadvisor import TripAdvisorService, names_match


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
                "description": "A great hotel for families.",
                "awards": [{"display_name": "Travellers' Choice 2024"}],
                "amenities": ["WiFi", "Pool"],
                "trip_types": [{"name": "Familias", "value": "40"}],
                "review_rating_count": {"5": 800, "4": 300, "3": 50},
                "phone": "+56 2 9999 8888",
                "email": "test@hotel.com",
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
    assert loc.description == "A great hotel for families."
    assert len(loc.awards) == 1
    assert loc.amenities == ["WiFi", "Pool"]
    assert loc.phone == "+56 2 9999 8888"
    assert loc.email == "test@hotel.com"


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


@respx.mock
@pytest.mark.asyncio
async def test_get_photos_success(service):
    respx.get(
        "https://api.content.tripadvisor.com/api/v1/location/123456/photos"
    ).mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": "1",
                        "caption": "Pool",
                        "images": {"small": {"url": "https://img.ta/1.jpg", "width": 150, "height": 150}},
                    },
                    {
                        "id": "2",
                        "caption": "Lobby",
                        "images": {"small": {"url": "https://img.ta/2.jpg", "width": 150, "height": 150}},
                    },
                ]
            },
        )
    )

    photos = await service.get_photos("123456")
    assert len(photos) == 2
    assert photos[0].id == "1"
    assert photos[0].images["small"]["url"] == "https://img.ta/1.jpg"
    assert photos[1].caption == "Lobby"


@respx.mock
@pytest.mark.asyncio
async def test_get_photos_empty(service):
    respx.get(
        "https://api.content.tripadvisor.com/api/v1/location/123456/photos"
    ).mock(return_value=Response(200, json={"data": []}))

    photos = await service.get_photos("123456")
    assert photos == []


@respx.mock
@pytest.mark.asyncio
async def test_get_photos_rate_limit(service):
    respx.get(
        "https://api.content.tripadvisor.com/api/v1/location/123456/photos"
    ).mock(return_value=Response(429, text="Rate limit exceeded"))

    with pytest.raises(RateLimitError):
        await service.get_photos("123456")


@respx.mock
@pytest.mark.asyncio
async def test_get_photos_error(service):
    respx.get(
        "https://api.content.tripadvisor.com/api/v1/location/123456/photos"
    ).mock(return_value=Response(500, text="Internal Server Error"))

    with pytest.raises(TripAdvisorError) as exc_info:
        await service.get_photos("123456")
    assert exc_info.value.status_code == 500


# --- names_match / compound matching tests ---


def test_names_match_exact_overlap():
    """Two names sharing significant tokens match."""
    assert names_match("Hotel Paraiso", "Paraiso Hotel") is True


def test_names_match_no_overlap():
    """No significant token overlap → no match."""
    assert names_match("Hotel Sol", "Residencia Luna") is False


def test_names_match_compound_lifestyle():
    """'Life Style' matches 'Lifestyle' via compound concatenation."""
    assert names_match("Life Style Hotel", "Lifestyle Hotel") is True


def test_names_match_compound_reverse():
    """'Lifestyle' matches 'Life Style' (reverse direction)."""
    assert names_match("Lifestyle Hotel", "Life Style Hotel") is True


def test_names_match_compound_no_match():
    """Compound tokens that don't exist in other → no match."""
    assert names_match("Blue Star Hotel", "Greenfield Inn") is False


def test_names_match_single_token():
    """Single significant token: only 1 match required."""
    assert names_match("Paramanta", "Hotel Paramanta") is True


def test_names_match_compound_paramanta():
    """'Para Manta' doesn't match 'Paramanta' — not adjacent after sort."""
    # "manta" + "para" → "mantapara" (sorted), not "paramanta"
    # This is expected: compound match only works on sorted-adjacent pairs
    assert names_match("Para Manta Hotel", "Hotel Paramanta") is False


def test_names_match_empty_names():
    """Empty or stop-word-only names don't match."""
    assert names_match("Hotel", "Hotel") is False
    assert names_match("", "Paraiso") is False
