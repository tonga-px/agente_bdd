"""Tests for EnrichmentService._create_contacts."""

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
from app.schemas.instagram import InstagramData
from app.schemas.tripadvisor import TripAdvisorLocation
from app.schemas.website import WebScrapedData
from app.exceptions.custom import HubSpotError
from app.services.enrichment import (
    EnrichmentService,
    _extract_conflicting_id,
    _is_same_company,
    _normalize_phone,
)
from app.services.google_places import GooglePlacesService
from app.services.hubspot import HubSpotService
from app.services.instagram import InstagramService


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
async def test_google_only_creates_contact(service, hubspot_mock):
    """Google Places phone → creates a '/ Google' contact."""
    place = _place(phone_intl="+34 911 234 567")

    await service._create_contacts("C1", "Hotel Sol", place, None)

    hubspot_mock.create_contact.assert_awaited_once_with(
        "C1",
        {
            "firstname": "Recepcion Hotel Sol",
            "lastname": "/ Google",
            "phone": "+34911234567",
        },
    )


@pytest.mark.asyncio
async def test_creates_tripadvisor_contact(service, hubspot_mock):
    """TripAdvisor has a phone (no Google) → one contact created."""
    ta = _ta_location(phone="+52 55 1234 5678")

    await service._create_contacts("C1", "Hotel Luna", None, ta)

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
    """Both sources have different phones → Google + TripAdvisor contacts created."""
    place = _place(phone_intl="+34 911 111 111")
    ta = _ta_location(phone="+52 55 222 2222")

    await service._create_contacts("C1", "Hotel Mar", place, ta)

    assert hubspot_mock.create_contact.await_count == 2
    calls = hubspot_mock.create_contact.await_args_list
    assert calls[0].args == ("C1", {
        "firstname": "Recepcion Hotel Mar",
        "lastname": "/ Google",
        "phone": "+34911111111",
    })
    assert calls[1].args == ("C1", {
        "firstname": "Recepcion Hotel Mar",
        "lastname": "/ TripAdvisor",
        "phone": "+52552222222",
    })


@pytest.mark.asyncio
async def test_dedup_same_phone(service, hubspot_mock):
    """Same phone in both sources → only Google contact (TripAdvisor skipped)."""
    place = _place(phone_intl="+34 911 234 567")
    ta = _ta_location(phone="+34 911 234 567")

    await service._create_contacts("C1", "Hotel Rio", place, ta)

    hubspot_mock.create_contact.assert_awaited_once_with(
        "C1",
        {
            "firstname": "Recepcion Hotel Rio",
            "lastname": "/ Google",
            "phone": "+34911234567",
        },
    )


@pytest.mark.asyncio
async def test_skips_existing_contact_phone(service, hubspot_mock):
    """Existing contact already has that phone → no new contact."""
    ta = _ta_location(phone="+52 55 1234 5678")
    hubspot_mock.get_associated_contacts.return_value = [
        _contact(phone="+525512345678"),
    ]

    await service._create_contacts("C1", "Hotel Playa", None, ta)

    hubspot_mock.create_contact.assert_not_awaited()


@pytest.mark.asyncio
async def test_contact_creation_failure_doesnt_block(service, hubspot_mock):
    """Error in create_contact → no exception raised."""
    ta = _ta_location(phone="+52 55 1234 5678")
    hubspot_mock.create_contact.side_effect = Exception("HubSpot error")

    # Should not raise
    await service._create_contacts("C1", "Hotel Cielo", None, ta)


@pytest.mark.asyncio
async def test_no_phones_no_contacts(service, hubspot_mock):
    """No phones in any source → no contacts created, no API calls."""
    place = _place()  # no phone
    ta = _ta_location()  # no phone

    await service._create_contacts("C1", "Hotel Vacio", place, ta)

    hubspot_mock.get_associated_contacts.assert_not_awaited()
    hubspot_mock.create_contact.assert_not_awaited()


@pytest.mark.asyncio
async def test_prefers_international_over_national(service, hubspot_mock):
    """Google has both international and national → uses international for contact."""
    place = _place(phone_intl="+34 911 234 567", phone_national="911 234 567")

    await service._create_contacts("C1", "Hotel Dual", place, None)

    hubspot_mock.create_contact.assert_awaited_once_with(
        "C1",
        {
            "firstname": "Recepcion Hotel Dual",
            "lastname": "/ Google",
            "phone": "+34911234567",
        },
    )


