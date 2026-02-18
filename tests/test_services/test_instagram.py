"""Tests for InstagramService."""

import json

import httpx
import pytest
import respx
from httpx import Response

from app.schemas.instagram import InstagramData
from app.services.instagram import InstagramService, is_instagram_url, _extract_username


_API_URL = "https://www.instagram.com/api/v1/users/web_profile_info/"


def _profile_response(
    username="hotelitapua",
    full_name="Hotel Itapúa",
    biography="Reservas: +595 21 123 4567",
    external_url=None,
    business_email=None,
    business_phone_number=None,
    follower_count=1500,
    bio_links=None,
):
    user = {
        "full_name": full_name,
        "biography": biography,
        "external_url": external_url,
        "business_email": business_email,
        "business_phone_number": business_phone_number,
        "edge_followed_by": {"count": follower_count},
        "bio_links": bio_links or [],
    }
    return {"data": {"user": user}}


@pytest.fixture
def client():
    return httpx.AsyncClient()


@pytest.fixture
def service(client):
    return InstagramService(client)


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


# --- scrape ---


@respx.mock
@pytest.mark.asyncio
async def test_scrape_profile(service):
    """Basic profile scrape returns structured data."""
    respx.get(_API_URL).mock(return_value=Response(
        200, json=_profile_response(),
    ))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert result.username == "hotelitapua"
    assert result.full_name == "Hotel Itapúa"
    assert result.biography == "Reservas: +595 21 123 4567"
    assert result.follower_count == 1500
    assert result.profile_url == "https://www.instagram.com/hotelitapua/"


@respx.mock
@pytest.mark.asyncio
async def test_scrape_with_bio_phones(service):
    """Phones in biography are extracted and normalized to E.164."""
    respx.get(_API_URL).mock(return_value=Response(
        200,
        json=_profile_response(
            biography="Tel: +595 21 123 4567 / +595 981 654 321",
        ),
    ))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert "+595211234567" in result.bio_phones
    assert "+595981654321" in result.bio_phones


@respx.mock
@pytest.mark.asyncio
async def test_scrape_with_bio_emails(service):
    """Emails in biography are extracted."""
    respx.get(_API_URL).mock(return_value=Response(
        200,
        json=_profile_response(
            biography="Reservas: reservas@hotel.com | info@hotel.com",
        ),
    ))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert "reservas@hotel.com" in result.bio_emails
    assert "info@hotel.com" in result.bio_emails


@respx.mock
@pytest.mark.asyncio
async def test_scrape_with_business_fields(service):
    """Business email and phone from structured IG fields."""
    respx.get(_API_URL).mock(return_value=Response(
        200,
        json=_profile_response(
            biography="Bienvenidos",
            business_email="contact@hotel.com",
            business_phone_number="+595211234567",
        ),
    ))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert result.business_email == "contact@hotel.com"
    assert result.business_phone == "+595211234567"


@respx.mock
@pytest.mark.asyncio
async def test_scrape_bio_phone_dedup_against_business(service):
    """Bio phone same as business_phone → not duplicated in bio_phones."""
    respx.get(_API_URL).mock(return_value=Response(
        200,
        json=_profile_response(
            biography="Tel: +595 21 123 4567",
            business_phone_number="+595211234567",
        ),
    ))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert result.business_phone == "+595211234567"
    assert "+595211234567" not in result.bio_phones


@respx.mock
@pytest.mark.asyncio
async def test_scrape_bio_email_dedup_against_business(service):
    """Bio email same as business_email → not duplicated in bio_emails."""
    respx.get(_API_URL).mock(return_value=Response(
        200,
        json=_profile_response(
            biography="Email: contact@hotel.com",
            business_email="contact@hotel.com",
        ),
    ))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert result.business_email == "contact@hotel.com"
    assert "contact@hotel.com" not in result.bio_emails


@respx.mock
@pytest.mark.asyncio
async def test_scrape_with_whatsapp_wa_me(service):
    """wa.me link in bio_links → WhatsApp number extracted."""
    respx.get(_API_URL).mock(return_value=Response(
        200,
        json=_profile_response(
            biography="Reservas por WhatsApp",
            bio_links=[{"url": "https://wa.me/595981654321"}],
        ),
    ))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert result.whatsapp == "+595981654321"


@respx.mock
@pytest.mark.asyncio
async def test_scrape_with_whatsapp_external_url(service):
    """wa.me in external_url → WhatsApp number extracted."""
    respx.get(_API_URL).mock(return_value=Response(
        200,
        json=_profile_response(
            biography="Reservas",
            external_url="https://wa.me/595981654321",
        ),
    ))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert result.whatsapp == "+595981654321"
    assert result.external_url == "https://wa.me/595981654321"


@respx.mock
@pytest.mark.asyncio
async def test_scrape_with_whatsapp_wa_link(service):
    """wa.link URL → follows redirect → extracts phone."""
    # First: IG API response with wa.link
    respx.get(_API_URL).mock(return_value=Response(
        200,
        json=_profile_response(
            biography="",
            external_url="https://wa.link/abc123",
        ),
    ))
    # Second: wa.link redirect
    respx.get("https://wa.link/abc123").mock(return_value=Response(
        301,
        headers={"location": "https://api.whatsapp.com/send?phone=595981654321"},
    ))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert result.whatsapp == "+595981654321"


@respx.mock
@pytest.mark.asyncio
async def test_scrape_profile_not_found(service):
    """404 → returns empty InstagramData with username."""
    respx.get(_API_URL).mock(return_value=Response(404))

    result = await service.scrape("https://www.instagram.com/nonexistent/")

    assert result.username == "nonexistent"
    assert result.full_name is None
    assert result.bio_phones == []


@respx.mock
@pytest.mark.asyncio
async def test_scrape_rate_limited(service):
    """429 → returns empty InstagramData with username."""
    respx.get(_API_URL).mock(return_value=Response(429))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert result.username == "hotelitapua"
    assert result.full_name is None


@respx.mock
@pytest.mark.asyncio
async def test_scrape_private_profile(service):
    """Private profile with limited data → returns what's available."""
    respx.get(_API_URL).mock(return_value=Response(
        200,
        json=_profile_response(
            full_name="Hotel Privado",
            biography="Solo reservas por DM",
            follower_count=500,
        ),
    ))

    result = await service.scrape("https://www.instagram.com/hotelprivado/")

    assert result.full_name == "Hotel Privado"
    assert result.follower_count == 500


@respx.mock
@pytest.mark.asyncio
async def test_scrape_network_error(service):
    """Network error → returns empty InstagramData (never raises)."""
    respx.get(_API_URL).mock(side_effect=httpx.ConnectError("Connection refused"))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert isinstance(result, InstagramData)


@respx.mock
@pytest.mark.asyncio
async def test_scrape_invalid_url(service):
    """Non-Instagram URL → returns empty InstagramData."""
    result = await service.scrape("https://www.booking.com/hotel/x")

    assert result.username is None
