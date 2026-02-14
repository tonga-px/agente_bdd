import httpx
import pytest
import respx
from httpx import Response

from app.exceptions.custom import ElevenLabsError, RateLimitError
from app.services.elevenlabs import (
    CONVERSATIONS_URL,
    ElevenLabsService,
    OUTBOUND_CALL_URL,
)


@respx.mock
@pytest.mark.asyncio
async def test_start_outbound_call_success():
    respx.post(OUTBOUND_CALL_URL).mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "conversation_id": "conv-123",
                "sip_call_id": "sip-456",
            },
        )
    )

    async with httpx.AsyncClient() as client:
        service = ElevenLabsService(client, "key", "agent-1", "phone-1")
        resp = await service.start_outbound_call("+1234567890", {"hotel_name": "Test"})

    assert resp.success is True
    assert resp.conversation_id == "conv-123"
    assert resp.sip_call_id == "sip-456"


@respx.mock
@pytest.mark.asyncio
async def test_start_outbound_call_no_dynamic_vars():
    respx.post(OUTBOUND_CALL_URL).mock(
        return_value=Response(200, json={"success": True, "conversation_id": "conv-789"})
    )

    async with httpx.AsyncClient() as client:
        service = ElevenLabsService(client, "key", "agent-1", "phone-1")
        resp = await service.start_outbound_call("+1234567890")

    assert resp.success is True
    assert resp.conversation_id == "conv-789"

    # Verify no conversation_initiation_client_data was sent
    sent = respx.calls[0].request
    import json
    body = json.loads(sent.content)
    assert "conversation_initiation_client_data" not in body


@respx.mock
@pytest.mark.asyncio
async def test_start_outbound_call_rate_limit():
    respx.post(OUTBOUND_CALL_URL).mock(return_value=Response(429, text="Rate limited"))

    async with httpx.AsyncClient() as client:
        service = ElevenLabsService(client, "key", "agent-1", "phone-1")
        with pytest.raises(RateLimitError):
            await service.start_outbound_call("+1234567890")


@respx.mock
@pytest.mark.asyncio
async def test_start_outbound_call_error():
    respx.post(OUTBOUND_CALL_URL).mock(return_value=Response(500, text="Server error"))

    async with httpx.AsyncClient() as client:
        service = ElevenLabsService(client, "key", "agent-1", "phone-1")
        with pytest.raises(ElevenLabsError) as exc_info:
            await service.start_outbound_call("+1234567890")

    assert exc_info.value.status_code == 500


@respx.mock
@pytest.mark.asyncio
async def test_get_conversation_success():
    respx.get(f"{CONVERSATIONS_URL}/conv-123").mock(
        return_value=Response(
            200,
            json={
                "conversation_id": "conv-123",
                "status": "done",
                "transcript": [
                    {"role": "agent", "message": "Hola"},
                    {"role": "user", "message": "Buenos dias"},
                ],
                "analysis": {
                    "extracted_data": {
                        "hotel_name": "Hotel Test",
                        "num_rooms": "50",
                    }
                },
            },
        )
    )

    async with httpx.AsyncClient() as client:
        service = ElevenLabsService(client, "key", "agent-1", "phone-1")
        resp = await service.get_conversation("conv-123")

    assert resp.conversation_id == "conv-123"
    assert resp.status == "done"
    assert len(resp.transcript) == 2
    assert resp.transcript[0].role == "agent"
    assert resp.analysis.extracted_data["hotel_name"] == "Hotel Test"


@respx.mock
@pytest.mark.asyncio
async def test_get_conversation_rate_limit():
    respx.get(f"{CONVERSATIONS_URL}/conv-123").mock(
        return_value=Response(429, text="Rate limited")
    )

    async with httpx.AsyncClient() as client:
        service = ElevenLabsService(client, "key", "agent-1", "phone-1")
        with pytest.raises(RateLimitError):
            await service.get_conversation("conv-123")


@respx.mock
@pytest.mark.asyncio
async def test_get_conversation_error():
    respx.get(f"{CONVERSATIONS_URL}/conv-123").mock(
        return_value=Response(404, text="Not found")
    )

    async with httpx.AsyncClient() as client:
        service = ElevenLabsService(client, "key", "agent-1", "phone-1")
        with pytest.raises(ElevenLabsError) as exc_info:
            await service.get_conversation("conv-123")

    assert exc_info.value.status_code == 404


@respx.mock
@pytest.mark.asyncio
async def test_get_conversation_audio_success():
    audio_bytes = b"\xff\xfb\x90\x00" * 100  # fake mp3 data
    respx.get(f"{CONVERSATIONS_URL}/conv-123/audio").mock(
        return_value=Response(200, content=audio_bytes)
    )

    async with httpx.AsyncClient() as client:
        service = ElevenLabsService(client, "key", "agent-1", "phone-1")
        result = await service.get_conversation_audio("conv-123")

    assert result == audio_bytes
    assert isinstance(result, bytes)


@respx.mock
@pytest.mark.asyncio
async def test_get_conversation_audio_rate_limit():
    respx.get(f"{CONVERSATIONS_URL}/conv-123/audio").mock(
        return_value=Response(429, text="Rate limited")
    )

    async with httpx.AsyncClient() as client:
        service = ElevenLabsService(client, "key", "agent-1", "phone-1")
        with pytest.raises(RateLimitError):
            await service.get_conversation_audio("conv-123")


@respx.mock
@pytest.mark.asyncio
async def test_get_conversation_audio_error():
    respx.get(f"{CONVERSATIONS_URL}/conv-123/audio").mock(
        return_value=Response(404, text="Not found")
    )

    async with httpx.AsyncClient() as client:
        service = ElevenLabsService(client, "key", "agent-1", "phone-1")
        with pytest.raises(ElevenLabsError) as exc_info:
            await service.get_conversation_audio("conv-123")

    assert exc_info.value.status_code == 404