@pytest.mark.asyncio
async def test_normalizes_phone_without_plus(service, hubspot_mock):
    """Phone without '+' prefix gets normalized to E.164."""
    ta = _ta_location(phone="52 55 1234 5678")

    await service._create_contacts("C1", "Hotel Norm", None, ta)

    args = hubspot_mock.create_contact.await_args.args
    assert args[1]["phone"] == "+525512345678"


@pytest.mark.asyncio
async def test_dedup_ignores_formatting_differences(service, hubspot_mock):
    """Same digits with different formatting → only Google contact (TripAdvisor deduped)."""
    place = _place(phone_intl="+34 911-234-567")
    ta = _ta_location(phone="+34911234567")

    await service._create_contacts("C1", "Hotel Format", place, ta)

    hubspot_mock.create_contact.assert_awaited_once_with(
        "C1",
        {
            "firstname": "Recepcion Hotel Format",
            "lastname": "/ Google",
            "phone": "+34911234567",
        },
    )


@pytest.mark.asyncio
async def test_existing_contact_mobile_also_checked(service, hubspot_mock):
    """Existing contact's mobilephone field is also checked for dedup."""
    ta = _ta_location(phone="+52 55 1234 5678")
    hubspot_mock.get_associated_contacts.return_value = [
        _contact(mobile="+525512345678"),
    ]

    await service._create_contacts("C1", "Hotel Mobile", None, ta)

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


def test_normalize_phone_rejects_local_zero():
    """Phone starting with 0 (local, no country code) is rejected."""
    assert _normalize_phone("0336275307") == ""
    assert _normalize_phone("+0336275307") == ""


def test_normalize_phone_rejects_too_short():
    """Fewer than 7 digits is rejected."""
    assert _normalize_phone("12345") == ""
    assert _normalize_phone("+123456") == ""


def test_normalize_phone_rejects_too_long():
    """More than 15 digits is rejected."""
    assert _normalize_phone("1234567890123456") == ""


def test_normalize_phone_accepts_7_digits():
    """Exactly 7 digits is accepted."""
    assert _normalize_phone("5611111") == "+5611111"


def test_normalize_phone_accepts_15_digits():
    """Exactly 15 digits is accepted."""
    assert _normalize_phone("123456789012345") == "+123456789012345"


# --- Web data contact creation tests ---


def _web_data(phones=None, whatsapp=None, emails=None):
    return WebScrapedData(
        phones=phones or [],
        whatsapp=whatsapp,
        emails=emails or [],
        source_url="https://hotel.com",
    )


@pytest.mark.asyncio
async def test_web_email_creates_contact(service, hubspot_mock):
    """Website email → contact with email + lastname '/ Website'."""
    web = _web_data(emails=["reservas@hotel.com"])

    await service._create_contacts("C1", "Hotel Web", None, None, web)

    hubspot_mock.create_contact.assert_awaited_once_with(
        "C1",
        {
            "firstname": "Recepcion Hotel Web",
            "lastname": "/ Website",
            "email": "reservas@hotel.com",
        },
    )


@pytest.mark.asyncio
async def test_web_email_phone_whatsapp(service, hubspot_mock):
    """Website email + phone + WhatsApp → all in one contact."""
    web = _web_data(
        phones=["+541152630435"],
        whatsapp="+5491123530759",
        emails=["info@hotel.com"],
    )

    await service._create_contacts("C1", "Hotel Full", None, None, web)

    hubspot_mock.create_contact.assert_awaited_once_with(
        "C1",
        {
            "firstname": "Recepcion Hotel Full",
            "lastname": "/ Website",
            "email": "info@hotel.com",
            "phone": "+541152630435",
            "mobilephone": "+5491123530759",
        },
    )


@pytest.mark.asyncio
async def test_web_phone_only_creates_contact(service, hubspot_mock):
    """Website phone (no email) → phone-only contact."""
    web = _web_data(phones=["+541199887766"])

    await service._create_contacts("C1", "Hotel Ph", None, None, web)

    hubspot_mock.create_contact.assert_awaited_once_with(
        "C1",
        {
            "firstname": "Recepcion Hotel Ph",
            "lastname": "/ Website",
            "phone": "+541199887766",
        },
    )


