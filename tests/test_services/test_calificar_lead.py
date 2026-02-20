import pytest
import respx
from httpx import Response
from unittest.mock import AsyncMock

from app.schemas.hubspot import (
    HubSpotCompany,
    HubSpotCompanyProperties,
    HubSpotContact,
    HubSpotContactProperties,
    HubSpotLead,
    HubSpotLeadProperties,
    HubSpotNote,
)
from app.schemas.responses import LeadAction
from app.services.calificar_lead import (
    CalificarLeadService,
    _compute_market_fit,
    _fix_encoding,
)
from app.services.claude import ClaudeService
from app.services.hubspot import HubSpotService

# HubSpot URLs
HUBSPOT_SEARCH_URL = "https://api.hubapi.com/crm/v3/objects/companies/search"
HUBSPOT_COMPANY_URL = "https://api.hubapi.com/crm/v3/objects/companies/C1"
HUBSPOT_NOTES_URL = "https://api.hubapi.com/crm/v3/objects/notes"
HUBSPOT_TASKS_URL = "https://api.hubapi.com/crm/v3/objects/tasks"
HUBSPOT_LEADS_URL = "https://api.hubapi.com/crm/v3/objects/leads"
HUBSPOT_ASSOC_CONTACTS = "https://api.hubapi.com/crm/v4/objects/companies/C1/associations/contacts"
HUBSPOT_ASSOC_NOTES = "https://api.hubapi.com/crm/v4/objects/companies/C1/associations/notes"
HUBSPOT_ASSOC_EMAILS = "https://api.hubapi.com/crm/v4/objects/companies/C1/associations/emails"
HUBSPOT_ASSOC_CALLS = "https://api.hubapi.com/crm/v4/objects/companies/C1/associations/calls"
HUBSPOT_ASSOC_COMMS = "https://api.hubapi.com/crm/v4/objects/companies/C1/associations/communications"
HUBSPOT_ASSOC_LEADS = "https://api.hubapi.com/crm/v4/objects/companies/C1/associations/leads"


def _make_company(
    company_id="C1",
    name="Hotel Test",
    city="Santiago",
    country="Chile",
    market_fit=None,
    cantidad_de_habitaciones=None,
    booking_url="https://www.booking.com/hotel/cl/test.html",
    tipo_de_empresa=None,
):
    return HubSpotCompany(
        id=company_id,
        properties=HubSpotCompanyProperties(
            name=name,
            city=city,
            country=country,
            agente="calificar_lead",
            market_fit=market_fit,
            cantidad_de_habitaciones=cantidad_de_habitaciones,
            booking_url=booking_url,
            tipo_de_empresa=tipo_de_empresa,
        ),
    )


def _mock_empty_associations():
    respx.get(HUBSPOT_ASSOC_CONTACTS).mock(return_value=Response(200, json={"results": []}))
    respx.get(HUBSPOT_ASSOC_NOTES).mock(return_value=Response(200, json={"results": []}))
    respx.get(HUBSPOT_ASSOC_EMAILS).mock(return_value=Response(200, json={"results": []}))
    respx.get(HUBSPOT_ASSOC_CALLS).mock(return_value=Response(200, json={"results": []}))
    respx.get(HUBSPOT_ASSOC_COMMS).mock(return_value=Response(200, json={"results": []}))


def _mock_company_get(booking_url="https://www.booking.com/hotel/cl/test.html"):
    respx.get(HUBSPOT_COMPANY_URL).mock(
        return_value=Response(200, json={
            "id": "C1",
            "properties": {
                "name": "Hotel Test",
                "city": "Santiago",
                "country": "Chile",
                "agente": "calificar_lead",
                "booking_url": booking_url,
            },
        })
    )


