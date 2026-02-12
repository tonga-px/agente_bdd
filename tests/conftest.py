import httpx
import pytest
import respx
from fastapi.testclient import TestClient


@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")


@pytest.fixture
def client(mock_env):
    from app.main import app
    with TestClient(app) as c:
        yield c
