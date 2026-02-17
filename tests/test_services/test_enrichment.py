"""Tests for EnrichmentService._create_phone_contacts."""

import httpx
import pytest
import respx
from httpx import Response
from unittest.mock import AsyncMock, patch

from app.schemas.google_places import GooglePlace
from app.schemas.hubspot import (
    HubSpotCompany,
    HubSpotCompanyProperties,
    HubSpotContact,
    HubSpotContactProperties,
)
from app.schemas.tripadvisor import TripAdvisorLocation
from app.services.enrichment import EnrichmentService, _normalize_phone
from app.services.google_places import GooglePlacesService
from app.services.hubspot import HubSpotService


@pytest.fixture
def hubspot_mock():
    mock = AsyncMock(spec=HubSpotService)
    mock.get_associated_contacts.return_value = []
    mock.create_contact.return_value = "new-contact-id"
    return mock


@pytest.fixture
def service(hubspot_mock):
    google = AsyncMock(spec=GooglePlacesService)
    return EnrichmentService(
        hubspot=hubspot_mock,
        google_places=google,
        tripadvisor=None,
        overwrite=False,
    )


def _place(phone_intl=None, phone_national=None):
    return GooglePlace(
        internationalPhoneNumber=phone_intl,
        nationalPhoneNumber=phone_national,
    )


def _ta_location(phone=None):
    return TripAdvisorLocation(location_id="ta-1", phone=phone)


def _contact(contact_id="100", phone=None, mobile=None):
    return HubSpotContact(
        id=contact_id,
        properties=HubSpotContactProperties(
            phone=phone,
            mobilephone=mobile,
        ),
    )


# --- Tests ---


@pytest.mark.asyncio
async def test_google_only_no_contact(service, hubspot_mock):
    """Google Places phone only → no contact created (phone goes to company field)."""
    place = _place(phone_intl="+34 911 234 567")

    await service._create_phone_contacts("C1", "Hotel Sol", place, None)

    hubspot_mock.get_associated_contacts.assert_not_awaited()
    hubspot_mock.create_contact.assert_not_awaited()


@pytest.mark.asyncio
async def test_creates_tripadvisor_contact(service, hubspot_mock):
    """TripAdvisor has a phone (no Google) → one contact created."""
    ta = _ta_location(phone="+52 55 1234 5678")

    await service._create_phone_contacts("C1", "Hotel Luna", None, ta)

    hubspot_mock.create_contact.assert_awaited_once_with(
        "C1",
        {
            "firstname": "Recepcion Hotel Luna",
            "lastname": "/ TripAdvisor",
            "phone": "+525512345678",
        },
    )


@pytest.mark.asyncio
async def test_creates_tripadvisor_contact_different_from_google(service, hubspot_mock):
    """Both sources have different phones → only TripAdvisor contact created."""
    place = _place(phone_intl="+34 911 111 111")
    ta = _ta_location(phone="+52 55 222 2222")

    await service._create_phone_contacts("C1", "Hotel Mar", place, ta)

    hubspot_mock.create_contact.assert_awaited_once_with(
        "C1",
        {
            "firstname": "Recepcion Hotel Mar",
            "lastname": "/ TripAdvisor",
            "phone": "+52552222222",
        },
    )


@pytest.mark.asyncio
async def test_dedup_same_phone(service, hubspot_mock):
    """Same phone in both sources → no contact (TripAdvisor skipped, Google on company)."""
    place = _place(phone_intl="+34 911 234 567")
    ta = _ta_location(phone="+34 911 234 567")

    await service._create_phone_contacts("C1", "Hotel Rio", place, ta)

    hubspot_mock.create_contact.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_existing_contact_phone(service, hubspot_mock):
    """Existing contact already has that phone → no new contact."""
    ta = _ta_location(phone="+52 55 1234 5678")
    hubspot_mock.get_associated_contacts.return_value = [
        _contact(phone="+525512345678"),
    ]

    await service._create_phone_contacts("C1", "Hotel Playa", None, ta)

    hubspot_mock.create_contact.assert_not_awaited()


@pytest.mark.asyncio
async def test_contact_creation_failure_doesnt_block(service, hubspot_mock):
    """Error in create_contact → no exception raised."""
    ta = _ta_location(phone="+52 55 1234 5678")
    hubspot_mock.create_contact.side_effect = Exception("HubSpot error")

    # Should not raise
    await service._create_phone_contacts("C1", "Hotel Cielo", None, ta)


@pytest.mark.asyncio
async def test_no_phones_no_contacts(service, hubspot_mock):
    """No phones in any source → no contacts created, no API calls."""
    place = _place()  # no phone
    ta = _ta_location()  # no phone

    await service._create_phone_contacts("C1", "Hotel Vacio", place, ta)

    hubspot_mock.get_associated_contacts.assert_not_awaited()
    hubspot_mock.create_contact.assert_not_awaited()


@pytest.mark.asyncio
async def test_prefers_international_over_national(service, hubspot_mock):
    """Google has both international and national → no contact created (Google only)."""
    place = _place(phone_intl="+34 911 234 567", phone_national="911 234 567")

    await service._create_phone_contacts("C1", "Hotel Dual", place, None)

    hubspot_mock.create_contact.assert_not_awaited()


@pytest.mark.asyncio
async def test_normalizes_phone_without_plus(service, hubspot_mock):
    """Phone without '+' prefix gets normalized to E.164."""
    ta = _ta_location(phone="52 55 1234 5678")

    await service._create_phone_contacts("C1", "Hotel Norm", None, ta)

    args = hubspot_mock.create_contact.await_args.args
    assert args[1]["phone"] == "+525512345678"


@pytest.mark.asyncio
async def test_dedup_ignores_formatting_differences(service, hubspot_mock):
    """Same digits with different formatting → deduplicated (no contact)."""
    place = _place(phone_intl="+34 911-234-567")
    ta = _ta_location(phone="+34911234567")

    await service._create_phone_contacts("C1", "Hotel Format", place, ta)

    hubspot_mock.create_contact.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_contact_mobile_also_checked(service, hubspot_mock):
    """Existing contact's mobilephone field is also checked for dedup."""
    ta = _ta_location(phone="+52 55 1234 5678")
    hubspot_mock.get_associated_contacts.return_value = [
        _contact(mobile="+525512345678"),
    ]

    await service._create_phone_contacts("C1", "Hotel Mobile", None, ta)

    hubspot_mock.create_contact.assert_not_awaited()


# --- _normalize_phone tests ---


def test_normalize_phone_strips_spaces_dashes():
    assert _normalize_phone("+34 911-234-567") == "+34911234567"


def test_normalize_phone_without_plus():
    assert _normalize_phone("52 55 1234 5678") == "+525512345678"


def test_normalize_phone_already_e164():
    assert _normalize_phone("+34911234567") == "+34911234567"


def test_normalize_phone_empty():
    assert _normalize_phone("") == ""
