"""Tests for InstagramService (Perplexity-based)."""

import json

import httpx
import pytest
import respx
from httpx import Response

from app.schemas.instagram import InstagramData
from app.services.instagram import (
    InstagramService,
    _cross_validate,
    _is_all_null,
    _values_match,
    is_instagram_url,
    _extract_username,
    _extract_phones,
    _extract_emails,
)


_PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"


def _perplexity_response(data: dict) -> dict:
    """Wrap data dict as a Perplexity chat completion response."""
    return {
        "choices": [
            {"message": {"content": json.dumps(data)}}
        ],
    }


def _ig_json(
    full_name="Hotel Itapúa",
    biography="Reservas: +595 21 123 4567",
    external_url=None,
    business_email=None,
    business_phone=None,
    follower_count=1500,
    whatsapp_url=None,
):
    return {
        "full_name": full_name,
        "biography": biography,
        "external_url": external_url,
        "business_email": business_email,
        "business_phone": business_phone,
        "follower_count": follower_count,
        "whatsapp_url": whatsapp_url,
    }


@pytest.fixture
def client():
    return httpx.AsyncClient()


@pytest.fixture
def service(client):
    return InstagramService(client, "test-api-key")


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


# --- scrape ---


@respx.mock
@pytest.mark.asyncio
async def test_scrape_profile(service):
    """Basic profile scrape via Perplexity returns structured data."""
    respx.post(_PERPLEXITY_URL).mock(return_value=Response(
        200, json=_perplexity_response(_ig_json()),
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
    respx.post(_PERPLEXITY_URL).mock(return_value=Response(
        200,
        json=_perplexity_response(_ig_json(
            biography="Tel: +595 21 123 4567 / +595 981 654 321",
        )),
    ))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert "+595211234567" in result.bio_phones
    assert "+595981654321" in result.bio_phones


@respx.mock
@pytest.mark.asyncio
async def test_scrape_with_bio_emails(service):
    """Emails in biography are extracted."""
    respx.post(_PERPLEXITY_URL).mock(return_value=Response(
        200,
        json=_perplexity_response(_ig_json(
            biography="Reservas: reservas@hotel.com | info@hotel.com",
        )),
    ))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert "reservas@hotel.com" in result.bio_emails
    assert "info@hotel.com" in result.bio_emails


@respx.mock
@pytest.mark.asyncio
async def test_scrape_with_business_fields(service):
    """Business email and phone from Perplexity response."""
    respx.post(_PERPLEXITY_URL).mock(return_value=Response(
        200,
        json=_perplexity_response(_ig_json(
            biography="Bienvenidos",
            business_email="contact@hotel.com",
            business_phone="+595211234567",
        )),
    ))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert result.business_email == "contact@hotel.com"
    assert result.business_phone == "+595211234567"


@respx.mock
@pytest.mark.asyncio
async def test_scrape_bio_phone_dedup_against_business(service):
    """Bio phone same as business_phone → not duplicated in bio_phones."""
    respx.post(_PERPLEXITY_URL).mock(return_value=Response(
        200,
        json=_perplexity_response(_ig_json(
            biography="Tel: +595 21 123 4567",
            business_phone="+595211234567",
        )),
    ))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert result.business_phone == "+595211234567"
    assert "+595211234567" not in result.bio_phones


@respx.mock
@pytest.mark.asyncio
async def test_scrape_bio_email_dedup_against_business(service):
    """Bio email same as business_email → not duplicated in bio_emails."""
    respx.post(_PERPLEXITY_URL).mock(return_value=Response(
        200,
        json=_perplexity_response(_ig_json(
            biography="Email: contact@hotel.com",
            business_email="contact@hotel.com",
        )),
    ))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert result.business_email == "contact@hotel.com"
    assert "contact@hotel.com" not in result.bio_emails


@respx.mock
@pytest.mark.asyncio
async def test_scrape_with_whatsapp_wa_me(service):
    """wa.me URL in whatsapp_url → WhatsApp number extracted."""
    respx.post(_PERPLEXITY_URL).mock(return_value=Response(
        200,
        json=_perplexity_response(_ig_json(
            biography="Reservas por WhatsApp",
            whatsapp_url="https://wa.me/595981654321",
        )),
    ))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert result.whatsapp == "+595981654321"


@respx.mock
@pytest.mark.asyncio
async def test_scrape_with_whatsapp_external_url(service):
    """wa.me in external_url (no whatsapp_url) → WhatsApp number extracted."""
    respx.post(_PERPLEXITY_URL).mock(return_value=Response(
        200,
        json=_perplexity_response(_ig_json(
            biography="Reservas",
            external_url="https://wa.me/595981654321",
        )),
    ))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert result.whatsapp == "+595981654321"
    assert result.external_url == "https://wa.me/595981654321"


@respx.mock
@pytest.mark.asyncio
async def test_scrape_with_whatsapp_wa_link(service):
    """wa.link URL in whatsapp_url → follows redirect → extracts phone."""
    respx.post(_PERPLEXITY_URL).mock(return_value=Response(
        200,
        json=_perplexity_response(_ig_json(
            biography="",
            whatsapp_url="https://wa.link/abc123",
        )),
    ))
    respx.get("https://wa.link/abc123").mock(return_value=Response(
        301,
        headers={"location": "https://api.whatsapp.com/send?phone=595981654321"},
    ))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert result.whatsapp == "+595981654321"


@respx.mock
@pytest.mark.asyncio
async def test_scrape_perplexity_error(service):
    """Perplexity API error → returns InstagramData with username only."""
    respx.post(_PERPLEXITY_URL).mock(return_value=Response(500))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert result.username == "hotelitapua"
    assert result.full_name is None
    assert result.bio_phones == []


@respx.mock
@pytest.mark.asyncio
async def test_scrape_perplexity_bad_json(service):
    """Perplexity returns non-JSON → returns InstagramData with username."""
    respx.post(_PERPLEXITY_URL).mock(return_value=Response(
        200,
        json={"choices": [{"message": {"content": "I cannot access Instagram"}}]},
    ))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert result.username == "hotelitapua"
    assert result.full_name is None


@respx.mock
@pytest.mark.asyncio
async def test_scrape_network_error(service):
    """Network error → returns empty InstagramData (never raises)."""
    respx.post(_PERPLEXITY_URL).mock(side_effect=httpx.ConnectError("Connection refused"))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert isinstance(result, InstagramData)


@respx.mock
@pytest.mark.asyncio
async def test_scrape_invalid_url(service):
    """Non-Instagram URL → returns empty InstagramData."""
    result = await service.scrape("https://www.booking.com/hotel/x")

    assert result.username is None


@respx.mock
@pytest.mark.asyncio
async def test_scrape_perplexity_sends_auth_header(service):
    """Perplexity request includes Authorization header with API key."""
    route = respx.post(_PERPLEXITY_URL).mock(return_value=Response(
        200, json=_perplexity_response(_ig_json()),
    ))

    await service.scrape("https://www.instagram.com/hotelitapua/")

    assert route.called
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer test-api-key"


# --- _is_all_null ---


def test_is_all_null_true():
    assert _is_all_null({"a": None, "b": None}) is True


def test_is_all_null_false():
    assert _is_all_null({"a": None, "b": "value"}) is False


def test_is_all_null_empty():
    assert _is_all_null({}) is True


# --- retry on all-null ---


def _all_null_json():
    return _ig_json(
        full_name=None, biography=None, external_url=None,
        business_email=None, business_phone=None,
        follower_count=None, whatsapp_url=None,
    )


@respx.mock
@pytest.mark.asyncio
async def test_scrape_retries_on_all_null_then_succeeds(service):
    """First call returns all-null, next two return data → cross-validated."""
    route = respx.post(_PERPLEXITY_URL).mock(side_effect=[
        Response(200, json=_perplexity_response(_all_null_json())),
        Response(200, json=_perplexity_response(_ig_json())),
        Response(200, json=_perplexity_response(_ig_json())),
    ])

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert route.call_count == 3
    assert result.full_name == "Hotel Itapúa"
    assert result.follower_count == 1500


@respx.mock
@pytest.mark.asyncio
async def test_scrape_retries_max_3_times(service):
    """All 3 attempts return all-null → returns data with nulls."""
    route = respx.post(_PERPLEXITY_URL).mock(side_effect=[
        Response(200, json=_perplexity_response(_all_null_json())),
        Response(200, json=_perplexity_response(_all_null_json())),
        Response(200, json=_perplexity_response(_all_null_json())),
    ])

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert route.call_count == 3
    assert result.username == "hotelitapua"
    assert result.full_name is None


@respx.mock
@pytest.mark.asyncio
async def test_scrape_two_calls_for_cross_validation(service):
    """Data present on first call → makes second call for cross-validation."""
    route = respx.post(_PERPLEXITY_URL).mock(return_value=Response(
        200, json=_perplexity_response(_ig_json()),
    ))

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert route.call_count == 2
    assert result.full_name == "Hotel Itapúa"


@respx.mock
@pytest.mark.asyncio
async def test_scrape_cross_validation_drops_mismatched_fields(service):
    """Fields that disagree between two calls are set to None."""
    route = respx.post(_PERPLEXITY_URL).mock(side_effect=[
        Response(200, json=_perplexity_response(_ig_json(
            business_phone="+595211234567",
            whatsapp_url="https://wa.me/595981111111",
            follower_count=1500,
        ))),
        Response(200, json=_perplexity_response(_ig_json(
            business_phone="+595219999999",
            whatsapp_url="https://wa.me/595989999999",
            follower_count=12500,
        ))),
    ])

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert route.call_count == 2
    assert result.full_name == "Hotel Itapúa"  # same in both → kept
    assert result.business_phone is None  # disagreed → dropped
    assert result.whatsapp is None  # disagreed → dropped
    assert result.follower_count is None  # disagreed → dropped


@respx.mock
@pytest.mark.asyncio
async def test_scrape_cross_validation_keeps_matching_fields(service):
    """Fields that agree between two calls are kept."""
    route = respx.post(_PERPLEXITY_URL).mock(side_effect=[
        Response(200, json=_perplexity_response(_ig_json(
            business_email="hotel@test.com",
            business_phone="+595 21 123 4567",
        ))),
        Response(200, json=_perplexity_response(_ig_json(
            business_email="HOTEL@TEST.COM",
            business_phone="595211234567",
        ))),
    ])

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert route.call_count == 2
    assert result.business_email == "hotel@test.com"  # case-insensitive match
    assert result.business_phone == "+595211234567"  # digits match


@respx.mock
@pytest.mark.asyncio
async def test_scrape_only_one_non_null_skips_validation(service):
    """If only 1 non-null response in 3 attempts, use it without validation."""
    route = respx.post(_PERPLEXITY_URL).mock(side_effect=[
        Response(200, json=_perplexity_response(_all_null_json())),
        Response(200, json=_perplexity_response(_all_null_json())),
        Response(200, json=_perplexity_response(_ig_json())),
    ])

    result = await service.scrape("https://www.instagram.com/hotelitapua/")

    assert route.call_count == 3
    assert result.full_name == "Hotel Itapúa"


# --- _values_match pure tests ---


def test_values_match_phone_digits_only():
    assert _values_match("business_phone", "+595 21 123", "59521123") is True
    assert _values_match("business_phone", "+595211", "+595999") is False


def test_values_match_email_case_insensitive():
    assert _values_match("business_email", "A@B.com", "a@b.com") is True
    assert _values_match("business_email", "a@b.com", "x@y.com") is False


def test_values_match_name_case_insensitive():
    assert _values_match("full_name", "Hotel Sol", "hotel sol") is True


def test_values_match_biography_always_true():
    assert _values_match("biography", "text A", "text B") is True


def test_values_match_follower_count_exact():
    assert _values_match("follower_count", 1500, 1500) is True
    assert _values_match("follower_count", 1500, 12500) is False


def test_values_match_whatsapp_url_digits():
    assert _values_match("whatsapp_url", "https://wa.me/595981654321", "wa.me/595981654321") is True
    assert _values_match("whatsapp_url", "https://wa.me/595981654321", "wa.me/595999999999") is False


# --- _cross_validate pure tests ---


def test_cross_validate_both_agree():
    a = {"full_name": "Hotel", "business_email": "a@b.com", "follower_count": 100}
    b = {"full_name": "hotel", "business_email": "A@B.COM", "follower_count": 100}
    result = _cross_validate(a, b)
    assert result["full_name"] == "Hotel"
    assert result["business_email"] == "a@b.com"
    assert result["follower_count"] == 100


def test_cross_validate_disagreement_drops():
    a = {"business_phone": "+595111", "follower_count": 100}
    b = {"business_phone": "+595999", "follower_count": 200}
    result = _cross_validate(a, b)
    assert result["business_phone"] is None
    assert result["follower_count"] is None


def test_cross_validate_one_null_keeps_value():
    a = {"business_email": "a@b.com", "business_phone": None}
    b = {"business_email": None, "business_phone": "+595111"}
    result = _cross_validate(a, b)
    assert result["business_email"] == "a@b.com"
    assert result["business_phone"] == "+595111"


def test_cross_validate_both_null():
    a = {"business_email": None}
    b = {"business_email": None}
    result = _cross_validate(a, b)
    assert result["business_email"] is None
