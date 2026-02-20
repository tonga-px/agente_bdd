"""Tests for TavilyService."""

import pytest
from unittest.mock import AsyncMock, patch

from app.schemas.booking import BookingData
from app.schemas.tavily import ReputationData
from app.schemas.website import WebScrapedData
from app.services.tavily import TavilyService


@pytest.fixture
def tavily_client_mock():
    return AsyncMock()


@pytest.fixture
def service(tavily_client_mock):
    with patch("app.services.tavily.AsyncTavilyClient", return_value=tavily_client_mock):
        svc = TavilyService(api_key="test-key")
    return svc


# --- extract_website tests ---


@pytest.mark.asyncio
async def test_extract_website_phones_and_emails(service, tavily_client_mock):
    """Extract returns phones, emails, whatsapp from website content."""
    tavily_client_mock.extract.return_value = {
        "results": [{
            "raw_content": (
                "Contacto: +54 11 5263 0435 Email: reservas@hotel.com "
                "WhatsApp: https://wa.me/5491123530759"
            ),
        }],
    }

    result = await service.extract_website("https://hotel.com")

    assert isinstance(result, WebScrapedData)
    assert "+541152630435" in result.phones
    assert "reservas@hotel.com" in result.emails
    assert result.whatsapp == "+5491123530759"
    assert result.source_url == "https://hotel.com"


@pytest.mark.asyncio
async def test_extract_website_empty_result(service, tavily_client_mock):
    """Extract with empty results returns empty WebScrapedData."""
    tavily_client_mock.extract.return_value = {"results": []}

    result = await service.extract_website("https://hotel.com")

    assert result.phones == []
    assert result.emails == []
    assert result.whatsapp is None
    assert result.source_url == "https://hotel.com"


