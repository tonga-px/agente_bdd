"""Tests for HacerTareasService (mocked HubSpot, no real I/O)."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from httpx import Response

from app.services.hacer_tareas import HacerTareasService
from app.services.hubspot import (
    COMPANY_URL,
    NOTES_URL,
    TASK_ASSOCIATIONS_URL,
    TASKS_SEARCH_URL,
    TASKS_URL,
    HubSpotService,
)


def _task(task_id, subject, status="NOT_STARTED", timestamp="1740000000000"):
    return {
        "id": task_id,
        "properties": {
            "hs_task_subject": subject,
            "hs_task_status": status,
            "hs_timestamp": timestamp,
        },
    }


def _mock_search_tasks(tasks):
    respx.post(TASKS_SEARCH_URL).mock(
        return_value=Response(200, json={"results": tasks})
    )


def _mock_task_associations(task_id, company_ids):
    url = f"{TASK_ASSOCIATIONS_URL}/{task_id}/associations/companies"
    respx.get(url).mock(
        return_value=Response(
            200,
            json={"results": [{"toObjectId": cid} for cid in company_ids]},
        )
    )


def _mock_get_company(company_id, country="Paraguay", agente=""):
    respx.get(f"{COMPANY_URL}/{company_id}").mock(
        return_value=Response(
            200,
            json={
                "id": company_id,
                "properties": {
                    "name": "Hotel Test",
                    "country": country,
                    "agente": agente,
                },
            },
        )
    )


def _mock_update_company(company_id):
    respx.patch(f"{COMPANY_URL}/{company_id}").mock(
        return_value=Response(200, json={"id": company_id})
    )


def _mock_update_task(task_id):
    respx.patch(f"{TASKS_URL}/{task_id}").mock(
        return_value=Response(200, json={"id": task_id})
    )


def _mock_create_note():
    respx.post(NOTES_URL).mock(
        return_value=Response(200, json={"id": "note-1"})
    )


# Use a fixed time that is a business day + business hour in Paraguay
# Tuesday 2026-02-17, 14:00 UTC = 11:00 PYT (within 9-17)
BUSINESS_DATETIME = "2026-02-17T14:00:00+00:00"


@respx.mock
@pytest.mark.asyncio
@patch("app.services.hacer_tareas.is_business_hour", return_value=True)
@patch("app.services.hacer_tareas.is_business_day", return_value=True)
async def test_activated_task(mock_day, mock_hour):
    """Eligible task with free company → activated."""
    _mock_search_tasks([
        _task("t1", "Agente:calificar_lead | Hotel Sol"),
    ])
    _mock_task_associations("t1", ["c1"])
    _mock_get_company("c1", country="Paraguay", agente="")
    _mock_update_company("c1")
    _mock_update_task("t1")
    _mock_create_note()

    async with httpx.AsyncClient() as client:
        service = HacerTareasService(HubSpotService(client, "test-token"))
        result = await service.run()

    assert result.total_found == 1
    assert result.activated == 1
    assert result.results[0].status == "activated"
    assert result.results[0].agente_value == "calificar_lead"
    assert result.results[0].company_id == "c1"


@respx.mock
@pytest.mark.asyncio
async def test_non_agent_tasks_filtered_out():
    """Tasks without 'Agente:' prefix are not processed."""
    _mock_search_tasks([
        _task("t1", "Tarea manual"),
        _task("t2", "Follow up call"),
    ])

    async with httpx.AsyncClient() as client:
        service = HacerTareasService(HubSpotService(client, "test-token"))
        result = await service.run()

    assert result.total_found == 0
    assert result.results == []


@respx.mock
@pytest.mark.asyncio
@patch("app.services.hacer_tareas.is_business_hour", return_value=True)
@patch("app.services.hacer_tareas.is_business_day", return_value=True)
async def test_no_company_skipped(mock_day, mock_hour):
    """Task with no company association → skipped."""
    _mock_search_tasks([
        _task("t1", "Agente:calificar_lead | Hotel Sol"),
    ])
    _mock_task_associations("t1", [])

    async with httpx.AsyncClient() as client:
        service = HacerTareasService(HubSpotService(client, "test-token"))
        result = await service.run()

    assert result.skipped == 1
    assert result.results[0].status == "skipped"
    assert result.results[0].message == "no_company"


@respx.mock
@pytest.mark.asyncio
@patch("app.services.hacer_tareas.is_business_hour", return_value=True)
@patch("app.services.hacer_tareas.is_business_day", return_value=True)
async def test_company_busy_skipped(mock_day, mock_hour):
    """Company with active agente → skipped."""
    _mock_search_tasks([
        _task("t1", "Agente:calificar_lead | Hotel Sol"),
    ])
    _mock_task_associations("t1", ["c1"])
    _mock_get_company("c1", country="Paraguay", agente="datos")

    async with httpx.AsyncClient() as client:
        service = HacerTareasService(HubSpotService(client, "test-token"))
        result = await service.run()

    assert result.skipped == 1
    assert result.results[0].status == "skipped"
    assert result.results[0].message == "company_busy"


@respx.mock
@pytest.mark.asyncio
@patch("app.services.hacer_tareas.is_business_hour", return_value=False)
async def test_outside_hours_skipped(mock_hour):
    """Outside business hours → skipped."""
    _mock_search_tasks([
        _task("t1", "Agente:calificar_lead | Hotel Sol"),
    ])
    _mock_task_associations("t1", ["c1"])
    _mock_get_company("c1", country="Paraguay", agente="")

    async with httpx.AsyncClient() as client:
        service = HacerTareasService(HubSpotService(client, "test-token"))
        result = await service.run()

    assert result.skipped == 1
    assert result.results[0].status == "skipped"
    assert result.results[0].message == "outside_hours"


@respx.mock
@pytest.mark.asyncio
@patch("app.services.hacer_tareas.is_business_hour", return_value=True)
@patch("app.services.hacer_tareas.is_business_day", return_value=False)
@patch("app.services.hacer_tareas.compute_task_due_date", return_value="2026-02-18T14:00:00+00:00")
async def test_holiday_rescheduled(mock_due, mock_day, mock_hour):
    """Holiday/weekend → rescheduled."""
    _mock_search_tasks([
        _task("t1", "Agente:calificar_lead | Hotel Sol"),
    ])
    _mock_task_associations("t1", ["c1"])
    _mock_get_company("c1", country="Paraguay", agente="")
    _mock_update_task("t1")

    async with httpx.AsyncClient() as client:
        service = HacerTareasService(HubSpotService(client, "test-token"))
        result = await service.run()

    assert result.rescheduled == 1
    assert result.results[0].status == "rescheduled"
    assert "2026-02-18" in result.results[0].message


@respx.mock
@pytest.mark.asyncio
@patch("app.services.hacer_tareas.is_business_hour", return_value=True)
@patch("app.services.hacer_tareas.is_business_day", return_value=True)
async def test_multiple_tasks(mock_day, mock_hour):
    """Multiple tasks processed in one run."""
    _mock_search_tasks([
        _task("t1", "Agente:calificar_lead | Hotel A"),
        _task("t2", "Agente:datos | Hotel B"),
        _task("t3", "Tarea manual"),  # filtered out
    ])
    _mock_task_associations("t1", ["c1"])
    _mock_task_associations("t2", ["c2"])
    _mock_get_company("c1", country="Paraguay", agente="")
    _mock_get_company("c2", country="Chile", agente="")
    _mock_update_company("c1")
    _mock_update_company("c2")
    _mock_update_task("t1")
    _mock_update_task("t2")
    _mock_create_note()

    async with httpx.AsyncClient() as client:
        service = HacerTareasService(HubSpotService(client, "test-token"))
        result = await service.run()

    assert result.total_found == 2
    assert result.activated == 2


@respx.mock
@pytest.mark.asyncio
@patch("app.services.hacer_tareas.is_business_hour", return_value=True)
@patch("app.services.hacer_tareas.is_business_day", return_value=True)
async def test_error_in_one_task_does_not_block_others(mock_day, mock_hour):
    """Error in one task doesn't prevent processing others."""
    _mock_search_tasks([
        _task("t1", "Agente:calificar_lead | Hotel A"),
        _task("t2", "Agente:calificar_lead | Hotel B"),
    ])
    # t1: association call fails
    respx.get(f"{TASK_ASSOCIATIONS_URL}/t1/associations/companies").mock(
        return_value=Response(500, text="server error")
    )
    # t2: works fine
    _mock_task_associations("t2", ["c2"])
    _mock_get_company("c2", country="Paraguay", agente="")
    _mock_update_company("c2")
    _mock_update_task("t2")
    _mock_create_note()

    async with httpx.AsyncClient() as client:
        service = HacerTareasService(HubSpotService(client, "test-token"))
        result = await service.run()

    assert result.errors == 1
    assert result.activated == 1
    assert result.results[0].status == "error"
    assert result.results[1].status == "activated"


