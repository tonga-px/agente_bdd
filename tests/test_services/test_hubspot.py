import httpx
import pytest
import respx
from httpx import Response

from app.exceptions.custom import HubSpotError
from app.services.hubspot import COMPANY_URL, CONTACTS_URL, HubSpotService


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
