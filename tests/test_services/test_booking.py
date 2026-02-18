"""Tests for BookingScraperService."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from httpx import Response

from app.schemas.booking import BookingData
from app.services.booking import BookingScraperService


@pytest.fixture
def client():
    return httpx.AsyncClient()


@pytest.fixture
def service(client):
    return BookingScraperService(client)


# --- _extract_booking_url_from_html ---


def test_extract_url_from_html(service):
    html = '<a href="https://www.booking.com/hotel/ar/diplomatic-mendoza.html">Book</a>'
    url = service._extract_booking_url_from_html(html)
    assert url == "https://www.booking.com/hotel/ar/diplomatic-mendoza.html"


def test_extract_url_no_match(service):
    html = '<a href="https://www.tripadvisor.com/Hotel-123">Review</a>'
    assert service._extract_booking_url_from_html(html) is None


def test_extract_url_different_formats(service):
    html = 'href="https://booking.com/hotel/es/hotel-madrid.en-gb.html?aid=123"'
    url = service._extract_booking_url_from_html(html)
    assert url is not None
    assert "booking.com/hotel/es/hotel-madrid" in url


def test_extract_url_strips_trailing_quote(service):
    html = "src='https://www.booking.com/hotel/cl/test-hotel.html'"
    url = service._extract_booking_url_from_html(html)
    assert url.endswith(".html")


# --- _parse_booking_html ---


def _make_jsonld_html(ld_data):
    """Wrap JSON-LD data in a minimal HTML page."""
    return f"""
    <html><head>
    <script type="application/ld+json">{json.dumps(ld_data)}</script>
    </head><body></body></html>
    """


def test_parse_hotel_jsonld(service):
    ld = {
        "@type": "Hotel",
        "name": "Hotel Mendoza",
        "aggregateRating": {
            "ratingValue": 8.4,
            "reviewCount": 1567,
        },
        "priceRange": "$$$",
    }
    result = service._parse_booking_html(_make_jsonld_html(ld), "https://booking.com/hotel/ar/test")
    assert result.hotel_name == "Hotel Mendoza"
    assert result.rating == 8.4
    assert result.review_count == 1567
    assert result.price_range == "$$$"
    assert result.url == "https://booking.com/hotel/ar/test"


def test_parse_jsonld_array(service):
    ld = [
        {"@type": "BreadcrumbList", "name": "nav"},
        {
            "@type": "Hotel",
            "name": "Hotel Array",
            "aggregateRating": {"ratingValue": 7.2, "reviewCount": 300},
        },
    ]
    result = service._parse_booking_html(_make_jsonld_html(ld), "https://booking.com/hotel/ar/x")
    assert result.hotel_name == "Hotel Array"
    assert result.rating == 7.2
    assert result.review_count == 300


def test_parse_lodging_business_type(service):
    ld = {"@type": "LodgingBusiness", "name": "Hostel Central"}
    result = service._parse_booking_html(_make_jsonld_html(ld), "https://booking.com/hotel/ar/x")
    assert result.hotel_name == "Hostel Central"


def test_parse_no_jsonld_fallback_og_title(service):
    html = """
    <html><head>
    <meta property="og:title" content="Grand Hotel Buenos Aires" />
    </head><body></body></html>
    """
    result = service._parse_booking_html(html, "https://booking.com/hotel/ar/grand")
    assert result.hotel_name == "Grand Hotel Buenos Aires"
    assert result.rating is None


def test_parse_empty_html(service):
    result = service._parse_booking_html("<html><body></body></html>", "https://booking.com/hotel/ar/x")
    assert result.hotel_name is None
    assert result.rating is None
    assert result.url == "https://booking.com/hotel/ar/x"


def test_parse_invalid_json(service):
    html = '<html><head><script type="application/ld+json">not valid json</script></head></html>'
    result = service._parse_booking_html(html, "https://booking.com/hotel/ar/x")
    assert result.rating is None


def test_parse_missing_rating_fields(service):
    ld = {"@type": "Hotel", "name": "Minimal Hotel"}
    result = service._parse_booking_html(_make_jsonld_html(ld), "https://booking.com/hotel/ar/x")
    assert result.hotel_name == "Minimal Hotel"
    assert result.rating is None
    assert result.review_count is None


# --- _scrape_booking_page ---


@pytest.mark.asyncio
@respx.mock
async def test_scrape_page_success(service):
    respx.get("https://www.booking.com/hotel/ar/test.html").mock(
        return_value=Response(200, text="<html>ok</html>", headers={"content-type": "text/html"})
    )
    html = await service._scrape_booking_page("https://www.booking.com/hotel/ar/test.html")
    assert html == "<html>ok</html>"


@pytest.mark.asyncio
@respx.mock
async def test_scrape_page_http_error(service):
    respx.get("https://www.booking.com/hotel/ar/bad.html").mock(
        return_value=Response(403)
    )
    html = await service._scrape_booking_page("https://www.booking.com/hotel/ar/bad.html")
    assert html is None


@pytest.mark.asyncio
@respx.mock
async def test_scrape_page_not_html(service):
    respx.get("https://www.booking.com/hotel/ar/pdf.html").mock(
        return_value=Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
    )
    html = await service._scrape_booking_page("https://www.booking.com/hotel/ar/pdf.html")
    assert html is None


# --- search_and_scrape integration ---


@pytest.mark.asyncio
@respx.mock
async def test_search_and_scrape_tier1_website_html(service):
    """Tier 1: Booking URL found in hotel website HTML → scrape it."""
    website_html = '<a href="https://www.booking.com/hotel/ar/mendoza-hotel.html">Booking</a>'

    ld = {"@type": "Hotel", "name": "Mendoza Hotel", "aggregateRating": {"ratingValue": 8.0, "reviewCount": 500}}
    booking_html = _make_jsonld_html(ld)

    respx.get("https://www.booking.com/hotel/ar/mendoza-hotel.html").mock(
        return_value=Response(200, text=booking_html, headers={"content-type": "text/html"})
    )

    result = await service.search_and_scrape("Mendoza Hotel", "Mendoza", "Argentina", website_html)
    assert result.hotel_name == "Mendoza Hotel"
    assert result.rating == 8.0
    assert result.review_count == 500


@pytest.mark.asyncio
async def test_search_and_scrape_tier2_ddg(service):
    """Tier 2: DDG search finds URL → scrape it."""
    ddg_results = [{"href": "https://www.booking.com/hotel/cl/santiago-hotel.html", "title": "Santiago Hotel"}]

    with patch("app.services.booking.BookingScraperService._search_booking_url", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = "https://www.booking.com/hotel/cl/santiago-hotel.html"

        with patch.object(service, "_scrape_booking_page", new_callable=AsyncMock) as mock_scrape:
            ld = {"@type": "Hotel", "name": "Santiago Hotel", "aggregateRating": {"ratingValue": 7.5, "reviewCount": 200}}
            mock_scrape.return_value = _make_jsonld_html(ld)

            result = await service.search_and_scrape("Santiago Hotel", "Santiago", "Chile", website_html=None)

    assert result.hotel_name == "Santiago Hotel"
    assert result.rating == 7.5


@pytest.mark.asyncio
async def test_search_and_scrape_no_url_returns_empty(service):
    """No URL found at all → empty BookingData."""
    with patch.object(service, "_search_booking_url", new_callable=AsyncMock, return_value=None):
        result = await service.search_and_scrape("Unknown Hotel", None, None, website_html=None)
    assert result.url is None
    assert result.rating is None


@pytest.mark.asyncio
async def test_search_and_scrape_exception_returns_empty(service):
    """Exception during scrape → returns empty BookingData (never raises)."""
    with patch.object(service, "_do_search_and_scrape", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        result = await service.search_and_scrape("Crash Hotel", None, None)
    assert result == BookingData()


# --- _search_booking_url ---


@pytest.mark.asyncio
async def test_search_booking_url_no_library(service):
    """If duckduckgo-search is not installed, returns None."""
    with patch.dict("sys.modules", {"duckduckgo_search": None}):
        with patch("builtins.__import__", side_effect=ImportError("no module")):
            result = await service._search_booking_url("Test", None, None)
    assert result is None


@pytest.mark.asyncio
async def test_search_booking_url_filters_non_hotel(service):
    """DDG results without booking.com/hotel/ are skipped."""
    fake_results = [
        {"href": "https://www.booking.com/city/ar/mendoza.html"},
        {"href": "https://www.booking.com/hotel/ar/real-hotel.html"},
    ]

    with patch("app.services.booking.asyncio.to_thread", new_callable=AsyncMock, return_value=fake_results):
        result = await service._search_booking_url("Real Hotel", "Mendoza", "Argentina")

    assert result == "https://www.booking.com/hotel/ar/real-hotel.html"
