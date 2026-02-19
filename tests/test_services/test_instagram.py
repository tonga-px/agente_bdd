"""Tests for InstagramService (Tavily-based)."""

import httpx
import pytest
import respx
from httpx import Response
from unittest.mock import AsyncMock

from app.schemas.instagram import InstagramData
from app.services.instagram import (
    InstagramService,
    is_instagram_url,
    _extract_username,
    _extract_phones,
    _extract_emails,
    _parse_follower_count,
    _parse_profile_text,
)


# --- Sample profile text for testing ---

_SAMPLE_PROFILE_TEXT = """\
Hotel Itapúa
🏨 Hotel boutique en Asunción
Reservas: +595 21 123 4567
reservas@hotelitapua.com
1,500 followers · 200 posts
https://wa.me/595981654321
https://www.hotelitapua.com
"""

_SAMPLE_PROFILE_MINIMAL = "Hotel Itapúa\nAsunción"


@pytest.fixture
def tavily_mock():
    return AsyncMock()


@pytest.fixture
def client():
    return httpx.AsyncClient()


@pytest.fixture
def service(tavily_mock, client):
    return InstagramService(tavily_mock, client)


# --- is_instagram_url ---


def test_is_instagram_url_true():
    assert is_instagram_url("https://www.instagram.com/hotelitapua/") is True
    assert is_instagram_url("https://instagram.com/hotelitapua") is True
    assert is_instagram_url("http://www.instagram.com/hotelitapua/") is True


def test_is_instagram_url_false():
    assert is_instagram_url("https://www.booking.com/hotel/x") is False
    assert is_instagram_url("https://example.com") is False
    assert is_instagram_url("") is False


# --- _extract_username ---


def test_extract_username_basic():
    assert _extract_username("https://www.instagram.com/hotelitapua/") == "hotelitapua"


def test_extract_username_no_trailing_slash():
    assert _extract_username("https://www.instagram.com/hotel_test") == "hotel_test"


def test_extract_username_with_query():
    assert _extract_username("https://www.instagram.com/myhotel?hl=es") == "myhotel"


def test_extract_username_skips_non_profile():
    assert _extract_username("https://www.instagram.com/p/ABC123/") is None
    assert _extract_username("https://www.instagram.com/reel/XYZ/") is None
    assert _extract_username("https://www.instagram.com/explore/") is None


def test_extract_username_invalid():
    assert _extract_username("https://www.booking.com/hotel") is None


# --- _extract_phones / _extract_emails ---


def test_extract_phones_from_bio():
    phones = _extract_phones("Tel: +595 21 123 4567 / +595 981 654 321", None)
    assert "+595211234567" in phones
    assert "+595981654321" in phones


def test_extract_phones_dedup_business():
    phones = _extract_phones("Tel: +595 21 123 4567", "+595211234567")
    assert phones == []


def test_extract_emails_from_bio():
    emails = _extract_emails("Reservas: reservas@hotel.com | info@hotel.com", None)
    assert "reservas@hotel.com" in emails
    assert "info@hotel.com" in emails


def test_extract_emails_dedup_business():
    emails = _extract_emails("Email: contact@hotel.com", "contact@hotel.com")
    assert emails == []


def test_extract_emails_blocks_instagram_domain():
    emails = _extract_emails("noreply@instagram.com real@hotel.com", None)
    assert "noreply@instagram.com" not in emails
    assert "real@hotel.com" in emails


# --- _parse_follower_count ---


def test_parse_follower_count_plain():
    assert _parse_follower_count("1,500 followers") == 1500


def test_parse_follower_count_k():
    assert _parse_follower_count("1.5K followers") == 1500


def test_parse_follower_count_m():
    assert _parse_follower_count("15M seguidores") == 15_000_000


def test_parse_follower_count_lowercase_k():
    assert _parse_follower_count("2.3k followers") == 2300


def test_parse_follower_count_no_match():
    assert _parse_follower_count("No data here") is None


def test_parse_follower_count_plain_no_comma():
    assert _parse_follower_count("500 followers") == 500


# --- _parse_profile_text ---


def test_parse_profile_text_full():
    data = _parse_profile_text(_SAMPLE_PROFILE_TEXT, "hotelitapua", "https://www.instagram.com/hotelitapua/")
    assert data.username == "hotelitapua"
    assert data.profile_url == "https://www.instagram.com/hotelitapua/"
    assert data.follower_count == 1500
    assert data.business_phone == "+595211234567"
    assert data.business_email == "reservas@hotelitapua.com"
    assert data.whatsapp == "+595981654321"
    assert data.external_url == "https://www.hotelitapua.com"


def test_parse_profile_text_minimal():
    data = _parse_profile_text(_SAMPLE_PROFILE_MINIMAL, "hotelitapua", "https://www.instagram.com/hotelitapua/")
    assert data.username == "hotelitapua"
    assert data.follower_count is None
    assert data.business_phone is None
    assert data.business_email is None


# --- scrape (integration with mocked Tavily) ---