@respx.mock
async def test_run_completed_conejo():
    """Full flow: Claude returns Conejo, company is updated, note is created."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        claude.analyze = AsyncMock(return_value={
            "cantidad_de_habitaciones": "20",
            "market_fit": "Conejo",
            "razonamiento": "El hotel tiene 20 habitaciones según la nota.",
            "tipo_de_empresa": "Hotel",
            "resumen_interacciones": "- Llamada realizada el 2024-01-15",
        })
        service = CalificarLeadService(hubspot, claude)

        _mock_company_get()
        _mock_empty_associations()
        respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))
        respx.post(HUBSPOT_NOTES_URL).mock(return_value=Response(200, json={"id": "note-1"}))

        result = await service.run(company_id="C1")

    assert result.status == "completed"
    assert result.market_fit == "Conejo"
    assert result.rooms == "20"
    assert result.reasoning == "El hotel tiene 20 habitaciones según la nota."
    assert result.tipo_de_empresa == "Hotel"
    assert result.resumen_interacciones == "- Llamada realizada el 2024-01-15"
    assert result.lifecyclestage == "lead"
    assert result.lead_actions == []
    assert result.note is not None


@respx.mock
async def test_run_no_fit_updates_leads():
    """When market_fit is 'No es FIT', leads are updated and tasks created."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        claude.analyze = AsyncMock(return_value={
            "cantidad_de_habitaciones": "3",
            "market_fit": "No es FIT",
            "razonamiento": "Solo tiene 3 habitaciones.",
        })
        service = CalificarLeadService(hubspot, claude)

        _mock_company_get()
        _mock_empty_associations()
        respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))
        respx.post(HUBSPOT_NOTES_URL).mock(return_value=Response(200, json={"id": "note-1"}))

        # Mock leads
        respx.get(HUBSPOT_ASSOC_LEADS).mock(
            return_value=Response(200, json={
                "results": [{"toObjectId": "L1"}],
            })
        )
        respx.get(f"{HUBSPOT_LEADS_URL}/L1").mock(
            return_value=Response(200, json={
                "id": "L1",
                "properties": {
                    "hubspot_owner_id": "owner-1",
                    "hs_lead_name": "Lead Test",
                    "hs_pipeline_stage": "123",
                },
            })
        )
        respx.patch(f"{HUBSPOT_LEADS_URL}/L1").mock(return_value=Response(200, json={}))
        respx.post(HUBSPOT_TASKS_URL).mock(return_value=Response(200, json={"id": "task-1"}))

        result = await service.run(company_id="C1")

    assert result.status == "completed"
    assert result.market_fit == "No es FIT"
    assert result.lifecyclestage == "subscriber"
    # Should have stage_updated + task_created actions
    assert len(result.lead_actions) == 2
    assert result.lead_actions[0].action == "stage_updated"
    assert result.lead_actions[1].action == "task_created"


@respx.mock
async def test_run_claude_returns_none():
    """When Claude returns None, result is error."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        claude.analyze = AsyncMock(return_value=None)
        service = CalificarLeadService(hubspot, claude)

        _mock_company_get()
        _mock_empty_associations()
        respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))

        result = await service.run(company_id="C1")

    assert result.status == "error"
    assert "no results" in result.message.lower()


@respx.mock
async def test_run_no_companies_found():
    """When no companies have agente='calificar_lead'."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        service = CalificarLeadService(hubspot, claude)

        respx.post(HUBSPOT_SEARCH_URL).mock(
            return_value=Response(200, json={"results": []})
        )

        result = await service.run()

    assert result.status == "error"
    assert "No companies" in result.message


@respx.mock
async def test_resolve_next_company_id():
    """resolve_next_company_id returns the first company ID."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        service = CalificarLeadService(hubspot, claude)

        respx.post(HUBSPOT_SEARCH_URL).mock(
            return_value=Response(200, json={
                "results": [{"id": "C42", "properties": {"name": "Hotel 42"}}],
            })
        )

        cid = await service.resolve_next_company_id()

    assert cid == "C42"


@respx.mock
async def test_resolve_next_company_id_none():
    """resolve_next_company_id returns None when no companies found."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        service = CalificarLeadService(hubspot, claude)

        respx.post(HUBSPOT_SEARCH_URL).mock(
            return_value=Response(200, json={"results": []})
        )

        cid = await service.resolve_next_company_id()

    assert cid is None


@respx.mock
async def test_run_invalid_market_fit_recomputed():
    """When Claude returns invalid market_fit, compute_market_fit_with_type is used."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        claude.analyze = AsyncMock(return_value={
            "cantidad_de_habitaciones": "30",
            "market_fit": "Grande",  # invalid — ignored by new logic
            "razonamiento": "Es un hotel grande.",
        })
        service = CalificarLeadService(hubspot, claude)

        _mock_company_get()
        _mock_empty_associations()
        respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))
        respx.post(HUBSPOT_NOTES_URL).mock(return_value=Response(200, json={"id": "note-1"}))

        result = await service.run(company_id="C1")

    assert result.status == "completed"
    assert result.market_fit == "Elefante"  # computed from 30 rooms + has booking
    assert result.rooms == "30"


@respx.mock
async def test_run_no_fit_no_leads():
    """No es FIT with no leads => no lead actions."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        claude.analyze = AsyncMock(return_value={
            "cantidad_de_habitaciones": "2",
            "market_fit": "No es FIT",
            "razonamiento": "Muy pocas habitaciones.",
        })
        service = CalificarLeadService(hubspot, claude)

        _mock_company_get()
        _mock_empty_associations()
        respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))
        respx.post(HUBSPOT_NOTES_URL).mock(return_value=Response(200, json={"id": "note-1"}))

        # No leads
        respx.get(HUBSPOT_ASSOC_LEADS).mock(
            return_value=Response(200, json={"results": []})
        )

        result = await service.run(company_id="C1")

    assert result.status == "completed"
    assert result.market_fit == "No es FIT"
    assert result.lead_actions == []


