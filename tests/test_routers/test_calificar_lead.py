import asyncio
from unittest.mock import AsyncMock, patch

import respx
from httpx import AsyncClient, Response

# HubSpot URLs
HUBSPOT_SEARCH_URL = "https://api.hubapi.com/crm/v3/objects/companies/search"
HUBSPOT_COMPANY_URL = "https://api.hubapi.com/crm/v3/objects/companies/C1"
HUBSPOT_NOTES_URL = "https://api.hubapi.com/crm/v3/objects/notes"
HUBSPOT_ASSOC_CONTACTS = "https://api.hubapi.com/crm/v4/objects/companies/C1/associations/contacts"
HUBSPOT_ASSOC_NOTES = "https://api.hubapi.com/crm/v4/objects/companies/C1/associations/notes"
HUBSPOT_ASSOC_EMAILS = "https://api.hubapi.com/crm/v4/objects/companies/C1/associations/emails"
HUBSPOT_ASSOC_CALLS = "https://api.hubapi.com/crm/v4/objects/companies/C1/associations/calls"
HUBSPOT_ASSOC_COMMS = "https://api.hubapi.com/crm/v4/objects/companies/C1/associations/communications"


def _mock_company():
    respx.get(HUBSPOT_COMPANY_URL).mock(
        return_value=Response(200, json={
            "id": "C1",
            "properties": {
                "name": "Hotel Test",
                "city": "Santiago",
                "country": "Chile",
                "agente": "calificar_lead",
                "booking_url": "https://www.booking.com/hotel/cl/test.html",
            },
        })
    )


def _mock_empty_associations():
    respx.get(HUBSPOT_ASSOC_CONTACTS).mock(return_value=Response(200, json={"results": []}))
    respx.get(HUBSPOT_ASSOC_NOTES).mock(return_value=Response(200, json={"results": []}))
    respx.get(HUBSPOT_ASSOC_EMAILS).mock(return_value=Response(200, json={"results": []}))
    respx.get(HUBSPOT_ASSOC_CALLS).mock(return_value=Response(200, json={"results": []}))
    respx.get(HUBSPOT_ASSOC_COMMS).mock(return_value=Response(200, json={"results": []}))


async def submit_and_wait(client: AsyncClient, json=None, timeout: float = 5.0):
    """POST /calificar_lead -> 202, then poll GET /jobs/{id}."""
    resp = await client.post("/calificar_lead", json=json)
    assert resp.status_code == 202

    data = resp.json()
    job_id = data["job_id"]
    assert data["status"] == "pending"

    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
        status_resp = await client.get(f"/jobs/{job_id}")
        assert status_resp.status_code == 200
        job = status_resp.json()
        if job["status"] in ("completed", "failed"):
            return job

    raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")


@respx.mock
async def test_calificar_lead_full_flow(client):
    """Full integration: submit job, Claude analyzes, company updated."""
    _mock_company()
    _mock_empty_associations()
    respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))
    respx.post(HUBSPOT_NOTES_URL).mock(return_value=Response(200, json={"id": "note-1"}))

    with patch(
        "app.services.claude.ClaudeService.analyze",
        new_callable=AsyncMock,
        return_value={
            "cantidad_de_habitaciones": "20",
            "market_fit": "Conejo",
            "razonamiento": "20 habitaciones según notas.",
            "tipo_de_empresa": "Hotel",
            "resumen_interacciones": "- Llamada inicial realizada",
        },
    ):
        job = await submit_and_wait(client, json={"company_id": "C1"})

    assert job["status"] == "completed"
    result = job["result"]
    assert result["company_id"] == "C1"
    assert result["status"] == "completed"
    assert result["market_fit"] == "Conejo"
    assert result["rooms"] == "20"
    assert result["tipo_de_empresa"] == "Hotel"
    assert result["resumen_interacciones"] == "- Llamada inicial realizada"
    assert result["lifecyclestage"] == "lead"


@respx.mock
async def test_calificar_lead_503_without_config(client):
    """If Anthropic is not configured, endpoint returns 503."""
    from app.main import app
    original = app.state.calificar_lead_service
    app.state.calificar_lead_service = None

    resp = await client.post("/calificar_lead", json={"company_id": "C1"})
    assert resp.status_code == 503
    assert "Anthropic not configured" in resp.json()["detail"]

    app.state.calificar_lead_service = original


@respx.mock
async def test_calificar_lead_duplicate_rejected(client):
    """Second request for same company is rejected while first is running."""
    _mock_company()
    _mock_empty_associations()
    respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))

    # Make Claude slow so job stays running
    async def slow_analyze(*args, **kwargs):
        await asyncio.sleep(10)
        return {"cantidad_de_habitaciones": "10", "market_fit": "Hormiga", "razonamiento": "ok"}

    with patch(
        "app.services.claude.ClaudeService.analyze",
        new_callable=AsyncMock,
        side_effect=slow_analyze,
    ):
        # First request — accepted
        resp1 = await client.post("/calificar_lead", json={"company_id": "C1"})
        assert resp1.status_code == 202

        # Wait for job to start running
        await asyncio.sleep(0.1)

        # Second request — duplicate
        resp2 = await client.post("/calificar_lead", json={"company_id": "C1"})
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["status"] == "already_running"


@respx.mock
async def test_calificar_lead_error_flow(client):
    """When Claude fails, job completes with error status."""
    _mock_company()
    _mock_empty_associations()
    respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))
    respx.post(HUBSPOT_NOTES_URL).mock(return_value=Response(200, json={"id": "note-1"}))

    with patch(
        "app.services.claude.ClaudeService.analyze",
        new_callable=AsyncMock,
        return_value=None,
    ):
        job = await submit_and_wait(client, json={"company_id": "C1"})

    assert job["status"] == "completed"
    result = job["result"]
    assert result["status"] == "error"
    assert "no results" in result.get("message", "").lower()
