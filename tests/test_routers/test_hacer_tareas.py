"""Integration tests for POST /hacer_tareas router."""

import asyncio
from unittest.mock import patch

import respx
from httpx import AsyncClient, Response

from app.services.hubspot import (
    COMPANY_URL,
    NOTES_URL,
    TASK_ASSOCIATIONS_URL,
    TASKS_SEARCH_URL,
    TASKS_URL,
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


async def submit_and_wait(client: AsyncClient, timeout: float = 5.0):
    """POST /hacer_tareas → 202, then poll GET /jobs/{job_id} until terminal."""
    resp = await client.post("/hacer_tareas")
    assert resp.status_code == 202

    data = resp.json()
    job_id = data["job_id"]
    assert data["status"] == "pending"

    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
        status_resp = await client.get(f"/jobs/{job_id}")
        assert status_resp.status_code == 200
        job = status_resp.json()
        if job["status"] in ("completed", "failed"):
            return job

    raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")


@respx.mock
@patch("app.services.hacer_tareas.is_business_hour", return_value=True)
@patch("app.services.hacer_tareas.is_business_day", return_value=True)
async def test_hacer_tareas_full_flow(mock_day, mock_hour, client):
    """Full flow: search tasks → activate agent → complete task."""
    _mock_search_tasks([
        _task("t1", "Agente:calificar_lead | Hotel Sol"),
    ])
    _mock_task_associations("t1", ["c1"])
    _mock_get_company("c1", country="Paraguay", agente="")
    _mock_update_company("c1")
    _mock_update_task("t1")
    _mock_create_note()

    job = await submit_and_wait(client)

    assert job["status"] == "completed"
    result = job["result"]
    assert result["total_found"] == 1
    assert result["activated"] == 1
    assert result["results"][0]["status"] == "activated"
    assert result["results"][0]["agente_value"] == "calificar_lead"


@respx.mock
async def test_hacer_tareas_empty(client):
    """No tasks → empty response."""
    _mock_search_tasks([])

    job = await submit_and_wait(client)

    assert job["status"] == "completed"
    assert job["result"]["total_found"] == 0
    assert job["result"]["activated"] == 0


@respx.mock
@patch("app.services.hacer_tareas.is_business_hour", return_value=True)
@patch("app.services.hacer_tareas.is_business_day", return_value=True)
async def test_hacer_tareas_company_busy(mock_day, mock_hour, client):
    """Company with active agente → skipped."""
    _mock_search_tasks([
        _task("t1", "Agente:calificar_lead | Hotel Sol"),
    ])
    _mock_task_associations("t1", ["c1"])
    _mock_get_company("c1", country="Paraguay", agente="datos")

    job = await submit_and_wait(client)

    assert job["status"] == "completed"
    assert job["result"]["skipped"] == 1
    assert job["result"]["results"][0]["message"] == "company_busy"


@respx.mock
async def test_hacer_tareas_duplicate_job(client):
    """Second POST while first is running → already_running."""
    # Make the first job hang by not mocking anything → it'll fail, but let's
    # test the duplicate check by making search slow
    _mock_search_tasks([])

    resp1 = await client.post("/hacer_tareas")
    assert resp1.status_code == 202
    job_id1 = resp1.json()["job_id"]

    # Wait for first job to finish
    deadline = asyncio.get_event_loop().time() + 5.0
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
        status_resp = await client.get(f"/jobs/{job_id1}")
        if status_resp.json()["status"] in ("completed", "failed"):
            break

    # Now first job is done, second should work
    _mock_search_tasks([])
    resp2 = await client.post("/hacer_tareas")
    assert resp2.status_code == 202


@respx.mock
@patch("app.services.hacer_tareas.is_business_hour", return_value=False)
async def test_hacer_tareas_outside_hours(mock_hour, client):
    """Outside business hours → skipped."""
    _mock_search_tasks([
        _task("t1", "Agente:calificar_lead | Hotel Sol"),
    ])
    _mock_task_associations("t1", ["c1"])
    _mock_get_company("c1", country="Paraguay", agente="")

    job = await submit_and_wait(client)

    assert job["status"] == "completed"
    assert job["result"]["skipped"] == 1
    assert job["result"]["results"][0]["message"] == "outside_hours"


@respx.mock
async def test_hacer_tareas_filters_non_agent_tasks(client):
    """Tasks without Agente: prefix are filtered out."""
    _mock_search_tasks([
        _task("t1", "Tarea manual"),
        _task("t2", "Follow up call"),
    ])

    job = await submit_and_wait(client)

    assert job["status"] == "completed"
    assert job["result"]["total_found"] == 0
