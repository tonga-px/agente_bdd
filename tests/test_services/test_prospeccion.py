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
from app.schemas.google_places import AddressComponent, GooglePlace
from app.services.elevenlabs import ElevenLabsService
from app.services.google_places import GooglePlacesService
from app.services.hubspot import HubSpotService
from app.services.prospeccion import (
    ProspeccionService,
    _compute_market_fit,
    _describe_error,
    _parse_num_rooms,
    _split_name,
)


def _make_company(
    company_id="C1",
    name="Hotel Test",
    phone="+56 1 1111",
    city="Santiago",
    country="Chile",
    website="https://hoteltest.cl",
    address=None,
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
            address=address,
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
            data_collection_results={
                "hotel_name": {"value": "Hotel Test"},
                "num_rooms": {"value": "80"},
                "decision_maker_name": {"value": "Juan Perez"},
                "decision_maker_phone": {"value": "+56 9 9999"},
                "decision_maker_email": {"value": "juan@test.cl"},
                "date_and_time": {"value": "Martes 15 a las 10:00"},
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

    # Verify HubSpot was updated (only agente cleared, extracted data goes to note)
    hubspot.update_company.assert_called_once()
    update_args = hubspot.update_company.call_args
    props = update_args[0][1]
    assert props["agente"] == ""

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
async def test_deduplicate_phones_ignores_formatting():
    """Same digits with different formatting should be deduplicated."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company(phone="+56323203958")
    contact = _make_contact(phone="+56 32 320 3958", mobile="+56 9 8888")
    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.return_value = []
    hubspot.get_associated_emails.return_value = []
    hubspot.get_associated_contacts.return_value = [contact]

    elevenlabs.start_outbound_call.return_value = OutboundCallResponse(
        success=False, message="No answer"
    )

    service = ProspeccionService(hubspot, elevenlabs)
    result = await service.run(company_id="C1")

    # +56323203958 and +56 32 320 3958 are the same number — only 2 attempts
    assert len(result.call_attempts) == 2
    phones = [a.phone_number for a in result.call_attempts]
    assert "+56323203958" in phones
    assert "+56 9 8888" in phones


@pytest.mark.asyncio
async def test_sip_486_retry_succeeds():
    """SIP 486 (Busy Here) triggers a retry after delay, second attempt connects."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company(phone="+56 1 1111")
    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.return_value = []
    hubspot.get_associated_emails.return_value = []
    hubspot.get_associated_contacts.return_value = []

    # First call: SIP 486, second call: success
    elevenlabs.start_outbound_call.side_effect = [
        OutboundCallResponse(success=False, conversation_id="c-busy", message="SIP 486: Busy Here"),
        OutboundCallResponse(success=True, conversation_id="conv-1"),
    ]

    done_conv = _done_conversation()
    elevenlabs.get_conversation.side_effect = [done_conv, done_conv]

    service = ProspeccionService(hubspot, elevenlabs)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0), \
         patch("app.services.prospeccion.SIP_BUSY_RETRY_DELAY", 0):
        result = await service.run(company_id="C1")

    assert result.status == "completed"
    assert len(result.call_attempts) == 2
    assert result.call_attempts[0].status == "failed"
    assert "486" in result.call_attempts[0].error
    assert result.call_attempts[1].status == "connected"


@pytest.mark.asyncio
async def test_sip_486_retry_also_fails():
    """SIP 486 retry also fails — no infinite loop, moves on."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company(phone="+56 1 1111")
    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.return_value = []
    hubspot.get_associated_emails.return_value = []
    hubspot.get_associated_contacts.return_value = []

    # Both attempts: SIP 486
    elevenlabs.start_outbound_call.return_value = OutboundCallResponse(
        success=False, conversation_id="c-busy", message="SIP 486: Busy Here"
    )

    service = ProspeccionService(hubspot, elevenlabs)

    with patch("app.services.prospeccion.SIP_BUSY_RETRY_DELAY", 0):
        result = await service.run(company_id="C1")

    assert result.status == "all_failed"
    assert len(result.call_attempts) == 2
    assert all("486" in a.error for a in result.call_attempts)


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

    company = _make_company(name="Hotel Paraiso", city="Lima", country="Peru", website="https://paraiso.pe", address="Av. Larco 123")
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
    assert dynamic_vars["hotel_address"] == "Av. Larco 123"
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


# --- _split_name tests ---

def test_split_name_two_parts():
    assert _split_name("Juan García") == ("Juan", "García")


def test_split_name_single():
    assert _split_name("Juan") == ("Juan", "")


def test_split_name_multiple_parts():
    assert _split_name("María de los Angeles López") == ("María", "de los Angeles López")


def test_split_name_empty():
    assert _split_name("") == ("", "")


def test_split_name_whitespace():
    assert _split_name("  Juan  García  ") == ("Juan", "García")


# --- _upsert_decision_maker_contact tests ---

@pytest.mark.asyncio
async def test_contact_created_when_decision_maker_data():
    """A new contact is created when decision maker data is extracted."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company()
    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.return_value = []
    hubspot.get_associated_emails.return_value = []
    hubspot.get_associated_contacts.return_value = []
    hubspot.create_contact.return_value = "new-contact-id"

    elevenlabs.start_outbound_call.return_value = OutboundCallResponse(
        success=True, conversation_id="conv-1"
    )
    done_conv = _done_conversation()
    elevenlabs.get_conversation.side_effect = [done_conv, done_conv]

    service = ProspeccionService(hubspot, elevenlabs)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0):
        result = await service.run(company_id="C1")

    assert result.status == "completed"
    hubspot.create_contact.assert_called_once()
    call_args = hubspot.create_contact.call_args
    assert call_args[0][0] == "C1"
    props = call_args[0][1]
    assert props["firstname"] == "Juan"
    assert props["lastname"] == "Perez"
    assert props["phone"] == "+56 9 9999"
    assert props["email"] == "juan@test.cl"


