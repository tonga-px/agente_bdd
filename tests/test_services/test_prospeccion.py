import httpx
import pytest
import respx
from httpx import Response
from unittest.mock import AsyncMock, patch

from app.schemas.elevenlabs import (
    ConversationAnalysis,
    ConversationResponse,
    ConversationTranscriptEntry,
    OutboundCallResponse,
)
from app.schemas.hubspot import (
    HubSpotCompany,
    HubSpotCompanyProperties,
    HubSpotContact,
    HubSpotContactProperties,
    HubSpotNote,
)
from app.services.elevenlabs import ElevenLabsService
from app.services.hubspot import HubSpotService
from app.services.prospeccion import ProspeccionService


def _make_company(
    company_id="C1",
    name="Hotel Test",
    phone="+56 1 1111",
    city="Santiago",
    country="Chile",
    website="https://hoteltest.cl",
):
    return HubSpotCompany(
        id=company_id,
        properties=HubSpotCompanyProperties(
            name=name,
            phone=phone,
            city=city,
            country=country,
            website=website,
            agente="llamada_prospeccion",
        ),
    )


def _make_contact(contact_id="100", phone="+56 2 2222", mobile=None, firstname="Juan", lastname="Perez"):
    return HubSpotContact(
        id=contact_id,
        properties=HubSpotContactProperties(
            firstname=firstname,
            lastname=lastname,
            phone=phone,
            mobilephone=mobile,
            jobtitle="Director",
        ),
    )


def _done_conversation():
    return ConversationResponse(
        conversation_id="conv-1",
        status="done",
        transcript=[
            ConversationTranscriptEntry(role="agent", message="Hola"),
            ConversationTranscriptEntry(role="user", message="Buenos dias"),
        ],
        analysis=ConversationAnalysis(
            extracted_data={
                "hotel_name": "Hotel Test",
                "num_rooms": "80",
                "decision_maker_name": "Juan Perez",
                "decision_maker_phone": "+56 9 9999",
                "decision_maker_email": "juan@test.cl",
                "date_and_time": "Martes 15 a las 10:00",
            }
        ),
    )


@pytest.mark.asyncio
async def test_full_flow():
    """Happy path: company has phone, call connects, data extracted."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company()
    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.return_value = []
    hubspot.get_associated_emails.return_value = []
    hubspot.get_associated_contacts.return_value = []

    elevenlabs.start_outbound_call.return_value = OutboundCallResponse(
        success=True, conversation_id="conv-1"
    )

    done_conv = _done_conversation()
    # First call from _poll_conversation, second from post-poll fetch
    elevenlabs.get_conversation.side_effect = [done_conv, done_conv]

    service = ProspeccionService(hubspot, elevenlabs)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0):
        result = await service.run(company_id="C1")

    assert result.status == "completed"
    assert result.company_id == "C1"
    assert result.extracted_data.hotel_name == "Hotel Test"
    assert result.extracted_data.num_rooms == "80"
    assert result.transcript == "Agente: Hola\nHotel: Buenos dias"
    assert len(result.call_attempts) == 1
    assert result.call_attempts[0].status == "connected"

    # Verify HubSpot was updated
    hubspot.update_company.assert_called_once()
    update_args = hubspot.update_company.call_args
    props = update_args[0][1]
    assert props["agente"] == ""
    assert props["num_rooms"] == "80"
    assert props["decision_maker_name"] == "Juan Perez"

    # Verify note was created
    hubspot.create_note.assert_called_once()


@pytest.mark.asyncio
async def test_no_phone():
    """Company and contacts have no phone numbers."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company(phone=None)
    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.return_value = []
    hubspot.get_associated_emails.return_value = []
    hubspot.get_associated_contacts.return_value = []

    service = ProspeccionService(hubspot, elevenlabs)
    result = await service.run(company_id="C1")

    assert result.status == "no_phone"
    hubspot.update_company.assert_called_once_with("C1", {"agente": ""})
    elevenlabs.start_outbound_call.assert_not_called()