@pytest.mark.asyncio
async def test_extract_website_fallback_contact_page(service, tavily_client_mock):
    """No emails on main page → tries /contacto, /contact pages."""
    call_count = 0

    async def _extract_side_effect(urls, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Main page: phone but no email
            return {"results": [{"raw_content": "Tel: +541199887766"}]}
        elif call_count == 2:
            # /contacto page: has email
            return {"results": [{"raw_content": "Email: info@hotel.com"}]}
        return {"results": []}

    tavily_client_mock.extract.side_effect = _extract_side_effect

    result = await service.extract_website("https://hotel.com")

    assert "+541199887766" in result.phones
    assert "info@hotel.com" in result.emails
    assert call_count == 2  # main + /contacto


@pytest.mark.asyncio
async def test_extract_website_api_error_graceful(service, tavily_client_mock):
    """API error returns empty WebScrapedData (graceful degradation)."""
    tavily_client_mock.extract.side_effect = Exception("API down")

    result = await service.extract_website("https://hotel.com")

    assert result.phones == []
    assert result.emails == []
    assert result.source_url == "https://hotel.com"


@pytest.mark.asyncio
async def test_extract_website_no_raw_content(service, tavily_client_mock):
    """Result with missing raw_content field returns empty data."""
    tavily_client_mock.extract.return_value = {
        "results": [{"url": "https://hotel.com"}],
    }

    result = await service.extract_website("https://hotel.com")

    assert result.phones == []
    assert result.emails == []


@pytest.mark.asyncio
async def test_extract_website_blocks_bad_emails(service, tavily_client_mock):
    """Blocked email domains (google.com, etc.) are filtered out."""
    tavily_client_mock.extract.return_value = {
        "results": [{
            "raw_content": "user@google.com noreply@hotel.com reservas@hotel.com",
        }],
    }

    result = await service.extract_website("https://hotel.com")

    assert "user@google.com" not in result.emails
    assert "reservas@hotel.com" in result.emails


# --- search_booking_data tests ---


@pytest.mark.asyncio
async def test_search_booking_data_success(service, tavily_client_mock):
    """Booking search returns URL, rating, review count."""
    tavily_client_mock.search.return_value = {
        "results": [{
            "url": "https://www.booking.com/hotel/ar/test.html",
            "title": "Hotel Test Mendoza | Booking.com",
            "content": "Rating: 8.4/10 based on 1,234 reviews. Great location.",
        }],
    }

    result = await service.search_booking_data("Hotel Test", "Mendoza", "Argentina")

    assert isinstance(result, BookingData)
    assert result.url == "https://www.booking.com/hotel/ar/test.html"
    assert result.rating == 8.4
    assert result.review_count == 1234
    assert result.hotel_name == "Hotel Test Mendoza"


@pytest.mark.asyncio
async def test_search_booking_data_no_results(service, tavily_client_mock):
    """No Booking results returns empty BookingData."""
    tavily_client_mock.search.return_value = {"results": []}

    result = await service.search_booking_data("Hotel Fake", "Nowhere")

    assert isinstance(result, BookingData)
    assert result.url is None
    assert result.rating is None


@pytest.mark.asyncio
async def test_search_booking_data_api_error(service, tavily_client_mock):
    """API error returns empty BookingData (graceful degradation)."""
    tavily_client_mock.search.side_effect = Exception("API down")

    result = await service.search_booking_data("Hotel Test")

    assert isinstance(result, BookingData)
    assert result.url is None


@pytest.mark.asyncio
async def test_search_booking_data_non_booking_url_ignored(service, tavily_client_mock):
    """URL not from booking.com is not used."""
    tavily_client_mock.search.return_value = {
        "results": [{
            "url": "https://www.tripadvisor.com/hotel/test",
            "title": "Hotel Test",
            "content": "Some review content",
        }],
    }

    result = await service.search_booking_data("Hotel Test")

    assert result.url is None


# --- search_room_count tests ---


@pytest.mark.asyncio
async def test_search_room_count_from_answer(service, tavily_client_mock):
    """Room count found in Tavily answer."""
    tavily_client_mock.search.return_value = {
        "answer": "El Hotel Sol tiene 45 habitaciones y está ubicado en Lima.",
        "results": [],
    }

    result = await service.search_room_count("Hotel Sol", "Lima", "Peru")

    assert result == "45"


@pytest.mark.asyncio
async def test_search_room_count_from_content(service, tavily_client_mock):
    """Room count found in result content (fallback)."""
    tavily_client_mock.search.return_value = {
        "answer": "No specific information found.",
        "results": [{
            "content": "The hotel features 22 rooms with ocean views.",
        }],
    }

    result = await service.search_room_count("Hotel Mar", "Cancun")

    assert result == "22"


@pytest.mark.asyncio
async def test_search_room_count_rooms_english(service, tavily_client_mock):
    """Room count with English 'rooms' keyword."""
    tavily_client_mock.search.return_value = {
        "answer": "This boutique hotel has 15 rooms.",
        "results": [],
    }

    result = await service.search_room_count("Hotel Boutique", "Lima")

    assert result == "15"


@pytest.mark.asyncio
async def test_search_room_count_none(service, tavily_client_mock):
    """No room info found → None."""
    tavily_client_mock.search.return_value = {
        "answer": "Hotel Sol is a popular hotel in Lima.",
        "results": [{
            "content": "Great service and location.",
        }],
    }

    result = await service.search_room_count("Hotel Sol", "Lima")

    assert result is None


@pytest.mark.asyncio
async def test_search_room_count_api_error(service, tavily_client_mock):
    """API error → None (graceful degradation)."""
    tavily_client_mock.search.side_effect = Exception("API down")

    result = await service.search_room_count("Hotel Sol")

    assert result is None


# --- search_reputation tests ---


@pytest.mark.asyncio
async def test_search_reputation_full(service, tavily_client_mock):
    """Reputation search extracts Google, TripAdvisor, Booking ratings."""
    tavily_client_mock.search.return_value = {
        "answer": "Hotel Sol has excellent reviews across platforms.",
        "results": [{
            "content": (
                "Google rating: 4.3/5 with 1,234 reviews. "
                "TripAdvisor rating: 4.5/5 with 3,566 reviews. "
                "Booking rating: 8.4/10 with 2,100 reviews."
            ),
        }],
    }

    result = await service.search_reputation("Hotel Sol", "Lima", "Peru")

    assert isinstance(result, ReputationData)
    assert result.google_rating == 4.3
    assert result.google_review_count == 1234
    assert result.tripadvisor_rating == 4.5
    assert result.tripadvisor_review_count == 3566
    assert result.booking_rating == 8.4
    assert result.booking_review_count == 2100
    assert result.summary is not None


@pytest.mark.asyncio
async def test_search_reputation_partial(service, tavily_client_mock):
    """Only Google data available → returns partial ReputationData."""
    tavily_client_mock.search.return_value = {
        "answer": "The hotel has a Google rating of 4.2/5.",
        "results": [{
            "content": "Google reviews show 4.2/5 with 500 reviews.",
        }],
    }

    result = await service.search_reputation("Hotel Test", "Lima")

    assert result is not None
    assert result.google_rating == 4.2
    assert result.tripadvisor_rating is None
    assert result.booking_rating is None


@pytest.mark.asyncio
async def test_search_reputation_no_data(service, tavily_client_mock):
    """No rating data found → None."""
    tavily_client_mock.search.return_value = {
        "answer": "",
        "results": [{
            "content": "General information about the hotel.",
        }],
    }

    result = await service.search_reputation("Hotel Unknown")

    assert result is None


@pytest.mark.asyncio
async def test_search_reputation_api_error(service, tavily_client_mock):
    """API error → None (graceful degradation)."""
    tavily_client_mock.search.side_effect = Exception("API down")

    result = await service.search_reputation("Hotel Sol")

    assert result is None


@pytest.mark.asyncio
async def test_search_reputation_answer_only(service, tavily_client_mock):
    """Only Tavily answer (no structured data in content) → returns summary."""
    tavily_client_mock.search.return_value = {
        "answer": "Hotel Sol has good Google reviews with 4.1/5 rating.",
        "results": [],
    }

    result = await service.search_reputation("Hotel Sol", "Lima")

    assert result is not None
    assert result.google_rating == 4.1
    assert result.summary is not None


# --- extract_instagram_profile tests ---


@pytest.mark.asyncio
async def test_extract_instagram_profile_success(service, tavily_client_mock):
    """Extract returns raw text from Instagram profile."""
    tavily_client_mock.extract.return_value = {
        "results": [{"raw_content": "Hotel Sol\nBio text\n1,500 followers"}],
    }

    result = await service.extract_instagram_profile("https://www.instagram.com/hotelsol/")

    assert result is not None
    assert "Hotel Sol" in result
    assert "1,500 followers" in result


@pytest.mark.asyncio
async def test_extract_instagram_profile_empty(service, tavily_client_mock):
    """Extract returns no results → None."""
    tavily_client_mock.extract.return_value = {"results": []}

    result = await service.extract_instagram_profile("https://www.instagram.com/hotelsol/")

    assert result is None


@pytest.mark.asyncio
async def test_extract_instagram_profile_no_content(service, tavily_client_mock):
    """Extract result has no raw_content → None."""
    tavily_client_mock.extract.return_value = {
        "results": [{"url": "https://www.instagram.com/hotelsol/"}],
    }

    result = await service.extract_instagram_profile("https://www.instagram.com/hotelsol/")

    assert result is None


@pytest.mark.asyncio
async def test_extract_instagram_profile_api_error(service, tavily_client_mock):
    """API error → None (graceful degradation)."""
    tavily_client_mock.extract.side_effect = Exception("API down")

    result = await service.extract_instagram_profile("https://www.instagram.com/hotelsol/")

    assert result is None


# --- search_instagram_profile tests ---


@pytest.mark.asyncio
async def test_search_instagram_profile_success(service, tavily_client_mock):
    """Search returns combined answer + content."""
    tavily_client_mock.search.return_value = {
        "answer": "Hotel Sol is a boutique hotel in Lima.",
        "results": [
            {"content": "Hotel Sol (@hotelsol) · 1,500 followers"},
            {"content": "Reservas: +51 1 234 5678"},
        ],
    }

    result = await service.search_instagram_profile("hotelsol", "Hotel Sol", "Lima")

    assert result is not None
    assert "Hotel Sol" in result
    assert "1,500 followers" in result
    assert "+51 1 234 5678" in result


@pytest.mark.asyncio
async def test_search_instagram_profile_answer_only(service, tavily_client_mock):
    """Search returns only answer (no results) → still returns text."""
    tavily_client_mock.search.return_value = {
        "answer": "Hotel Sol has 500 followers and is a boutique hotel.",
        "results": [],
    }

    result = await service.search_instagram_profile("hotelsol")

    assert result is not None
    assert "Hotel Sol" in result


@pytest.mark.asyncio
async def test_search_instagram_profile_no_data(service, tavily_client_mock):
    """Search returns empty → None."""
    tavily_client_mock.search.return_value = {
        "answer": "",
        "results": [],
    }

    result = await service.search_instagram_profile("hotelsol")

    assert result is None


@pytest.mark.asyncio
async def test_search_instagram_profile_api_error(service, tavily_client_mock):
    """API error → None (graceful degradation)."""
    tavily_client_mock.search.side_effect = Exception("API down")

    result = await service.search_instagram_profile("hotelsol")

    assert result is None


@pytest.mark.asyncio
async def test_search_instagram_profile_uses_advanced_search(service, tavily_client_mock):
    """Search uses advanced depth and profile URL in query."""
    tavily_client_mock.search.return_value = {
        "answer": "Some data",
        "results": [],
    }

    await service.search_instagram_profile("hotelsol", "Hotel Sol", "Lima")

    tavily_client_mock.search.assert_awaited_once()
    call_kwargs = tavily_client_mock.search.call_args
    assert "@hotelsol" in call_kwargs.kwargs.get("query", "")
    assert call_kwargs.kwargs.get("search_depth") == "advanced"
    assert call_kwargs.kwargs.get("include_domains") == ["instagram.com"]


# --- extract_website: instagram_url detection ---


@pytest.mark.asyncio
async def test_extract_website_finds_instagram_url(service, tavily_client_mock):
    """Instagram profile URL in website content is extracted."""
    tavily_client_mock.extract.return_value = {
        "results": [{
            "raw_content": (
                "Síguenos en https://www.instagram.com/hotelvillamansa "
                "Tel: +54 11 1234 5678"
            ),
        }],
    }

    result = await service.extract_website("https://villamansa.com")

    assert result.instagram_url == "https://www.instagram.com/hotelvillamansa"


@pytest.mark.asyncio
async def test_extract_website_ignores_non_profile_instagram_paths(service, tavily_client_mock):
    """Non-profile Instagram paths (reel, stories, explore, etc.) are ignored."""
    tavily_client_mock.extract.return_value = {
        "results": [{
            "raw_content": (
                "Check our https://www.instagram.com/reel/ABC123 "
                "and https://instagram.com/explore"
            ),
        }],
    }

    result = await service.extract_website("https://hotel.com")

    assert result.instagram_url is None


@pytest.mark.asyncio
async def test_extract_website_instagram_url_first_valid(service, tavily_client_mock):
    """Multiple Instagram URLs → first valid profile is used."""
    tavily_client_mock.extract.return_value = {
        "results": [{
            "raw_content": (
                "See https://instagram.com/p/XYZ "
                "Follow us https://instagram.com/hotel_real "
                "Also https://instagram.com/other_hotel"
            ),
        }],
    }

    result = await service.extract_website("https://hotel.com")

    assert result.instagram_url == "https://instagram.com/hotel_real"


@pytest.mark.asyncio
async def test_extract_website_no_instagram_url(service, tavily_client_mock):
    """No Instagram URL in content → instagram_url is None."""
    tavily_client_mock.extract.return_value = {
        "results": [{
            "raw_content": "Tel: +54 11 1234 5678 Email: info@hotel.com",
        }],
    }

    result = await service.extract_website("https://hotel.com")

    assert result.instagram_url is None


# --- search_instagram_url tests ---


@pytest.mark.asyncio
async def test_search_instagram_url_finds_profile(service, tavily_client_mock):
    """Search returns Instagram profile URL from results."""
    tavily_client_mock.search.return_value = {
        "results": [
            {"url": "https://www.instagram.com/villamansah/reels/?hl=en", "content": "..."},
            {"url": "https://www.instagram.com/villamansah/?hl=en", "content": "..."},
        ],
    }

    result = await service.search_instagram_url("https://villamansa.com")

    assert result == "https://www.instagram.com/villamansah"


@pytest.mark.asyncio
async def test_search_instagram_url_skips_non_profile(service, tavily_client_mock):
    """Non-profile paths (explore, reel, p) are skipped."""
    tavily_client_mock.search.return_value = {
        "results": [
            {"url": "https://www.instagram.com/explore/locations/123/", "content": "..."},
            {"url": "https://www.instagram.com/p/ABC123/", "content": "..."},
            {"url": "https://www.instagram.com/hotelreal/", "content": "..."},
        ],
    }

    result = await service.search_instagram_url("https://hotel.com")

    assert result == "https://www.instagram.com/hotelreal"


@pytest.mark.asyncio
async def test_search_instagram_url_no_results(service, tavily_client_mock):
    """No results → None."""
    tavily_client_mock.search.return_value = {"results": []}

    result = await service.search_instagram_url("https://hotel.com")

    assert result is None


@pytest.mark.asyncio
async def test_search_instagram_url_api_error(service, tavily_client_mock):
    """API error → None (graceful degradation)."""
    tavily_client_mock.search.side_effect = Exception("API down")

    result = await service.search_instagram_url("https://hotel.com")

    assert result is None


# --- search_hoteles_data tests ---


@pytest.mark.asyncio
async def test_search_hoteles_data_success(service, tavily_client_mock):
    """Hoteles.com search returns combined answer + content."""
    tavily_client_mock.search.return_value = {
        "answer": "Hotel Sol is a 4-star hotel with 25 rooms.",
        "results": [
            {"content": "Hotel Sol - Reviews and prices on hoteles.com"},
        ],
    }

    result = await service.search_hoteles_data("Hotel Sol", "Lima", "Peru")

    assert result is not None
    assert "Hotel Sol" in result
    assert "25 rooms" in result
    tavily_client_mock.search.assert_awaited_once()
    call_kwargs = tavily_client_mock.search.call_args
    assert call_kwargs.kwargs.get("include_domains") == ["hoteles.com"]


@pytest.mark.asyncio
async def test_search_hoteles_data_no_results(service, tavily_client_mock):
    """No results → None."""
    tavily_client_mock.search.return_value = {
        "answer": "",
        "results": [],
    }

    result = await service.search_hoteles_data("Hotel Fake")

    assert result is None


@pytest.mark.asyncio
async def test_search_hoteles_data_api_error(service, tavily_client_mock):
    """API error → None (graceful degradation)."""
    tavily_client_mock.search.side_effect = Exception("API down")

    result = await service.search_hoteles_data("Hotel Sol")

    assert result is None


# --- scrape_booking_page tests ---


@pytest.mark.asyncio
async def test_scrape_booking_page_success(service, tavily_client_mock):
    """Extract Booking page and parse room types, rate, reviews."""
    tavily_client_mock.extract.return_value = {
        "results": [{
            "raw_content": (
                "Hotel Sol\n"
                "Habitación Doble Estándar\n"
                "Suite Junior con Vista\n"
                "Habitación Familiar\n"
                "Precio desde US$85 por noche. "
                "Basado en 1,234 reviews de huéspedes."
            ),
        }],
    }

    result = await service.scrape_booking_page(
        "Hotel Sol", "Lima", "Peru",
        known_url="https://www.booking.com/hotel/pe/sol.html",
    )

    assert result is not None
    assert result.source == "Booking.com"
    assert result.room_types is not None
    assert len(result.room_types) == 3
    assert "Suite Junior con Vista" in result.room_types
    assert result.nightly_rate_usd == "US$85"
    assert result.review_count == 1234
    assert result.url == "https://www.booking.com/hotel/pe/sol.html"


@pytest.mark.asyncio
async def test_scrape_booking_page_partial_data(service, tavily_client_mock):
    """Only some fields extracted from Booking page."""
    tavily_client_mock.extract.return_value = {
        "results": [{
            "raw_content": "Habitación Deluxe\n2 camas. Central location.",
        }],
    }

    result = await service.scrape_booking_page(
        "Hotel Test", "Lima", "Peru",
        known_url="https://www.booking.com/hotel/pe/test.html",
    )

    assert result is not None
    assert result.room_types == ["Habitación Deluxe"]
    assert result.nightly_rate_usd is None
    assert result.review_count is None


@pytest.mark.asyncio
async def test_scrape_booking_page_no_content(service, tavily_client_mock):
    """No content from extract → None."""
    tavily_client_mock.extract.return_value = {"results": []}

    result = await service.scrape_booking_page(
        "Hotel Test", "Lima", "Peru",
        known_url="https://www.booking.com/hotel/pe/test.html",
    )

    assert result is None


@pytest.mark.asyncio
async def test_scrape_booking_page_api_error(service, tavily_client_mock):
    """API error → None (graceful degradation)."""
    tavily_client_mock.extract.side_effect = Exception("API down")

    result = await service.scrape_booking_page(
        "Hotel Test", "Lima", "Peru",
        known_url="https://www.booking.com/hotel/pe/test.html",
    )

    assert result is None


@pytest.mark.asyncio
async def test_scrape_booking_page_discovers_url(service, tavily_client_mock):
    """No known_url → discovers URL via search, then extracts page."""
    tavily_client_mock.search.return_value = {
        "results": [{
            "url": "https://www.booking.com/hotel/pe/sol.html",
            "content": "Hotel Sol en Lima",
        }],
    }
    tavily_client_mock.extract.return_value = {
        "results": [{
            "raw_content": (
                "Habitación Doble Estándar\n"
                "Suite Junior\n"
                "From US$90 per night. 500 reviews."
            ),
        }],
    }

    result = await service.scrape_booking_page("Hotel Sol", "Lima", "Peru")

    assert result is not None
    assert result.source == "Booking.com"
    assert result.url == "https://www.booking.com/hotel/pe/sol.html"
    assert result.room_types is not None
    assert len(result.room_types) == 2
    tavily_client_mock.search.assert_awaited_once()
    call_kwargs = tavily_client_mock.search.call_args.kwargs
    assert call_kwargs.get("include_domains") == ["booking.com"]


@pytest.mark.asyncio
async def test_scrape_booking_page_discover_no_results(service, tavily_client_mock):
    """No known_url and search finds nothing → None."""
    tavily_client_mock.search.return_value = {"results": []}

    result = await service.scrape_booking_page("Hotel Fake", "Nowhere")

    assert result is None


# --- scrape_hoteles_page tests ---


@pytest.mark.asyncio
async def test_scrape_hoteles_page_success(service, tavily_client_mock):
    """Search + extract hoteles.com page."""
    call_count = 0

    async def _mock_search(**kwargs):
        return {
            "results": [{
                "url": "https://www.hoteles.com/ho123/hotel-sol/",
                "content": "Hotel Sol en Lima",
            }],
        }

    async def _mock_extract(urls, **kwargs):
        nonlocal call_count
        call_count += 1
        return {
            "results": [{
                "raw_content": (
                    "Hotel Sol\n"
                    "Habitación Standard\n2 beds\n"
                    "Suite Deluxe\n1 king bed\n"
                    "From US$65 per night. "
                    "Based on 567 opiniones."
                ),
            }],
        }

    tavily_client_mock.search.side_effect = _mock_search
    tavily_client_mock.extract.side_effect = _mock_extract

    result = await service.scrape_hoteles_page("Hotel Sol", "Lima", "Peru")

    assert result is not None
    assert result.source == "Hoteles.com"
    assert result.room_types is not None
    assert len(result.room_types) == 2
    assert "Habitación Standard" in result.room_types
    assert "Suite Deluxe" in result.room_types
    assert result.nightly_rate_usd == "US$65"
    assert result.review_count == 567
    assert "hoteles.com" in result.url


@pytest.mark.asyncio
async def test_scrape_hoteles_page_no_results(service, tavily_client_mock):
    """No search results → None."""
    tavily_client_mock.search.return_value = {"results": []}

    result = await service.scrape_hoteles_page("Hotel Fake")

    assert result is None


@pytest.mark.asyncio
async def test_scrape_hoteles_page_fallback_to_content(service, tavily_client_mock):
    """Extract fails → falls back to search result content."""
    async def _mock_search(**kwargs):
        return {
            "results": [{
                "url": "https://www.hoteles.com/ho123/hotel-sol/",
                "content": "Hotel Sol. Habitación Doble, Suite Premium. From US$50 per night, 200 reviews.",
            }],
        }

    async def _mock_extract(urls, **kwargs):
        return {"results": []}  # No content extracted

    tavily_client_mock.search.side_effect = _mock_search
    tavily_client_mock.extract.side_effect = _mock_extract

    result = await service.scrape_hoteles_page("Hotel Sol", "Lima")

    assert result is not None
    assert result.room_types is not None
    assert "Suite Premium" in result.room_types
    assert result.nightly_rate_usd == "US$50"


@pytest.mark.asyncio
async def test_scrape_hoteles_page_api_error(service, tavily_client_mock):
    """API error → None (graceful degradation)."""
    tavily_client_mock.search.side_effect = Exception("API down")

    result = await service.scrape_hoteles_page("Hotel Sol")

    assert result is None


# --- _parse_listing_data unit tests ---


def test_parse_listing_room_types():
    """Parse room type names from text."""
    from app.services.tavily import TavilyService
    result = TavilyService._parse_listing_data(
        "Habitación Doble Estándar\n2 camas\nSuite Junior\n1 cama king",
        "Test", None,
    )
    assert result.room_types is not None
    assert len(result.room_types) == 2
    assert "Suite Junior" in result.room_types


def test_parse_listing_rate_usd_prefix():
    """Parse rate with US$ prefix."""
    from app.services.tavily import TavilyService
    result = TavilyService._parse_listing_data(
        "Price from US$120 per night.", "Test", None,
    )
    assert result.nightly_rate_usd == "US$120"


def test_parse_listing_rate_dollar_per_night():
    """Parse rate with $ per night pattern."""
    from app.services.tavily import TavilyService
    result = TavilyService._parse_listing_data(
        "Tarifa: $85 por noche.", "Test", None,
    )
    assert result.nightly_rate_usd == "US$85"


def test_parse_listing_reviews():
    """Parse review count from text."""
    from app.services.tavily import TavilyService
    result = TavilyService._parse_listing_data(
        "Based on 2,345 reviews from guests.", "Test", None,
    )
    assert result.review_count == 2345


def test_parse_listing_no_data():
    """No structured data → all fields None."""
    from app.services.tavily import TavilyService
    result = TavilyService._parse_listing_data(
        "A beautiful hotel in the heart of the city.", "Test", None,
    )
    assert result.room_types is None
    assert result.nightly_rate_usd is None
    assert result.review_count is None


def test_parse_listing_deduplicates_room_types():
    """Duplicate room type names are removed."""
    from app.services.tavily import TavilyService
    result = TavilyService._parse_listing_data(
        "Suite Deluxe\n2 camas\nSuite Deluxe\n1 cama king",
        "Test", None,
    )
    assert result.room_types == ["Suite Deluxe"]