@respx.mock
@pytest.mark.asyncio
async def test_empty_tasks():
    """No tasks found → empty response."""
    _mock_search_tasks([])

    async with httpx.AsyncClient() as client:
        service = HacerTareasService(HubSpotService(client, "test-token"))
        result = await service.run()

    assert result.total_found == 0
    assert result.activated == 0
    assert result.results == []


@respx.mock
@pytest.mark.asyncio
@patch("app.services.hacer_tareas.is_business_hour", return_value=True)
@patch("app.services.hacer_tareas.is_business_day", return_value=True)
async def test_note_failure_does_not_block(mock_day, mock_hour):
    """Note creation failure doesn't prevent activation."""
    _mock_search_tasks([
        _task("t1", "Agente:calificar_lead | Hotel Sol"),
    ])
    _mock_task_associations("t1", ["c1"])
    _mock_get_company("c1", country="Paraguay", agente="")
    _mock_update_company("c1")
    _mock_update_task("t1")
    # Note creation fails
    respx.post(NOTES_URL).mock(
        return_value=Response(500, text="server error")
    )

    async with httpx.AsyncClient() as client:
        service = HacerTareasService(HubSpotService(client, "test-token"))
        result = await service.run()

    assert result.activated == 1
    assert result.results[0].status == "activated"
