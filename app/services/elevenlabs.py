import logging

import httpx

from app.exceptions.custom import ElevenLabsError, RateLimitError
from app.schemas.elevenlabs import ConversationResponse, OutboundCallResponse

logger = logging.getLogger(__name__)

OUTBOUND_CALL_URL = "https://api.elevenlabs.io/v1/convai/sip-trunk/outbound-call"
CONVERSATIONS_URL = "https://api.elevenlabs.io/v1/convai/conversations"


class ElevenLabsService:
    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        agent_id: str,
        phone_number_id: str,
    ):
        self._client = client
        self._headers = {"xi-api-key": api_key}
        self._agent_id = agent_id
        self._phone_number_id = phone_number_id

    async def start_outbound_call(
        self, to_number: str, dynamic_variables: dict | None = None
    ) -> OutboundCallResponse:
        payload: dict = {
            "agent_id": self._agent_id,
            "agent_phone_number_id": self._phone_number_id,
            "to_number": to_number,
        }
        if dynamic_variables:
            payload["conversation_initiation_client_data"] = {
                "dynamic_variables": dynamic_variables,
            }

        logger.info("Starting outbound call to %s", to_number)
        resp = await self._client.post(
            OUTBOUND_CALL_URL, json=payload, headers=self._headers
        )

        if resp.status_code == 429:
            raise RateLimitError("ElevenLabs")
        if resp.status_code >= 400:
            raise ElevenLabsError(resp.text, status_code=resp.status_code)

        data = resp.json()
        logger.info(
            "Outbound call started: conversation_id=%s",
            data.get("conversation_id"),
        )
        return OutboundCallResponse(**data)

    async def get_conversation(
        self, conversation_id: str
    ) -> ConversationResponse:
        url = f"{CONVERSATIONS_URL}/{conversation_id}"
        resp = await self._client.get(url, headers=self._headers)

        if resp.status_code == 429:
            raise RateLimitError("ElevenLabs")
        if resp.status_code >= 400:
            raise ElevenLabsError(resp.text, status_code=resp.status_code)

        return ConversationResponse(**resp.json())

    async def get_conversation_audio(
        self, conversation_id: str
    ) -> bytes:
        url = f"{CONVERSATIONS_URL}/{conversation_id}/audio"
        resp = await self._client.get(url, headers=self._headers)

        if resp.status_code == 429:
            raise RateLimitError("ElevenLabs")
        if resp.status_code >= 400:
            raise ElevenLabsError(resp.text, status_code=resp.status_code)

        return resp.content