@pytest.mark.asyncio
async def test_fallback_to_contact_phone():
    """Company phone fails, contact phone succeeds."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company(phone="+56 1 1111")
    contact = _make_contact(phone="+56 2 2222")
    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.return_value = []
    hubspot.get_associated_emails.return_value = []
    hubspot.get_associated_contacts.return_value = [contact]

    # First call fails, second succeeds
    elevenlabs.start_outbound_call.side_effect = [
        OutboundCallResponse(success=False, message="No answer"),
        OutboundCallResponse(success=True, conversation_id="conv-2"),
    ]

    done_conv = _done_conversation()
    elevenlabs.get_conversation.side_effect = [done_conv, done_conv]

    service = ProspeccionService(hubspot, elevenlabs)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0):
        result = await service.run(company_id="C1")

    assert result.status == "completed"
    assert len(result.call_attempts) == 2
    assert result.call_attempts[0].status == "failed"
    assert result.call_attempts[0].phone_number == "+56 1 1111"
    assert result.call_attempts[1].status == "connected"
    assert result.call_attempts[1].phone_number == "+56 2 2222"


@pytest.mark.asyncio
async def test_all_failed():
    """All phone numbers fail."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company(phone="+56 1 1111")
    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.return_value = []
    hubspot.get_associated_emails.return_value = []
    hubspot.get_associated_contacts.return_value = []

    elevenlabs.start_outbound_call.return_value = OutboundCallResponse(
        success=False, message="No answer"
    )

    service = ProspeccionService(hubspot, elevenlabs)
    result = await service.run(company_id="C1")

    assert result.status == "all_failed"
    assert len(result.call_attempts) == 1
    hubspot.update_company.assert_called_once_with("C1", {"agente": ""})


@pytest.mark.asyncio
async def test_call_exception_is_caught():
    """Exception during call results in error attempt, not crash."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company(phone="+56 1 1111")
    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.return_value = []
    hubspot.get_associated_emails.return_value = []
    hubspot.get_associated_contacts.return_value = []

    elevenlabs.start_outbound_call.side_effect = Exception("Connection error")

    service = ProspeccionService(hubspot, elevenlabs)
    result = await service.run(company_id="C1")

    assert result.status == "all_failed"
    assert result.call_attempts[0].status == "error"
    assert "Connection error" in result.call_attempts[0].error


@pytest.mark.asyncio
async def test_context_fetch_failure_continues():
    """If fetching notes/emails/contacts fails, prospeccion continues."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company()
    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.side_effect = Exception("notes failed")
    hubspot.get_associated_emails.side_effect = Exception("emails failed")
    hubspot.get_associated_contacts.side_effect = Exception("contacts failed")

    elevenlabs.start_outbound_call.return_value = OutboundCallResponse(
        success=True, conversation_id="conv-1"
    )

    done_conv = _done_conversation()
    elevenlabs.get_conversation.side_effect = [done_conv, done_conv]

    service = ProspeccionService(hubspot, elevenlabs)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0):
        result = await service.run(company_id="C1")

    assert result.status == "completed"


@pytest.mark.asyncio
async def test_search_mode_no_companies():
    """When no company_id, searches for agente=llamada_prospeccion."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    hubspot.search_companies.return_value = []

    service = ProspeccionService(hubspot, elevenlabs)
    result = await service.run()

    assert result.status == "error"
    assert "No companies found" in result.message
    hubspot.search_companies.assert_called_once_with(agente_value="llamada_prospeccion")


@pytest.mark.asyncio
async def test_deduplicate_phones():
    """Duplicate phone numbers should be skipped."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company(phone="+56 1 1111")
    # Contact has same phone as company
    contact = _make_contact(phone="+56 1 1111", mobile="+56 3 3333")
    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.return_value = []
    hubspot.get_associated_emails.return_value = []
    hubspot.get_associated_contacts.return_value = [contact]

    # All calls fail so we see all attempts
    elevenlabs.start_outbound_call.return_value = OutboundCallResponse(
        success=False, message="No answer"
    )

    service = ProspeccionService(hubspot, elevenlabs)
    result = await service.run(company_id="C1")

    # Should only have 2 attempts (company phone + contact mobile), not 3
    assert len(result.call_attempts) == 2
    phones = [a.phone_number for a in result.call_attempts]
    assert "+56 1 1111" in phones
    assert "+56 3 3333" in phones