@pytest.mark.asyncio
async def test_web_phone_with_whatsapp(service, hubspot_mock):
    """Website phone + WhatsApp (no email) → phone contact with mobilephone."""
    web = _web_data(phones=["+541199887766"], whatsapp="+5491123530759")

    await service._create_contacts("C1", "Hotel WA", None, None, web)

    hubspot_mock.create_contact.assert_awaited_once_with(
        "C1",
        {
            "firstname": "Recepcion Hotel WA",
            "lastname": "/ Website",
            "phone": "+541199887766",
            "mobilephone": "+5491123530759",
        },
    )


@pytest.mark.asyncio
async def test_web_phone_dedup_against_google(service, hubspot_mock):
    """Web phone same as Google → Google contact gets email, no separate website contact."""
    place = _place(phone_intl="+54 11 5263 0435")
    web = _web_data(phones=["+541152630435"], emails=["info@hotel.com"])

    await service._create_contacts("C1", "Hotel Dup", place, None, web)

    # Google contact gets the email; no separate website contact (email already used)
    hubspot_mock.create_contact.assert_awaited_once_with(
        "C1",
        {
            "firstname": "Recepcion Hotel Dup",
            "lastname": "/ Google",
            "phone": "+541152630435",
            "email": "info@hotel.com",
        },
    )


@pytest.mark.asyncio
async def test_web_email_dedup_against_existing(service, hubspot_mock):
    """Web email already in existing contacts → no web contact."""
    hubspot_mock.get_associated_contacts.return_value = [
        HubSpotContact(
            id="200",
            properties=HubSpotContactProperties(email="info@hotel.com"),
        ),
    ]
    web = _web_data(emails=["info@hotel.com"])

    await service._create_contacts("C1", "Hotel Exist", None, None, web)

    hubspot_mock.create_contact.assert_not_awaited()


@pytest.mark.asyncio
async def test_web_scraper_failure_doesnt_block(service, hubspot_mock):
    """Error in web data processing → no exception raised."""
    hubspot_mock.get_associated_contacts.side_effect = Exception("API error")
    web = _web_data(emails=["info@hotel.com"])

    # Should not raise
    await service._create_contacts("C1", "Hotel Err", None, None, web)


# --- Google phone contact creation tests ---


@pytest.mark.asyncio
async def test_google_phone_creates_contact(service, hubspot_mock):
    """Google phone with no existing contact → creates '/ Google' contact."""
    place = _place(phone_intl="+52 55 1234 5678")

    await service._create_contacts("C1", "Hotel Playa", place, None)

    hubspot_mock.create_contact.assert_awaited_once_with(
        "C1",
        {
            "firstname": "Recepcion Hotel Playa",
            "lastname": "/ Google",
            "phone": "+525512345678",
        },
    )


@pytest.mark.asyncio
async def test_google_phone_with_email_and_whatsapp(service, hubspot_mock):
    """Google phone + web email + WhatsApp → all packed into one Google contact."""
    place = _place(phone_intl="+52 55 1234 5678")
    web = _web_data(emails=["reservas@hotel.com"], whatsapp="+5215511112222")

    await service._create_contacts("C1", "Hotel Mix", place, None, web)

    hubspot_mock.create_contact.assert_awaited_once_with(
        "C1",
        {
            "firstname": "Recepcion Hotel Mix",
            "lastname": "/ Google",
            "phone": "+525512345678",
            "email": "reservas@hotel.com",
            "mobilephone": "+5215511112222",
            "hs_whatsapp_phone_number": "+5215511112222",
        },
    )


@pytest.mark.asyncio
async def test_google_phone_skipped_if_existing_contact(service, hubspot_mock):
    """Existing contact already has Google phone → no new contact created."""
    place = _place(phone_intl="+52 55 1234 5678")
    hubspot_mock.get_associated_contacts.return_value = [
        _contact(phone="+525512345678"),
    ]

    await service._create_contacts("C1", "Hotel Viejo", place, None)

    hubspot_mock.create_contact.assert_not_awaited()


