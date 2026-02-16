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
async def test_creates_google_contact(service, hubspot_mock):
    """Google Places has a phone → one contact created."""
    place = _place(phone_intl="+34 911 234 567")

    await service._create_phone_contacts("C1", "Hotel Sol", place, None)

    hubspot_mock.get_associated_contacts.assert_awaited_once_with("C1")
    hubspot_mock.create_contact.assert_awaited_once_with(
        "C1",
        {
            "firstname": "Recepcion Hotel Sol",
            "lastname": "Google Places",
            "phone": "+34 911 234 567",
        },
    )


@pytest.mark.asyncio
async def test_creates_tripadvisor_contact(service, hubspot_mock):
    """TripAdvisor has a phone → one contact created."""
    ta = _ta_location(phone="+52 55 1234 5678")

    await service._create_phone_contacts("C1", "Hotel Luna", None, ta)

    hubspot_mock.create_contact.assert_awaited_once_with(
        "C1",
        {
            "firstname": "Recepcion Hotel Luna",
            "lastname": "TripAdvisor",
            "phone": "+52 55 1234 5678",
        },
    )


@pytest.mark.asyncio
async def test_creates_both_contacts(service, hubspot_mock):
    """Both sources have different phones → two contacts created."""
    place = _place(phone_intl="+34 911 111 111")
    ta = _ta_location(phone="+52 55 222 2222")

    await service._create_phone_contacts("C1", "Hotel Mar", place, ta)

    assert hubspot_mock.create_contact.await_count == 2
    calls = hubspot_mock.create_contact.await_args_list
    assert calls[0].args == (
        "C1",
        {"firstname": "Recepcion Hotel Mar", "lastname": "Google Places", "phone": "+34 911 111 111"},
    )
    assert calls[1].args == (
        "C1",
        {"firstname": "Recepcion Hotel Mar", "lastname": "TripAdvisor", "phone": "+52 55 222 2222"},
    )


@pytest.mark.asyncio
async def test_dedup_same_phone(service, hubspot_mock):
    """Same phone in both sources → only one contact (Google Places wins)."""
    place = _place(phone_intl="+34 911 234 567")
    ta = _ta_location(phone="+34 911 234 567")

    await service._create_phone_contacts("C1", "Hotel Rio", place, ta)

    hubspot_mock.create_contact.assert_awaited_once()
    args = hubspot_mock.create_contact.await_args.args
    assert args[1]["lastname"] == "Google Places"


@pytest.mark.asyncio
async def test_skips_existing_contact_phone(service, hubspot_mock):
    """Existing contact already has that phone → no new contact."""
    place = _place(phone_intl="+34 911 234 567")
    hubspot_mock.get_associated_contacts.return_value = [
        _contact(phone="+34 911 234 567"),
    ]

    await service._create_phone_contacts("C1", "Hotel Playa", place, None)

    hubspot_mock.create_contact.assert_not_awaited()


@pytest.mark.asyncio
async def test_contact_creation_failure_doesnt_block(service, hubspot_mock):
    """Error in create_contact → no exception raised."""
    place = _place(phone_intl="+34 911 234 567")
    hubspot_mock.create_contact.side_effect = Exception("HubSpot error")

    # Should not raise
    await service._create_phone_contacts("C1", "Hotel Cielo", place, None)


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
    """When both international and national phone exist, uses international."""
    place = _place(phone_intl="+34 911 234 567", phone_national="911 234 567")

    await service._create_phone_contacts("C1", "Hotel Dual", place, None)

    args = hubspot_mock.create_contact.await_args.args
    assert args[1]["phone"] == "+34 911 234 567"


@pytest.mark.asyncio
async def test_normalizes_phone_without_plus(service, hubspot_mock):
    """Phone without '+' prefix gets normalized."""
    ta = _ta_location(phone="52 55 1234 5678")

    await service._create_phone_contacts("C1", "Hotel Norm", None, ta)

    args = hubspot_mock.create_contact.await_args.args
    assert args[1]["phone"] == "+52 55 1234 5678"


@pytest.mark.asyncio
async def test_dedup_ignores_formatting_differences(service, hubspot_mock):
    """Same digits with different formatting → deduplicated."""
    place = _place(phone_intl="+34 911-234-567")
    ta = _ta_location(phone="+34911234567")

    await service._create_phone_contacts("C1", "Hotel Format", place, ta)

    hubspot_mock.create_contact.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_contact_mobile_also_checked(service, hubspot_mock):
    """Existing contact's mobilephone field is also checked for dedup."""
    place = _place(phone_intl="+34 911 234 567")
    hubspot_mock.get_associated_contacts.return_value = [
        _contact(mobile="+34 911 234 567"),
    ]

    await service._create_phone_contacts("C1", "Hotel Mobile", place, None)

    hubspot_mock.create_contact.assert_not_awaited()
