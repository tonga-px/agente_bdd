import httpx
import pytest
import respx
from httpx import Response

from app.exceptions.custom import HubSpotError
from app.services.hubspot import (
    COMPANY_URL,
    CONTACTS_URL,
    EMAILS_URL,
    MERGE_URL,
    HubSpotService,
)


COMPANY_ID = "12345"
COMPANY_ENDPOINT = f"{COMPANY_URL}/{COMPANY_ID}"


@respx.mock
@pytest.mark.asyncio
async def test_get_company_success():
    respx.get(COMPANY_ENDPOINT).mock(
        return_value=Response(
            200,
            json={
                "id": COMPANY_ID,
                "properties": {
                    "name": "Acme Corp",
                    "domain": None,
                    "phone": None,
                    "website": None,
                    "address": None,
                    "city": "Santiago",
                    "state": None,
                    "zip": None,
                    "country": "Chile",
                    "agente": "datos",
                    "id_hotel": None,
                },
            },
        )
    )

    async with httpx.AsyncClient() as client:
        service = HubSpotService(client, "test-token")
        company = await service.get_company(COMPANY_ID)

    assert company.id == COMPANY_ID
    assert company.properties.name == "Acme Corp"
    assert company.properties.city == "Santiago"


@respx.mock
@pytest.mark.asyncio
async def test_get_company_not_found():
    respx.get(COMPANY_ENDPOINT).mock(
        return_value=Response(404, text="not found")
    )

    async with httpx.AsyncClient() as client:
        service = HubSpotService(client, "test-token")
        with pytest.raises(HubSpotError) as exc_info:
            await service.get_company(COMPANY_ID)

    assert exc_info.value.status_code == 404


CONTACT_ID = "501"
CONTACT_ENDPOINT = f"{CONTACTS_URL}/{CONTACT_ID}"


@respx.mock
@pytest.mark.asyncio
async def test_create_contact_success():
    respx.post(CONTACTS_URL).mock(
        return_value=Response(201, json={"id": CONTACT_ID})
    )

    async with httpx.AsyncClient() as client:
        service = HubSpotService(client, "test-token")
        result = await service.create_contact(
            COMPANY_ID, {"firstname": "Juan", "email": "juan@test.cl"}
        )

    assert result == CONTACT_ID
    req = respx.calls.last.request
    import json
    body = json.loads(req.content)
    assert body["properties"]["firstname"] == "Juan"
    assert body["associations"][0]["to"]["id"] == COMPANY_ID
    assert body["associations"][0]["types"][0]["associationTypeId"] == 1


@respx.mock
@pytest.mark.asyncio
async def test_create_contact_error():
    respx.post(CONTACTS_URL).mock(
        return_value=Response(400, text="bad request")
    )

    async with httpx.AsyncClient() as client:
        service = HubSpotService(client, "test-token")
        with pytest.raises(HubSpotError) as exc_info:
            await service.create_contact(COMPANY_ID, {"email": "x@y.com"})

    assert exc_info.value.status_code == 400


@respx.mock
@pytest.mark.asyncio
async def test_update_contact_success():
    respx.patch(CONTACT_ENDPOINT).mock(
        return_value=Response(200, json={"id": CONTACT_ID})
    )

    async with httpx.AsyncClient() as client:
        service = HubSpotService(client, "test-token")
        await service.update_contact(CONTACT_ID, {"phone": "+56 9 9999"})

    req = respx.calls.last.request
    import json
    body = json.loads(req.content)
    assert body["properties"]["phone"] == "+56 9 9999"


@respx.mock
@pytest.mark.asyncio
async def test_update_contact_error():
    respx.patch(CONTACT_ENDPOINT).mock(
        return_value=Response(404, text="not found")
    )

    async with httpx.AsyncClient() as client:
        service = HubSpotService(client, "test-token")
        with pytest.raises(HubSpotError) as exc_info:
            await service.update_contact(CONTACT_ID, {"phone": "+56 9 9999"})

    assert exc_info.value.status_code == 404


# --- get_associated_emails 403 silencing tests ---

ASSOC_EMAILS_URL = "https://api.hubapi.com/crm/v4/objects/companies/12345/associations/emails"