@pytest.mark.asyncio
async def test_note_failure_doesnt_block():
    """Note creation failure doesn't change status to error."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company()
    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.return_value = []
    hubspot.get_associated_emails.return_value = []
    hubspot.get_associated_contacts.return_value = []
    hubspot.create_note.side_effect = Exception("Note creation failed")

    elevenlabs.start_outbound_call.return_value = OutboundCallResponse(
        success=True, conversation_id="conv-1"
    )
    done_conv = _done_conversation()
    elevenlabs.get_conversation.side_effect = [done_conv, done_conv]

    service = ProspeccionService(hubspot, elevenlabs)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0):
        result = await service.run(company_id="C1")

    assert result.status == "completed"


@pytest.mark.asyncio
async def test_dynamic_variables_built_correctly():
    """Verify dynamic variables include contact/note/email summaries."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company(name="Hotel Paraiso", city="Lima", country="Peru", website="https://paraiso.pe")
    contact = _make_contact(firstname="Ana", lastname="Garcia")
    note = HubSpotNote(id="n1", properties={"hs_note_body": "<p>Client interested</p>"})

    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.return_value = [note]
    hubspot.get_associated_emails.return_value = []
    hubspot.get_associated_contacts.return_value = [contact]

    elevenlabs.start_outbound_call.return_value = OutboundCallResponse(
        success=True, conversation_id="conv-1"
    )
    done_conv = _done_conversation()
    elevenlabs.get_conversation.side_effect = [done_conv, done_conv]

    service = ProspeccionService(hubspot, elevenlabs)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0):
        await service.run(company_id="C1")

    call_args = elevenlabs.start_outbound_call.call_args
    dynamic_vars = call_args[0][1]
    assert dynamic_vars["hotel_name"] == "Hotel Paraiso"
    assert dynamic_vars["hotel_city"] == "Lima"
    assert dynamic_vars["hotel_country"] == "Peru"
    assert dynamic_vars["hotel_website"] == "https://paraiso.pe"
    assert "Ana Garcia (Director)" in dynamic_vars["known_contacts"]
    assert "Client interested" in dynamic_vars["recent_notes"]


@pytest.mark.asyncio
async def test_register_call_on_success():
    """After a successful call, audio is downloaded, uploaded, and call created."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company()
    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.return_value = []
    hubspot.get_associated_emails.return_value = []
    hubspot.get_associated_contacts.return_value = []
    hubspot.upload_file.return_value = "https://files.hubspot.com/call.mp3"

    elevenlabs.start_outbound_call.return_value = OutboundCallResponse(
        success=True, conversation_id="conv-1"
    )
    done_conv = _done_conversation()
    elevenlabs.get_conversation.side_effect = [done_conv, done_conv]
    elevenlabs.get_conversation_audio.return_value = b"fake-audio-data"

    service = ProspeccionService(hubspot, elevenlabs)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0):
        result = await service.run(company_id="C1")

    assert result.status == "completed"

    # Verify audio was downloaded
    elevenlabs.get_conversation_audio.assert_called_once_with("conv-1")

    # Verify file was uploaded
    hubspot.upload_file.assert_called_once()
    upload_args = hubspot.upload_file.call_args
    assert upload_args[0][0] == "call_C1_conv-1.mp3"
    assert upload_args[0][1] == b"fake-audio-data"

    # Verify call was created
    hubspot.create_call.assert_called_once()
    call_args = hubspot.create_call.call_args
    assert call_args[0][0] == "C1"
    props = call_args[0][1]
    assert props["hs_call_status"] == "COMPLETED"
    assert props["hs_call_direction"] == "OUTBOUND"
    assert props["hs_call_recording_url"] == "https://files.hubspot.com/call.mp3"
    assert "Hotel Test" in props["hs_call_title"]
    assert props["hs_call_to_number"] == "+56 1 1111"


@pytest.mark.asyncio
async def test_register_call_failure_doesnt_block():
    """If audio download fails, prospeccion still completes."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company()
    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.return_value = []
    hubspot.get_associated_emails.return_value = []
    hubspot.get_associated_contacts.return_value = []

    elevenlabs.start_outbound_call.return_value = OutboundCallResponse(
        success=True, conversation_id="conv-1"
    )
    done_conv = _done_conversation()
    elevenlabs.get_conversation.side_effect = [done_conv, done_conv]
    elevenlabs.get_conversation_audio.side_effect = Exception("Audio download failed")

    service = ProspeccionService(hubspot, elevenlabs)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0):
        result = await service.run(company_id="C1")

    assert result.status == "completed"
    hubspot.upload_file.assert_not_called()
    hubspot.create_call.assert_not_called()