@pytest.mark.asyncio
async def test_google_phone_email_not_repeated_in_website_contact(service, hubspot_mock):
    """Email already used in Google contact → website contact gets a different email or none."""
    place = _place(phone_intl="+52 55 1234 5678")
    web = _web_data(
        phones=["+541199887766"],
        emails=["reservas@hotel.com", "info@hotel.com"],
    )

    await service._create_contacts("C1", "Hotel Doble", place, None, web)

    assert hubspot_mock.create_contact.await_count == 2
    calls = hubspot_mock.create_contact.await_args_list
    # First call: Google contact with first email
    assert calls[0].args == ("C1", {
        "firstname": "Recepcion Hotel Doble",
        "lastname": "/ Google",
        "phone": "+525512345678",
        "email": "reservas@hotel.com",
    })
    # Second call: Website contact with second email (first was used by Google)
    assert calls[1].args == ("C1", {
        "firstname": "Recepcion Hotel Doble",
        "lastname": "/ Website",
        "email": "info@hotel.com",
        "phone": "+541199887766",
    })


@pytest.mark.asyncio
async def test_no_google_phone_website_email_still_creates(service, hubspot_mock):
    """No Google phone, website email → website contact created as before."""
    web = _web_data(emails=["info@hotel.com"])

    await service._create_contacts("C1", "Hotel Web Only", None, None, web)

    hubspot_mock.create_contact.assert_awaited_once_with(
        "C1",
        {
            "firstname": "Recepcion Hotel Web Only",
            "lastname": "/ Website",
            "email": "info@hotel.com",
        },
    )


# --- _extract_conflicting_id tests ---


def test_extract_conflicting_id():
    msg = (
        'Cannot set PropertyValueCoordinates{objectType=COMPANY, '
        'propertyName=id_hotel, value=ChIJ123} on 48328838322. '
        '51090765207 already has that value.'
    )
    assert _extract_conflicting_id(msg) == "51090765207"


def test_extract_conflicting_id_no_match():
    assert _extract_conflicting_id("some random error") is None
    assert _extract_conflicting_id("") is None


# --- _is_same_company tests ---


def test_is_same_company_exact_match():
    a = HubSpotCompanyProperties(name="Hotel Asunción", city="Asunción", country="Paraguay")
    b = HubSpotCompanyProperties(name="Hotel Asunción", city="Asunción", country="Paraguay")
    assert _is_same_company(a, b) is True


def test_is_same_company_name_contains():
    a = HubSpotCompanyProperties(name="Hotel X", city="Lima")
    b = HubSpotCompanyProperties(name="Hotel X Boutique", city="Lima")
    assert _is_same_company(a, b) is True


def test_is_same_company_different_name():
    a = HubSpotCompanyProperties(name="Hotel Sol", city="Lima")
    b = HubSpotCompanyProperties(name="Hotel Luna", city="Lima")
    assert _is_same_company(a, b) is False


def test_is_same_company_different_city():
    a = HubSpotCompanyProperties(name="Hotel Sol", city="Lima")
    b = HubSpotCompanyProperties(name="Hotel Sol", city="Cusco")
    assert _is_same_company(a, b) is False


def test_is_same_company_empty_name():
    a = HubSpotCompanyProperties(name=None, city="Lima")
    b = HubSpotCompanyProperties(name="Hotel Sol", city="Lima")
    assert _is_same_company(a, b) is False


def test_is_same_company_case_insensitive():
    a = HubSpotCompanyProperties(name="HOTEL SOL", city="Lima")
    b = HubSpotCompanyProperties(name="hotel sol", city="lima")
    assert _is_same_company(a, b) is True


def test_is_same_company_missing_city_still_matches():
    """If one side has no city, they can still match on name."""
    a = HubSpotCompanyProperties(name="Hotel Sol", city="Lima")
    b = HubSpotCompanyProperties(name="Hotel Sol", city=None)
    assert _is_same_company(a, b) is True


def test_is_same_company_different_country():
    a = HubSpotCompanyProperties(name="Hotel Sol", country="Peru")
    b = HubSpotCompanyProperties(name="Hotel Sol", country="Chile")
    assert _is_same_company(a, b) is False


# --- id_hotel conflict handling in _process_company ---


@pytest.fixture
def enrichment_service():
    """EnrichmentService with AsyncMock dependencies for conflict tests."""
    hs = AsyncMock(spec=HubSpotService)
    hs.create_note.return_value = None
    hs.create_contact.return_value = "new-contact-id"
    hs.get_associated_contacts.return_value = []

    gp = AsyncMock(spec=GooglePlacesService)

    return EnrichmentService(hubspot=hs, google_places=gp, overwrite=False), hs, gp


