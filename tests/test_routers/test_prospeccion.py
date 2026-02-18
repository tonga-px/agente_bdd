import asyncio

import respx
from httpx import AsyncClient, Response

# HubSpot URLs
HUBSPOT_SEARCH_URL = "https://api.hubapi.com/crm/v3/objects/companies/search"
HUBSPOT_COMPANY_URL = "https://api.hubapi.com/crm/v3/objects/companies/C1"
HUBSPOT_NOTES_URL = "https://api.hubapi.com/crm/v3/objects/notes"
HUBSPOT_ASSOC_CONTACTS = "https://api.hubapi.com/crm/v4/objects/companies/C1/associations/contacts"
HUBSPOT_ASSOC_NOTES = "https://api.hubapi.com/crm/v4/objects/companies/C1/associations/notes"
HUBSPOT_ASSOC_EMAILS = "https://api.hubapi.com/crm/v4/objects/companies/C1/associations/emails"

# HubSpot new URLs
HUBSPOT_FILES_URL = "https://api.hubapi.com/files/v3/files"
HUBSPOT_CALLS_URL = "https://api.hubapi.com/crm/v3/objects/calls"

# ElevenLabs URLs
ELEVENLABS_OUTBOUND = "https://api.elevenlabs.io/v1/convai/sip-trunk/outbound-call"
ELEVENLABS_CONVERSATION = "https://api.elevenlabs.io/v1/convai/conversations/conv-1"
ELEVENLABS_AUDIO = "https://api.elevenlabs.io/v1/convai/conversations/conv-1/audio"


def _mock_company():
    respx.get(HUBSPOT_COMPANY_URL).mock(
        return_value=Response(
            200,
            json={
                "id": "C1",
                "properties": {
                    "name": "Hotel Test",
                    "phone": "+56 1 1111",
                    "city": "Santiago",
                    "country": "Chile",
                    "agente": "llamada_prospeccion",
                },
            },
        )
    )


def _mock_empty_associations():
    respx.get(HUBSPOT_ASSOC_CONTACTS).mock(return_value=Response(200, json={"results": []}))
    respx.get(HUBSPOT_ASSOC_NOTES).mock(return_value=Response(200, json={"results": []}))
    respx.get(HUBSPOT_ASSOC_EMAILS).mock(return_value=Response(200, json={"results": []}))


def _mock_successful_call():
    respx.post(ELEVENLABS_OUTBOUND).mock(
        return_value=Response(
            200,
            json={"success": True, "conversation_id": "conv-1"},
        )
    )
    respx.get(ELEVENLABS_CONVERSATION).mock(
        return_value=Response(
            200,
            json={
                "conversation_id": "conv-1",
                "status": "done",
                "transcript": [
                    {"role": "agent", "message": "Hola"},
                    {"role": "user", "message": "Buenos dias"},
                ],
                "analysis": {
                    "data_collection_results": {
                        "hotel_name": {"value": "Hotel Test"},
                        "num_rooms": {"value": "50"},
                    }
                },
            },
        )
    )
    respx.get(ELEVENLABS_AUDIO).mock(
        return_value=Response(200, content=b"fake-audio-bytes")
    )
    respx.post(HUBSPOT_FILES_URL).mock(
        return_value=Response(200, json={"id": "file-1", "url": "https://files.hubspot.com/call.mp3"})
    )
    respx.post(HUBSPOT_CALLS_URL).mock(
        return_value=Response(200, json={"id": "call-1"})
    )


async def submit_prospeccion_and_wait(client: AsyncClient, json=None, timeout: float = 5.0):
    """POST /llamada_prospeccion → 202, then poll GET /jobs/{job_id}."""
    resp = await client.post("/llamada_prospeccion", json=json)
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
async def test_prospeccion_full_flow(client):
    _mock_company()
    _mock_empty_associations()
    _mock_successful_call()

    # Mock HubSpot update + note creation
    respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))
    respx.post(HUBSPOT_NOTES_URL).mock(return_value=Response(200, json={"id": "note-1"}))

    job = await submit_prospeccion_and_wait(client, json={"company_id": "C1"}, timeout=10.0)
    assert job["status"] == "completed"

    result = job["result"]
    assert result["company_id"] == "C1"
    assert result["status"] == "completed"
    assert result["extracted_data"]["hotel_name"] == "Hotel Test"
    assert result["extracted_data"]["num_rooms"] == "50"
    assert len(result["call_attempts"]) == 1
    assert result["call_attempts"][0]["status"] == "connected"


@respx.mock
async def test_prospeccion_no_phone(client):
    respx.get(HUBSPOT_COMPANY_URL).mock(
        return_value=Response(
            200,
            json={
                "id": "C1",
                "properties": {
                    "name": "Hotel Sin Telefono",
                    "phone": None,
                    "city": "Santiago",
                    "country": "Chile",
                    "agente": "llamada_prospeccion",
                },
            },
        )
    )
    _mock_empty_associations()
    respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))

    job = await submit_prospeccion_and_wait(client, json={"company_id": "C1"})
    assert job["status"] == "completed"

    result = job["result"]
    assert result["status"] == "no_phone"


@respx.mock
async def test_prospeccion_duplicate_rejected(client):
    """Second request for the same company is rejected while first is running."""
    _mock_company()
    _mock_empty_associations()
    # Slow call — keeps the job running
    respx.post(ELEVENLABS_OUTBOUND).mock(side_effect=lambda _: Response(
        200, json={"success": True, "conversation_id": "conv-1"},
    ))
    respx.get(ELEVENLABS_CONVERSATION).mock(
        return_value=Response(200, json={"conversation_id": "conv-1", "status": "processing"}),
    )

    # First request — accepted
    resp1 = await client.post("/llamada_prospeccion", json={"company_id": "C1"})
    assert resp1.status_code == 202

    # Wait a bit for job to start running
    await asyncio.sleep(0.1)

    # Second request — duplicate, returns 200 with existing job_id
    resp2 = await client.post("/llamada_prospeccion", json={"company_id": "C1"})
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["status"] == "already_running"
    assert "job_id" in data2


@respx.mock
async def test_prospeccion_503_without_config(client, monkeypatch):
    """If ElevenLabs is not configured, endpoint returns 503."""
    # Set prospeccion_service to None to simulate no config
    from app.main import app
    app.state.prospeccion_service = None

    resp = await client.post("/llamada_prospeccion", json={"company_id": "C1"})
    assert resp.status_code == 503
    assert "ElevenLabs not configured" in resp.json()["detail"]
