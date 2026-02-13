import httpx
import pytest
import respx
from httpx import Response

from app.exceptions.custom import HubSpotError
from app.services.hubspot import COMPANY_URL, HubSpotService


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