@respx.mock
@pytest.mark.asyncio
async def test_email_403_disables_future_fetches():
    """First 403 returns partial results and disables future email fetches."""
    respx.get(ASSOC_EMAILS_URL).mock(
        return_value=Response(200, json={"results": [{"toObjectId": "e1"}, {"toObjectId": "e2"}]})
    )
    respx.get(f"{EMAILS_URL}/e1").mock(
        return_value=Response(200, json={"id": "e1", "properties": {"hs_email_subject": "Hello"}})
    )
    respx.get(f"{EMAILS_URL}/e2").mock(
        return_value=Response(403, text="Forbidden")
    )

    async with httpx.AsyncClient() as client:
        service = HubSpotService(client, "test-token")

        # First call: gets e1, hits 403 on e2 → returns partial
        emails = await service.get_associated_emails(COMPANY_ID)
        assert len(emails) == 1
        assert service._email_fetch_disabled is True

        # Second call: short-circuits, returns empty
        emails2 = await service.get_associated_emails(COMPANY_ID)
        assert emails2 == []


@respx.mock
@pytest.mark.asyncio
async def test_email_no_403_works_normally():
    """Normal flow without 403 — all emails fetched."""
    respx.get(ASSOC_EMAILS_URL).mock(
        return_value=Response(200, json={"results": [{"toObjectId": "e1"}]})
    )
    respx.get(f"{EMAILS_URL}/e1").mock(
        return_value=Response(200, json={"id": "e1", "properties": {"hs_email_subject": "Hi"}})
    )

    async with httpx.AsyncClient() as client:
        service = HubSpotService(client, "test-token")
        emails = await service.get_associated_emails(COMPANY_ID)

    assert len(emails) == 1
    assert service._email_fetch_disabled is False


@respx.mock
@pytest.mark.asyncio
async def test_email_other_error_still_warns():
    """Non-403 errors (e.g. 500) log a warning but don't disable fetching."""
    respx.get(ASSOC_EMAILS_URL).mock(
        return_value=Response(200, json={"results": [{"toObjectId": "e1"}]})
    )
    respx.get(f"{EMAILS_URL}/e1").mock(
        return_value=Response(500, text="Server Error")
    )

    async with httpx.AsyncClient() as client:
        service = HubSpotService(client, "test-token")
        emails = await service.get_associated_emails(COMPANY_ID)

    assert len(emails) == 0
    assert service._email_fetch_disabled is False


# --- merge_companies tests ---


@respx.mock
@pytest.mark.asyncio
async def test_merge_companies_success():
    respx.post(MERGE_URL).mock(
        return_value=Response(200, json={"id": COMPANY_ID})
    )

    async with httpx.AsyncClient() as client:
        service = HubSpotService(client, "test-token")
        await service.merge_companies(COMPANY_ID, "99999")

    req = respx.calls.last.request
    import json
    body = json.loads(req.content)
    assert body["primaryObjectId"] == COMPANY_ID
    assert body["objectIdToMerge"] == "99999"


@respx.mock
@pytest.mark.asyncio
async def test_merge_companies_error():
    respx.post(MERGE_URL).mock(
        return_value=Response(400, text="bad request")
    )

    async with httpx.AsyncClient() as client:
        service = HubSpotService(client, "test-token")
        with pytest.raises(HubSpotError) as exc_info:
            await service.merge_companies(COMPANY_ID, "99999")

    assert exc_info.value.status_code == 400


# --- delete_contact tests ---


@respx.mock
@pytest.mark.asyncio
async def test_delete_contact_success():
    respx.delete(CONTACT_ENDPOINT).mock(
        return_value=Response(204)
    )

    async with httpx.AsyncClient() as client:
        service = HubSpotService(client, "test-token")
        await service.delete_contact(CONTACT_ID)

    assert respx.calls.last.request.method == "DELETE"


@respx.mock
@pytest.mark.asyncio
async def test_delete_contact_not_found():
    respx.delete(CONTACT_ENDPOINT).mock(
        return_value=Response(404, text="not found")
    )

    async with httpx.AsyncClient() as client:
        service = HubSpotService(client, "test-token")
        with pytest.raises(HubSpotError) as exc_info:
            await service.delete_contact(CONTACT_ID)

    assert exc_info.value.status_code == 404