@respx.mock
async def test_run_exception_clears_agente():
    """When an exception occurs, agente is cleared and error note created."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        claude.analyze = AsyncMock(side_effect=RuntimeError("boom"))
        service = CalificarLeadService(hubspot, claude)

        _mock_company_get()
        _mock_empty_associations()
        # First call sets pendiente, second clears agente after error
        respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))
        respx.post(HUBSPOT_NOTES_URL).mock(return_value=Response(200, json={"id": "note-1"}))

        result = await service.run(company_id="C1")

    assert result.status == "error"
    assert "boom" in result.message


# Unit tests for _compute_market_fit
def test_compute_market_fit_no_fit():
    assert _compute_market_fit(1) == "No es FIT"
    assert _compute_market_fit(4) == "No es FIT"


def test_compute_market_fit_hormiga():
    assert _compute_market_fit(5) == "Hormiga"
    assert _compute_market_fit(13) == "Hormiga"


def test_compute_market_fit_conejo():
    assert _compute_market_fit(14) == "Conejo"
    assert _compute_market_fit(27) == "Conejo"


def test_compute_market_fit_elefante():
    assert _compute_market_fit(28) == "Elefante"
    assert _compute_market_fit(100) == "Elefante"


@respx.mock
async def test_build_user_prompt_includes_context():
    """Verify the prompt includes company data, notes, calls, contacts."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        service = CalificarLeadService(hubspot, claude)

        company = _make_company()
        notes = [HubSpotNote(id="n1", properties={"hs_note_body": "Has 15 rooms", "hs_timestamp": "2024-01-01"})]
        calls = [{"properties": {"hs_call_body": "Called hotel", "hs_call_direction": "OUTBOUND", "hs_timestamp": "2024-01-02", "hs_call_status": "COMPLETED"}}]
        contacts = [HubSpotContact(id="c1", properties=HubSpotContactProperties(firstname="Juan", lastname="Perez", jobtitle="Director"))]

        prompt = service._build_user_prompt(company, notes, calls, [], contacts)

    assert "Hotel Test" in prompt
    assert "Santiago" in prompt
    assert "Has 15 rooms" in prompt
    assert "Called hotel" in prompt
    assert "Juan Perez" in prompt
    assert "Booking URL:" in prompt


@respx.mock
async def test_no_fit_lead_without_owner_skips_task():
    """Lead without hubspot_owner_id: stage is updated but no task created."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        claude.analyze = AsyncMock(return_value={
            "cantidad_de_habitaciones": "3",
            "market_fit": "No es FIT",
            "razonamiento": "Solo 3 hab.",
        })
        service = CalificarLeadService(hubspot, claude)

        _mock_company_get()
        _mock_empty_associations()
        respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))
        respx.post(HUBSPOT_NOTES_URL).mock(return_value=Response(200, json={"id": "note-1"}))

        # Lead without owner
        respx.get(HUBSPOT_ASSOC_LEADS).mock(
            return_value=Response(200, json={"results": [{"toObjectId": "L2"}]})
        )
        respx.get(f"{HUBSPOT_LEADS_URL}/L2").mock(
            return_value=Response(200, json={
                "id": "L2",
                "properties": {
                    "hubspot_owner_id": None,
                    "hs_lead_name": "Lead Sin Owner",
                    "hs_pipeline_stage": "123",
                },
            })
        )
        respx.patch(f"{HUBSPOT_LEADS_URL}/L2").mock(return_value=Response(200, json={}))

        result = await service.run(company_id="C1")

    assert result.status == "completed"
    # Only stage_updated, no task_created
    assert len(result.lead_actions) == 1
    assert result.lead_actions[0].action == "stage_updated"


def test_fix_encoding_double_encoded():
    # "Según" double-encoded: UTF-8 bytes of "ú" (0xC3 0xBA) stored as Latin-1
    double_encoded = "Seg\u00c3\u00ban la nota"
    assert _fix_encoding(double_encoded) == "Según la nota"


def test_fix_encoding_already_clean():
    clean = "Según la nota del hotel"
    assert _fix_encoding(clean) == clean


@respx.mock
async def test_reasoning_encoding_fixed():
    """Double-encoded reasoning from Claude is fixed in the response."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        claude.analyze = AsyncMock(return_value={
            "cantidad_de_habitaciones": "10",
            "market_fit": "Hormiga",
            "razonamiento": "Seg\u00c3\u00ban la nota, tiene 10 habitaciones.",
        })
        service = CalificarLeadService(hubspot, claude)

        _mock_company_get()
        _mock_empty_associations()
        respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))
        respx.post(HUBSPOT_NOTES_URL).mock(return_value=Response(200, json={"id": "note-1"}))

        result = await service.run(company_id="C1")

    assert result.status == "completed"
    assert "Según" in result.reasoning
    assert "\u00c3" not in result.reasoning