@pytest.mark.asyncio
async def test_contact_updated_when_email_matches():
    """Existing contact is updated (empty fields only) when email matches."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company()
    # Existing contact has same email but no phone
    existing_contact = HubSpotContact(
        id="existing-100",
        properties=HubSpotContactProperties(
            firstname="Juan",
            lastname="Perez",
            email="juan@test.cl",
            phone=None,
        ),
    )
    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.return_value = []
    hubspot.get_associated_emails.return_value = []
    hubspot.get_associated_contacts.return_value = [existing_contact]

    elevenlabs.start_outbound_call.return_value = OutboundCallResponse(
        success=True, conversation_id="conv-1"
    )
    done_conv = _done_conversation()
    elevenlabs.get_conversation.side_effect = [done_conv, done_conv]

    service = ProspeccionService(hubspot, elevenlabs)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0):
        result = await service.run(company_id="C1")

    assert result.status == "completed"
    hubspot.create_contact.assert_not_called()
    hubspot.update_contact.assert_called_once()
    call_args = hubspot.update_contact.call_args
    assert call_args[0][0] == "existing-100"
    props = call_args[0][1]
    # Only phone should be updated (firstname, lastname, email already exist)
    assert props["phone"] == "+56 9 9999"
    assert "firstname" not in props
    assert "lastname" not in props
    assert "email" not in props


@pytest.mark.asyncio
async def test_no_contact_upsert_when_no_decision_maker_data():
    """No contact created when no decision maker data is extracted."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company()
    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.return_value = []
    hubspot.get_associated_emails.return_value = []
    hubspot.get_associated_contacts.return_value = []

    # Conversation with no decision maker data
    conv_no_dm = ConversationResponse(
        conversation_id="conv-1",
        status="done",
        transcript=[
            ConversationTranscriptEntry(role="agent", message="Hola"),
        ],
        analysis=ConversationAnalysis(
            data_collection_results={
                "hotel_name": {"value": "Hotel Test"},
            }
        ),
    )

    elevenlabs.start_outbound_call.return_value = OutboundCallResponse(
        success=True, conversation_id="conv-1"
    )
    elevenlabs.get_conversation.side_effect = [conv_no_dm, conv_no_dm]

    service = ProspeccionService(hubspot, elevenlabs)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0):
        result = await service.run(company_id="C1")

    assert result.status == "completed"
    hubspot.create_contact.assert_not_called()
    hubspot.update_contact.assert_not_called()