@pytest.mark.asyncio
async def test_scrape_extract_success(service, tavily_mock):
    """Extract returns full profile text → parses all fields."""
    tavily_mock.extract_instagram_profile.return_value = _SAMPLE_PROFILE_TEXT

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert result.username == "hotelitapua"
    assert result.full_name is not None
    assert result.follower_count == 1500
    assert result.business_phone == "+595211234567"
    assert result.business_email == "reservas@hotelitapua.com"
    assert result.whatsapp == "+595981654321"
    tavily_mock.extract_instagram_profile.assert_awaited_once()
    tavily_mock.search_instagram_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_scrape_extract_with_bio_phones(service, tavily_mock):
    """Multiple phones in text → business_phone + bio_phones."""
    text = (
        "Hotel Sol\n"
        "Tel: +595 21 111 2222 / +595 981 333 4444\n"
        "800 followers\n"
    )
    tavily_mock.extract_instagram_profile.return_value = text

    result = await service.scrape("https://www.instagram.com/hotelsol/")

    assert result.business_phone == "+595211112222"
    assert "+595981333444" in result.bio_phones or "+5959813334444" in result.bio_phones


@pytest.mark.asyncio
async def test_scrape_extract_with_bio_emails(service, tavily_mock):
    """Multiple emails in text → business_email + bio_emails."""
    text = (
        "Hotel Sol\n"
        "contacto@hotelsol.com info@hotelsol.com\n"
        "800 followers\n"
    )
    tavily_mock.extract_instagram_profile.return_value = text

    result = await service.scrape("https://www.instagram.com/hotelsol/")

    assert result.business_email == "contacto@hotelsol.com"
    assert "info@hotelsol.com" in result.bio_emails


@pytest.mark.asyncio
async def test_scrape_extract_insufficient_falls_to_search(service, tavily_mock):
    """Extract returns too little text → falls back to Search."""
    tavily_mock.extract_instagram_profile.return_value = "short"
    tavily_mock.search_instagram_profile.return_value = _SAMPLE_PROFILE_TEXT

    result = await service.scrape(
        "https://www.instagram.com/hotelitapua/",
        hotel_name="Hotel Itapúa",
        city="Asunción",
    )

    assert result.username == "hotelitapua"
    assert result.follower_count == 1500
    tavily_mock.search_instagram_profile.assert_awaited_once_with(
        "hotelitapua", "Hotel Itapúa", "Asunción",
    )


@pytest.mark.asyncio
async def test_scrape_extract_none_falls_to_search(service, tavily_mock):
    """Extract returns None → falls back to Search."""
    tavily_mock.extract_instagram_profile.return_value = None
    tavily_mock.search_instagram_profile.return_value = _SAMPLE_PROFILE_TEXT

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert result.follower_count == 1500
    tavily_mock.search_instagram_profile.assert_awaited_once()


@pytest.mark.asyncio
async def test_scrape_both_fail_returns_minimal(service, tavily_mock):
    """Both Extract and Search fail → returns InstagramData with username only."""
    tavily_mock.extract_instagram_profile.return_value = None
    tavily_mock.search_instagram_profile.return_value = None

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert result.username == "hotelitapua"
    assert result.profile_url == "https://www.instagram.com/hotelitapua/"
    assert result.full_name is None
    assert result.follower_count is None
    assert result.bio_phones == []


@pytest.mark.asyncio
async def test_scrape_invalid_url_returns_empty(service, tavily_mock):
    """Non-Instagram URL → returns empty InstagramData."""
    result = await service.scrape("https://www.booking.com/hotel/x")

    assert result.username is None
    tavily_mock.extract_instagram_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_scrape_network_error_graceful(service, tavily_mock):
    """Exception in Tavily → returns empty InstagramData (never raises)."""
    tavily_mock.extract_instagram_profile.side_effect = Exception("boom")

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert isinstance(result, InstagramData)


@respx.mock
@pytest.mark.asyncio
async def test_scrape_with_wa_link_resolves(service, tavily_mock):
    """wa.link URL in text → follows redirect → extracts phone."""
    text = (
        "Hotel Sol\n"
        "Reservas via WhatsApp: https://wa.link/abc123\n"
        "500 followers\n"
    )
    tavily_mock.extract_instagram_profile.return_value = text
    respx.get("https://wa.link/abc123").mock(return_value=Response(
        301,
        headers={"location": "https://api.whatsapp.com/send?phone=595981654321"},
    ))

    result = await service.scrape("https://www.instagram.com/hotelsol/")

    assert result.whatsapp == "+595981654321"


@pytest.mark.asyncio
async def test_scrape_follower_count_formats(service, tavily_mock):
    """Follower count parsed from various formats."""
    for text_fmt, expected in [
        ("Hotel\n1,500 followers\n", 1500),
        ("Hotel\n2.5K followers\n", 2500),
        ("Hotel\n3M seguidores\n", 3_000_000),
    ]:
        # Pad to pass minimum extract length
        padded = text_fmt + " " * 50
        tavily_mock.extract_instagram_profile.return_value = padded
        result = await service.scrape("https://www.instagram.com/hotel/")
        assert result.follower_count == expected, f"Failed for: {text_fmt!r}"
