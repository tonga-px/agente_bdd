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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")


@pytest.fixture
async def client(mock_env):
    from app.main import app, lifespan

    async with lifespan(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            yield c
