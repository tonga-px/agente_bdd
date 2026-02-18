"""Tests for PerplexityService."""

import httpx
import pytest
import respx
from httpx import Response

from app.schemas.booking import BookingData
from app.services.perplexity import API_URL, PerplexityService


@pytest.fixture
def service():
    client = httpx.AsyncClient()
    return PerplexityService(client, "test-api-key")


def _perplexity_response(content: str) -> dict:
    """Build a minimal Perplexity API response."""
    return {
        "id": "test-id",
        "model": "sonar",
        "choices": [{"index": 0, "message": {"content": content}, "finish_reason": "stop"}],
    }


# --- search_booking_data ---


@respx.mock
@pytest.mark.asyncio
async def test_search_booking_data_success(service):
    content = '{"url": "https://www.booking.com/hotel/py/hub-hotel.html", "rating": 8.6, "review_count": 1234, "hotel_name": "The Hub Urban Hotel"}'
    respx.post(API_URL).mock(
        return_value=Response(200, json=_perplexity_response(content))
    )

    result = await service.search_booking_data("Hub Hotel", "Asunción", "Paraguay")

    assert result.url == "https://www.booking.com/hotel/py/hub-hotel.html"
    assert result.rating == 8.6
    assert result.review_count == 1234
    assert result.hotel_name == "The Hub Urban Hotel"


@respx.mock
@pytest.mark.asyncio
async def test_search_booking_data_not_found(service):
    content = '{"url": null, "rating": null, "review_count": null, "hotel_name": null}'
    respx.post(API_URL).mock(
        return_value=Response(200, json=_perplexity_response(content))
    )

    result = await service.search_booking_data("Nonexistent Hotel", "Nowhere", "Neverland")

    assert result.url is None
    assert result.rating is None
    assert result.review_count is None


@respx.mock
@pytest.mark.asyncio
async def test_search_booking_data_api_error(service):
    respx.post(API_URL).mock(return_value=Response(500, text="Internal Server Error"))

    result = await service.search_booking_data("Hotel Test", "Lima", "Peru")

    assert result == BookingData()


@respx.mock
@pytest.mark.asyncio
async def test_search_booking_data_timeout(service):
    respx.post(API_URL).mock(side_effect=httpx.ReadTimeout("timeout"))

    result = await service.search_booking_data("Hotel Test", "Lima", "Peru")

    assert result == BookingData()


@respx.mock
@pytest.mark.asyncio
async def test_search_booking_data_with_markdown_fences(service):
    """Perplexity sometimes wraps JSON in markdown code fences."""
    content = '```json\n{"url": "https://www.booking.com/hotel/ar/test.html", "rating": 7.5, "review_count": 500, "hotel_name": "Test Hotel"}\n```'
    respx.post(API_URL).mock(
        return_value=Response(200, json=_perplexity_response(content))
    )

    result = await service.search_booking_data("Test Hotel", "Buenos Aires", "Argentina")

    assert result.url == "https://www.booking.com/hotel/ar/test.html"
    assert result.rating == 7.5
    assert result.review_count == 500


@respx.mock
@pytest.mark.asyncio
async def test_search_booking_data_garbage_response(service):
    content = "I could not find any information about this hotel."
    respx.post(API_URL).mock(
        return_value=Response(200, json=_perplexity_response(content))
    )

    result = await service.search_booking_data("Unknown Hotel")

    assert result == BookingData()


@respx.mock
@pytest.mark.asyncio
async def test_search_booking_data_partial_data(service):
    """Only rating found, no URL."""
    content = '{"url": null, "rating": 8.0, "review_count": null, "hotel_name": "Hotel Parcial"}'
    respx.post(API_URL).mock(
        return_value=Response(200, json=_perplexity_response(content))
    )

    result = await service.search_booking_data("Hotel Parcial", "Lima", "Peru")

    assert result.url is None
    assert result.rating == 8.0
    assert result.review_count is None
    assert result.hotel_name == "Hotel Parcial"


@respx.mock
@pytest.mark.asyncio
async def test_search_booking_data_non_booking_url_rejected(service):
    """URL that's not booking.com is rejected."""
    content = '{"url": "https://www.tripadvisor.com/Hotel-123", "rating": 4.5, "review_count": 100, "hotel_name": "Hotel X"}'
    respx.post(API_URL).mock(
        return_value=Response(200, json=_perplexity_response(content))
    )

    result = await service.search_booking_data("Hotel X", "Lima", "Peru")

    assert result.url is None
    assert result.rating == 4.5


@respx.mock
@pytest.mark.asyncio
async def test_search_booking_data_no_city_country(service):
    """Search with only hotel name, no location."""
    content = '{"url": "https://www.booking.com/hotel/xx/test.html", "rating": 9.0, "review_count": 2000, "hotel_name": "Test"}'
    respx.post(API_URL).mock(
        return_value=Response(200, json=_perplexity_response(content))
    )

    result = await service.search_booking_data("Test Hotel")

    assert result.rating == 9.0

    # Verify the prompt doesn't contain "in" when no location
    req = respx.calls.last.request
    import json
    body = json.loads(req.content)
    user_msg = body["messages"][1]["content"]
    assert ' in ' not in user_msg or 'in {' in user_msg  # no location clause


@respx.mock
@pytest.mark.asyncio
async def test_search_booking_data_sends_correct_headers(service):
    content = '{"url": null, "rating": null, "review_count": null, "hotel_name": null}'
    respx.post(API_URL).mock(
        return_value=Response(200, json=_perplexity_response(content))
    )

    await service.search_booking_data("Test Hotel", "Lima", "Peru")

    req = respx.calls.last.request
    assert req.headers["authorization"] == "Bearer test-api-key"
    assert req.headers["content-type"] == "application/json"


# --- _try_parse_json ---


def test_try_parse_json_direct():
    result = PerplexityService._try_parse_json('{"key": "value"}')
    assert result == {"key": "value"}


def test_try_parse_json_with_surrounding_text():
    result = PerplexityService._try_parse_json('Here is the data: {"key": "value"} hope this helps')
    assert result == {"key": "value"}


def test_try_parse_json_markdown_fences():
    result = PerplexityService._try_parse_json('```json\n{"key": "value"}\n```')
    assert result == {"key": "value"}


def test_try_parse_json_no_json():
    result = PerplexityService._try_parse_json("no json here")
    assert result is None


def test_try_parse_json_empty():
    result = PerplexityService._try_parse_json("")
    assert result is None