@respx.mock
async def test_prompt_fixes_double_encoded_notes():
    """Double-encoded note bodies are fixed before sending to Claude."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        service = CalificarLeadService(hubspot, claude)

        company = _make_company()
        # Note body with double-encoded "ó"
        notes = [HubSpotNote(id="n1", properties={
            "hs_note_body": "Informaci\u00c3\u00b3n del hotel",
            "hs_timestamp": "2024-01-01",
        })]

        prompt = service._build_user_prompt(company, notes, [], [], [])

    assert "Información del hotel" in prompt
    assert "\u00c3" not in prompt


# --- New tests for booking/tipo_de_empresa/lifecyclestage ---


@respx.mock
async def test_no_booking_forces_no_fit():
    """Company without booking_url → always 'No es FIT' regardless of rooms."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        claude.analyze = AsyncMock(return_value={
            "cantidad_de_habitaciones": "50",
            "market_fit": "Elefante",
            "razonamiento": "Hotel grande.",
            "tipo_de_empresa": "Hotel",
        })
        service = CalificarLeadService(hubspot, claude)

        # Company without booking_url
        _mock_company_get(booking_url=None)
        _mock_empty_associations()
        respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))
        respx.post(HUBSPOT_NOTES_URL).mock(return_value=Response(200, json={"id": "note-1"}))
        respx.get(HUBSPOT_ASSOC_LEADS).mock(
            return_value=Response(200, json={"results": []})
        )

        result = await service.run(company_id="C1")

    assert result.status == "completed"
    assert result.market_fit == "No es FIT"
    assert result.lifecyclestage == "subscriber"


@respx.mock
async def test_hostel_under_5_hormiga():
    """Hostel with <5 rooms + booking → Hormiga (exception)."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        claude.analyze = AsyncMock(return_value={
            "cantidad_de_habitaciones": "3",
            "market_fit": "No es FIT",
            "razonamiento": "Hostel pequeño.",
            "tipo_de_empresa": "Hostel",
        })
        service = CalificarLeadService(hubspot, claude)

        _mock_company_get()
        _mock_empty_associations()
        respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))
        respx.post(HUBSPOT_NOTES_URL).mock(return_value=Response(200, json={"id": "note-1"}))

        result = await service.run(company_id="C1")

    assert result.status == "completed"
    assert result.market_fit == "Hormiga"
    assert result.tipo_de_empresa == "Hostel"
    assert result.lifecyclestage == "lead"


@respx.mock
async def test_hostel_no_booking_no_fit():
    """Hostel without booking → No es FIT (booking rule wins over exception)."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        claude.analyze = AsyncMock(return_value={
            "cantidad_de_habitaciones": "3",
            "market_fit": "No es FIT",
            "razonamiento": "Hostel sin booking.",
            "tipo_de_empresa": "Hostel",
        })
        service = CalificarLeadService(hubspot, claude)

        _mock_company_get(booking_url=None)
        _mock_empty_associations()
        respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))
        respx.post(HUBSPOT_NOTES_URL).mock(return_value=Response(200, json={"id": "note-1"}))
        respx.get(HUBSPOT_ASSOC_LEADS).mock(
            return_value=Response(200, json={"results": []})
        )

        result = await service.run(company_id="C1")

    assert result.status == "completed"
    assert result.market_fit == "No es FIT"
    assert result.lifecyclestage == "subscriber"