@pytest.mark.asyncio
async def test_contact_upsert_failure_doesnt_block():
    """If contact upsert fails, prospeccion still completes."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company()
    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.return_value = []
    hubspot.get_associated_emails.return_value = []
    hubspot.get_associated_contacts.return_value = []
    hubspot.create_contact.side_effect = Exception("Contact creation failed")

    elevenlabs.start_outbound_call.return_value = OutboundCallResponse(
        success=True, conversation_id="conv-1"
    )
    done_conv = _done_conversation()
    elevenlabs.get_conversation.side_effect = [done_conv, done_conv]

    service = ProspeccionService(hubspot, elevenlabs)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0):
        result = await service.run(company_id="C1")

    assert result.status == "completed"


# --- _parse_num_rooms tests ---

def test_parse_num_rooms_plain_number():
    assert _parse_num_rooms("80") == 80


def test_parse_num_rooms_with_text():
    assert _parse_num_rooms("80 habitaciones") == 80


def test_parse_num_rooms_approx():
    assert _parse_num_rooms("aprox. 50") == 50


def test_parse_num_rooms_no_sabe():
    assert _parse_num_rooms("no sabe") is None


def test_parse_num_rooms_empty():
    assert _parse_num_rooms("") is None


# --- _compute_market_fit tests ---

def test_compute_market_fit_hormiga_min():
    assert _compute_market_fit(1) == "Hormiga"


def test_compute_market_fit_hormiga_max():
    assert _compute_market_fit(13) == "Hormiga"


def test_compute_market_fit_conejo_min():
    assert _compute_market_fit(14) == "Conejo"


def test_compute_market_fit_conejo_max():
    assert _compute_market_fit(27) == "Conejo"


def test_compute_market_fit_elefante_min():
    assert _compute_market_fit(28) == "Elefante"


def test_compute_market_fit_elefante_large():
    assert _compute_market_fit(100) == "Elefante"


# --- market_fit integration tests ---

def _make_google_place_with_state(state: str = "Región Metropolitana"):
    return GooglePlace(
        formattedAddress="Av. Test 123",
        addressComponents=[
            AddressComponent(
                longText=state,
                shortText=state[:2],
                types=["administrative_area_level_1"],
            ),
            AddressComponent(
                longText="Santiago",
                shortText="Santiago",
                types=["locality"],
            ),
        ],
    )


def _setup_successful_call(hubspot, elevenlabs, company, conversation=None):
    """Helper to set up mocks for a successful call flow."""
    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.return_value = []
    hubspot.get_associated_emails.return_value = []
    hubspot.get_associated_contacts.return_value = []

    elevenlabs.start_outbound_call.return_value = OutboundCallResponse(
        success=True, conversation_id="conv-1"
    )
    conv = conversation or _done_conversation()
    elevenlabs.get_conversation.side_effect = [conv, conv]


@pytest.mark.asyncio
async def test_market_fit_written_to_hubspot():
    """num_rooms=80 → market_fit=Elefante written to HubSpot."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company()
    _setup_successful_call(hubspot, elevenlabs, company)

    service = ProspeccionService(hubspot, elevenlabs)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0):
        result = await service.run(company_id="C1")

    assert result.status == "completed"
    update_args = hubspot.update_company.call_args
    props = update_args[0][1]
    assert props["market_fit"] == "Elefante"
    assert props["agente"] == ""


@pytest.mark.asyncio
async def test_market_fit_skipped_when_already_set():
    """Company already has market_fit → not overwritten."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = HubSpotCompany(
        id="C1",
        properties=HubSpotCompanyProperties(
            name="Hotel Test",
            phone="+56 1 1111",
            agente="llamada_prospeccion",
            market_fit="Conejo",
        ),
    )
    _setup_successful_call(hubspot, elevenlabs, company)

    service = ProspeccionService(hubspot, elevenlabs)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0):
        result = await service.run(company_id="C1")

    assert result.status == "completed"
    update_args = hubspot.update_company.call_args
    props = update_args[0][1]
    assert "market_fit" not in props


@pytest.mark.asyncio
async def test_market_fit_unparseable_num_rooms():
    """num_rooms='no sabe' → market_fit not included."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company()
    conv = ConversationResponse(
        conversation_id="conv-1",
        status="done",
        transcript=[
            ConversationTranscriptEntry(role="agent", message="Hola"),
        ],
        analysis=ConversationAnalysis(
            data_collection_results={
                "hotel_name": {"value": "Hotel Test"},
                "num_rooms": {"value": "no sabe"},
            }
        ),
    )
    _setup_successful_call(hubspot, elevenlabs, company, conversation=conv)

    service = ProspeccionService(hubspot, elevenlabs)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0):
        result = await service.run(company_id="C1")

    assert result.status == "completed"
    update_args = hubspot.update_company.call_args
    props = update_args[0][1]
    assert "market_fit" not in props


# --- state lookup integration tests ---

@pytest.mark.asyncio
async def test_state_lookup_via_google_places():
    """State empty + google_places available → state written to HubSpot."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)
    google_places = AsyncMock(spec=GooglePlacesService)

    company = _make_company()
    _setup_successful_call(hubspot, elevenlabs, company)
    google_places.text_search.return_value = _make_google_place_with_state("Valparaíso")

    service = ProspeccionService(hubspot, elevenlabs, google_places=google_places)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0):
        result = await service.run(company_id="C1")

    assert result.status == "completed"
    update_args = hubspot.update_company.call_args
    props = update_args[0][1]
    assert props["state"] == "Valparaíso"


@pytest.mark.asyncio
async def test_state_lookup_uses_id_hotel():
    """Company has id_hotel → uses get_place_details, not text_search."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)
    google_places = AsyncMock(spec=GooglePlacesService)

    company = HubSpotCompany(
        id="C1",
        properties=HubSpotCompanyProperties(
            name="Hotel Test",
            phone="+56 1 1111",
            agente="llamada_prospeccion",
            id_hotel="ChIJ12345",
        ),
    )
    _setup_successful_call(hubspot, elevenlabs, company)
    google_places.get_place_details.return_value = _make_google_place_with_state("Biobío")

    service = ProspeccionService(hubspot, elevenlabs, google_places=google_places)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0):
        result = await service.run(company_id="C1")

    assert result.status == "completed"
    google_places.get_place_details.assert_called_once_with("ChIJ12345")
    google_places.text_search.assert_not_called()
    update_args = hubspot.update_company.call_args
    props = update_args[0][1]
    assert props["state"] == "Biobío"