def _company(company_id="48328838322", name="Hotel Sol", city="Lima", country="Peru"):
    return HubSpotCompany(
        id=company_id,
        properties=HubSpotCompanyProperties(
            name=name, city=city, country=country, agente="datos",
        ),
    )


def _google_place(place_id="ChIJ_test"):
    from app.schemas.google_places import DisplayName
    return GooglePlace(
        id=place_id,
        displayName=DisplayName(text="Hotel Sol"),
        formattedAddress="Calle 1, Lima, Peru",
        addressComponents=[
            {"longText": "Lima", "shortText": "Lima", "types": ["locality"]},
            {"longText": "Peru", "shortText": "PE", "types": ["country"]},
        ],
    )


@pytest.mark.asyncio
async def test_id_hotel_conflict_merge(enrichment_service):
    """VALIDATION_ERROR + same company → merge + retry update + enrichment completes."""
    svc, hs, gp = enrichment_service

    company = _company()
    hs.get_company.return_value = _company(company_id="51090765207", name="Hotel Sol", city="Lima", country="Peru")
    gp.text_search.return_value = _google_place()

    # First update_company call raises VALIDATION_ERROR, subsequent calls succeed
    call_count = 0
    async def _update_side_effect(cid, props):
        nonlocal call_count
        call_count += 1
        if call_count == 2:  # Second call is the enrichment update (first is pendiente)
            raise HubSpotError(
                'Cannot set id_hotel=ChIJ_test on 48328838322. 51090765207 already has that value.',
                status_code=400,
            )
    hs.update_company.side_effect = _update_side_effect
    hs.merge_companies.return_value = None

    result = await svc._process_company(company)

    assert result.status == "enriched"
    hs.merge_companies.assert_awaited_once_with("48328838322", "51090765207")
    # Enrichment note + merge note = 2 notes created
    assert hs.create_note.await_count == 2


@pytest.mark.asyncio
async def test_id_hotel_conflict_no_merge(enrichment_service):
    """VALIDATION_ERROR + different company → no merge, id_hotel dropped, enrichment completes."""
    svc, hs, gp = enrichment_service

    company = _company()
    hs.get_company.return_value = _company(company_id="51090765207", name="Hotel Luna", city="Lima", country="Peru")
    gp.text_search.return_value = _google_place()

    call_count = 0
    async def _update_side_effect(cid, props):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise HubSpotError(
                'Cannot set id_hotel=ChIJ_test on 48328838322. 51090765207 already has that value.',
                status_code=400,
            )
    hs.update_company.side_effect = _update_side_effect
    hs.merge_companies.return_value = None

    result = await svc._process_company(company)

    assert result.status == "enriched"
    hs.merge_companies.assert_not_awaited()
    # Enrichment note + conflict note = 2 notes created
    assert hs.create_note.await_count == 2


@pytest.mark.asyncio
async def test_id_hotel_conflict_merge_fails(enrichment_service):
    """VALIDATION_ERROR + merge fails → drop id_hotel, enrichment still completes."""
    svc, hs, gp = enrichment_service

    company = _company()
    hs.get_company.return_value = _company(company_id="51090765207", name="Hotel Sol", city="Lima", country="Peru")
    gp.text_search.return_value = _google_place()

    call_count = 0
    async def _update_side_effect(cid, props):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise HubSpotError(
                'Cannot set id_hotel=ChIJ_test on 48328838322. 51090765207 already has that value.',
                status_code=400,
            )
    hs.update_company.side_effect = _update_side_effect
    hs.merge_companies.side_effect = HubSpotError("merge failed", status_code=400)

    result = await svc._process_company(company)

    assert result.status == "enriched"
    # Enrichment note still created (at least 1)
    assert hs.create_note.await_count >= 1


# --- Instagram integration tests ---


def _ig_data(
    business_phone=None, bio_phones=None, business_email=None,
    bio_emails=None, whatsapp=None, username="hoteltest",
):
    return InstagramData(
        username=username,
        full_name="Hotel Test",
        biography="Test bio",
        profile_url=f"https://www.instagram.com/{username}/",
        business_phone=business_phone,
        bio_phones=bio_phones or [],
        business_email=business_email,
        bio_emails=bio_emails or [],
        whatsapp=whatsapp,
    )


