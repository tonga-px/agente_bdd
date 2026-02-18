from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport


@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")
    monkeypatch.setenv("TRIPADVISOR_API_KEY", "test-ta-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-el-key")
    monkeypatch.setenv("ELEVENLABS_AGENT_ID", "test-agent-id")
    monkeypatch.setenv("ELEVENLABS_PHONE_NUMBER_ID", "test-phone-id")


@pytest.fixture
async def client(mock_env):
    from app.main import app, lifespan

    # Prevent real DuckDuckGo searches in integration tests
    async def _fake_to_thread(func, *args, **kwargs):
        return []

    async with lifespan(app):
        with patch("app.services.booking.asyncio.to_thread", side_effect=_fake_to_thread):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as c:
                yield c
