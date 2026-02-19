from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.claude import ClaudeService


def _make_response(text: str):
    """Build a mock Anthropic response."""
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


@pytest.fixture
def service():
    return ClaudeService(api_key="test-key")


async def test_analyze_success(service):
    mock_resp = _make_response('{"cantidad_de_habitaciones": "25", "market_fit": "Conejo", "razonamiento": "tiene 25 hab"}')
    with patch.object(service._client.messages, "create", new_callable=AsyncMock, return_value=mock_resp):
        result = await service.analyze("system", "user")
    assert result == {"cantidad_de_habitaciones": "25", "market_fit": "Conejo", "razonamiento": "tiene 25 hab"}


async def test_analyze_with_markdown_fences(service):
    mock_resp = _make_response('```json\n{"market_fit": "Hormiga"}\n```')
    with patch.object(service._client.messages, "create", new_callable=AsyncMock, return_value=mock_resp):
        result = await service.analyze("system", "user")
    assert result == {"market_fit": "Hormiga"}


async def test_analyze_with_surrounding_text(service):
    mock_resp = _make_response('Here is the result: {"market_fit": "Elefante"} hope this helps!')
    with patch.object(service._client.messages, "create", new_callable=AsyncMock, return_value=mock_resp):
        result = await service.analyze("system", "user")
    assert result == {"market_fit": "Elefante"}


async def test_analyze_api_error_returns_none(service):
    with patch.object(service._client.messages, "create", new_callable=AsyncMock, side_effect=Exception("API error")):
        result = await service.analyze("system", "user")
    assert result is None


async def test_analyze_unparseable_returns_none(service):
    mock_resp = _make_response("I cannot determine the market fit for this hotel.")
    with patch.object(service._client.messages, "create", new_callable=AsyncMock, return_value=mock_resp):
        result = await service.analyze("system", "user")
    assert result is None


def test_try_parse_json_direct():
    assert ClaudeService._try_parse_json('{"a": 1}') == {"a": 1}


def test_try_parse_json_fenced():
    assert ClaudeService._try_parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_try_parse_json_fallback_regex():
    assert ClaudeService._try_parse_json('blah {"a": 1} blah') == {"a": 1}


def test_try_parse_json_invalid():
    assert ClaudeService._try_parse_json("no json here") is None