@pytest.mark.asyncio
async def test_state_skipped_when_already_set():
    """Company already has state → Google Places not called."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)
    google_places = AsyncMock(spec=GooglePlacesService)

    company = HubSpotCompany(
        id="C1",
        properties=HubSpotCompanyProperties(
            name="Hotel Test",
            phone="+56 1 1111",
            agente="llamada_prospeccion",
            state="Existing State",
        ),
    )
    _setup_successful_call(hubspot, elevenlabs, company)

    service = ProspeccionService(hubspot, elevenlabs, google_places=google_places)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0):
        result = await service.run(company_id="C1")

    assert result.status == "completed"
    google_places.text_search.assert_not_called()
    google_places.get_place_details.assert_not_called()
    update_args = hubspot.update_company.call_args
    props = update_args[0][1]
    assert "state" not in props


@pytest.mark.asyncio
async def test_state_lookup_failure_doesnt_block():
    """Google Places fails → status still completed, market_fit still written."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)
    google_places = AsyncMock(spec=GooglePlacesService)

    company = _make_company()
    _setup_successful_call(hubspot, elevenlabs, company)
    google_places.text_search.side_effect = Exception("Google Places down")

    service = ProspeccionService(hubspot, elevenlabs, google_places=google_places)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0):
        result = await service.run(company_id="C1")

    assert result.status == "completed"
    update_args = hubspot.update_company.call_args
    props = update_args[0][1]
    assert props["market_fit"] == "Elefante"
    assert "state" not in props


@pytest.mark.asyncio
async def test_no_google_places_skips_state():
    """Without google_places service → state not looked up (backward compat)."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company()
    _setup_successful_call(hubspot, elevenlabs, company)

    service = ProspeccionService(hubspot, elevenlabs)

    with patch("app.services.prospeccion.POLL_INTERVAL", 0):
        result = await service.run(company_id="C1")

    assert result.status == "completed"
    update_args = hubspot.update_company.call_args
    props = update_args[0][1]
    assert "state" not in props


# --- error note tests ---


@pytest.mark.asyncio
async def test_no_phone_creates_error_note():
    """no_phone path creates an error note in HubSpot."""
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
    hubspot.create_note.assert_called_once()
    note_body = hubspot.create_note.call_args[0][1]
    assert "Error - Agente Llamada Prospeccion" in note_body
    assert "no_phone" in note_body


@pytest.mark.asyncio
async def test_all_failed_creates_error_note():
    """all_failed path creates an error note with attempt details."""
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
    assert result.note is not None
    hubspot.create_note.assert_called_once()
    note_body = hubspot.create_note.call_args[0][1]
    assert "Llamada de Prospeccion" in note_body
    assert "No se pudo conectar" in note_body
    assert "+56 1 1111" in note_body
    assert "No answer" in note_body


@pytest.mark.asyncio
async def test_error_note_failure_doesnt_block_no_phone():
    """If error note creation fails in no_phone path, result is still returned."""
    hubspot = AsyncMock(spec=HubSpotService)
    elevenlabs = AsyncMock(spec=ElevenLabsService)

    company = _make_company(phone=None)
    hubspot.get_company.return_value = company
    hubspot.get_associated_notes.return_value = []
    hubspot.get_associated_emails.return_value = []
    hubspot.get_associated_contacts.return_value = []
    hubspot.create_note.side_effect = Exception("Note failed")

    service = ProspeccionService(hubspot, elevenlabs)
    result = await service.run(company_id="C1")

    assert result.status == "no_phone"


# --- _describe_error tests ---


def test_describe_error_read_timeout():
    assert "Tiempo de espera" in _describe_error(httpx.ReadTimeout("timed out"))


def test_describe_error_connect_timeout():
    assert "No se pudo conectar" in _describe_error(httpx.ConnectTimeout("connect timed out"))


def test_describe_error_connect_error():
    assert "conexión" in _describe_error(httpx.ConnectError("refused"))


def test_describe_error_generic_with_message():
    assert _describe_error(Exception("Something broke")) == "Something broke"


def test_describe_error_generic_empty_message():
    assert _describe_error(Exception()) == "Exception"