@respx.mock
async def test_lifecyclestage_subscriber():
    """No es FIT → lifecyclestage = subscriber."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        claude.analyze = AsyncMock(return_value={
            "cantidad_de_habitaciones": "2",
            "market_fit": "No es FIT",
            "razonamiento": "Pocas habitaciones.",
        })
        service = CalificarLeadService(hubspot, claude)

        _mock_company_get()
        _mock_empty_associations()
        respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))
        respx.post(HUBSPOT_NOTES_URL).mock(return_value=Response(200, json={"id": "note-1"}))
        respx.get(HUBSPOT_ASSOC_LEADS).mock(
            return_value=Response(200, json={"results": []})
        )

        result = await service.run(company_id="C1")

    assert result.lifecyclestage == "subscriber"


@respx.mock
async def test_lifecyclestage_lead():
    """Any non-'No es FIT' → lifecyclestage = lead."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        claude.analyze = AsyncMock(return_value={
            "cantidad_de_habitaciones": "15",
            "market_fit": "Conejo",
            "razonamiento": "Hotel mediano.",
        })
        service = CalificarLeadService(hubspot, claude)

        _mock_company_get()
        _mock_empty_associations()
        respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))
        respx.post(HUBSPOT_NOTES_URL).mock(return_value=Response(200, json={"id": "note-1"}))

        result = await service.run(company_id="C1")

    assert result.lifecyclestage == "lead"


@respx.mock
async def test_tipo_de_empresa_in_response():
    """tipo_de_empresa from Claude appears in response."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        claude.analyze = AsyncMock(return_value={
            "cantidad_de_habitaciones": "20",
            "market_fit": "Conejo",
            "razonamiento": "Hotel boutique.",
            "tipo_de_empresa": "Boutique hotel",
        })
        service = CalificarLeadService(hubspot, claude)

        _mock_company_get()
        _mock_empty_associations()
        respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))
        respx.post(HUBSPOT_NOTES_URL).mock(return_value=Response(200, json={"id": "note-1"}))

        result = await service.run(company_id="C1")

    assert result.tipo_de_empresa == "Boutique hotel"


@respx.mock
async def test_invalid_tipo_de_empresa_ignored():
    """Invalid tipo_de_empresa from Claude is set to None."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        claude.analyze = AsyncMock(return_value={
            "cantidad_de_habitaciones": "20",
            "market_fit": "Conejo",
            "razonamiento": "Es un hotel.",
            "tipo_de_empresa": "Posada",  # not in valid set
        })
        service = CalificarLeadService(hubspot, claude)

        _mock_company_get()
        _mock_empty_associations()
        respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))
        respx.post(HUBSPOT_NOTES_URL).mock(return_value=Response(200, json={"id": "note-1"}))

        result = await service.run(company_id="C1")

    assert result.tipo_de_empresa is None


@respx.mock
async def test_whatsapp_in_prompt():
    """WhatsApp messages appear in the prompt."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        service = CalificarLeadService(hubspot, claude)

        company = _make_company()
        whatsapp_msgs = [
            {
                "properties": {
                    "hs_communication_channel_type": "WHATS_APP",
                    "hs_communication_body": "Hola, quisiera reservar",
                    "hs_body_preview": None,
                    "hs_timestamp": "2024-06-01T10:00:00Z",
                },
            },
            {
                "properties": {
                    "hs_communication_channel_type": "WHATS_APP",
                    "hs_communication_body": None,
                    "hs_body_preview": None,
                    "hs_timestamp": "2024-06-02T10:00:00Z",
                },
            },
        ]

        prompt = service._build_user_prompt(
            company, [], [], [], [], whatsapp_msgs=whatsapp_msgs,
        )

    assert "## WhatsApp" in prompt
    assert "Hola, quisiera reservar" in prompt
    assert "(sin contenido)" in prompt


@respx.mock
async def test_hoteles_com_data_in_prompt():
    """Hoteles.com data appears in the prompt."""
    import httpx
    async with httpx.AsyncClient() as client:
        hubspot = HubSpotService(client, "test-token")
        claude = ClaudeService(api_key="test-key")
        service = CalificarLeadService(hubspot, claude)

        company = _make_company()
        hoteles_data = "Hotel Test - 4 estrellas, 25 habitaciones, piscina y spa"

        prompt = service._build_user_prompt(
            company, [], [], [], [], hoteles_data=hoteles_data,
        )

    assert "## Datos de Hoteles.com" in prompt
    assert "25 habitaciones" in prompt