@pytest.mark.asyncio
async def test_instagram_contact_created(service, hubspot_mock):
    """Instagram with phone and email → creates '/ Instagram' contact."""
    ig = _ig_data(
        business_phone="+595211234567",
        business_email="reservas@hotel.com",
        whatsapp="+595981654321",
    )

    await service._create_contacts("C1", "Hotel IG", None, None, None, instagram_data=ig)

    hubspot_mock.create_contact.assert_awaited_once_with(
        "C1",
        {
            "firstname": "Recepcion Hotel IG",
            "lastname": "/ Instagram",
            "phone": "+595211234567",
            "email": "reservas@hotel.com",
            "mobilephone": "+595981654321",
            "hs_whatsapp_phone_number": "+595981654321",
        },
    )


@pytest.mark.asyncio
async def test_instagram_bio_phones_used(service, hubspot_mock):
    """Instagram bio_phones used when no business_phone."""
    ig = _ig_data(bio_phones=["+595981111111"])

    await service._create_contacts("C1", "Hotel IG", None, None, None, instagram_data=ig)

    hubspot_mock.create_contact.assert_awaited_once()
    call_args = hubspot_mock.create_contact.await_args.args[1]
    assert call_args["phone"] == "+595981111111"
    assert call_args["lastname"] == "/ Instagram"


@pytest.mark.asyncio
async def test_instagram_phone_dedup_against_google(service, hubspot_mock):
    """Instagram phone same as Google phone → no Instagram contact created."""
    place = _place(phone_intl="+595 21 123 4567")
    ig = _ig_data(business_phone="+595211234567")

    await service._create_contacts("C1", "Hotel Dup", place, None, None, instagram_data=ig)

    # Only Google contact created, no Instagram contact
    assert hubspot_mock.create_contact.await_count == 1
    call_args = hubspot_mock.create_contact.await_args.args[1]
    assert call_args["lastname"] == "/ Google"


@pytest.mark.asyncio
async def test_instagram_email_only_creates_contact(service, hubspot_mock):
    """Instagram email (no phone) → contact created with email."""
    ig = _ig_data(bio_emails=["info@hotel.com"])

    await service._create_contacts("C1", "Hotel IG", None, None, None, instagram_data=ig)

    hubspot_mock.create_contact.assert_awaited_once()
    call_args = hubspot_mock.create_contact.await_args.args[1]
    assert call_args["email"] == "info@hotel.com"
    assert "phone" not in call_args


@pytest.mark.asyncio
async def test_instagram_no_data_no_contact(service, hubspot_mock):
    """Instagram with no phone or email → no contact created."""
    ig = _ig_data()

    await service._create_contacts("C1", "Hotel IG", None, None, None, instagram_data=ig)

    hubspot_mock.create_contact.assert_not_awaited()


@pytest.mark.asyncio
async def test_instagram_url_triggers_instagram_scrape():
    """Website URL is instagram.com → calls instagram.scrape(), not website_scraper.scrape()."""
    hs = AsyncMock(spec=HubSpotService)
    hs.create_note.return_value = None
    hs.create_contact.return_value = "new-contact-id"
    hs.get_associated_contacts.return_value = []

    gp = AsyncMock(spec=GooglePlacesService)
    gp.text_search.return_value = GooglePlace(
        id="ChIJ_test",
        websiteUri="https://www.instagram.com/hotelitapua/",
        formattedAddress="Asunción, Paraguay",
        addressComponents=[
            {"longText": "Asunción", "shortText": "ASU", "types": ["locality"]},
            {"longText": "Paraguay", "shortText": "PY", "types": ["country"]},
        ],
    )

    ig_mock = AsyncMock(spec=InstagramService)
    ig_mock.scrape.return_value = _ig_data(business_phone="+595211234567")

    ws_mock = AsyncMock()

    svc = EnrichmentService(
        hubspot=hs, google_places=gp,
        website_scraper=ws_mock, instagram=ig_mock,
        overwrite=False,
    )

    company = _company(name="Hotel Itapúa", city="Asunción", country="Paraguay")
    result = await svc._process_company(company)

    assert result.status == "enriched"
    ig_mock.scrape.assert_awaited_once_with(
        "https://www.instagram.com/hotelitapua/",
        hotel_name="Hotel Itapúa", city="Asunción",
    )
    ws_mock.scrape.assert_not_awaited()  # website scraper not called for IG URLs
