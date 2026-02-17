# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (dev includes pytest, respx)
pip install -e ".[dev]"

# Run locally
uvicorn app.main:app --reload --port 8000

# Tests (173 total)
pytest
pytest tests/test_services/test_enrichment.py -v          # single file
pytest tests/test_services/test_enrichment.py::test_name  # single test
pytest --cov=app                                          # with coverage
```

## Architecture

FastAPI async app deployed on Railway. Single web process, no DB, no background workers. All state is in-memory (`JobStore`, max 1000 jobs).

**Two main flows:**

1. **Enrichment** (`POST /datos` → 202, `POST /datos/sync` → 200): HubSpot search → Google Places text_search → TripAdvisor (optional) → website scraping (optional) → mappers → HubSpot update + note + contacts
2. **Prospeccion** (`POST /llamada_prospeccion` → 202): HubSpot lookup → build phone list → ElevenLabs outbound call → poll for result → extract data → HubSpot update + note + decision-maker contact + call recording

Jobs are polled via `GET /jobs/{job_id}`.

**Key patterns:**

- **Graceful degradation**: TripAdvisor, website scraping, contact creation, call recording — all wrapped in try/except, failures logged but never block the main flow.
- **Smart merge**: only fills empty HubSpot fields. Exception: `id_hotel` (place_id) and `name` (displayName) are always force-written from Google Places.
- **Dependency injection**: services created in `lifespan()`, stored on `app.state`, accessed via `Annotated[XService, Depends()]` in `dependencies.py`.
- **Shared httpx.AsyncClient**: 30s default timeout; file upload/download uses 120s.

## Testing

- `pytest-asyncio` with `asyncio_mode = "auto"` (set in `pyproject.toml`)
- HTTP mocking via `respx` (decorator `@respx.mock` on async test functions)
- Integration tests use `httpx.AsyncClient` + `ASGITransport` (NOT `TestClient`)
- Fixture in `conftest.py` triggers lifespan manually: `async with lifespan(app)`
- Router tests use `submit_and_wait()` helper: POST → poll `GET /jobs/{id}` with `asyncio.sleep(0.05)`
- Prospeccion router tests need `timeout=10.0` because `POLL_INTERVAL=5s`

## Critical conventions

- **Phones**: always normalized to E.164 (`+country digits`) via `_normalize_phone()`
- **Google Places**: enrichment always uses `text_search` (never `get_place_details` as primary)
- **ElevenLabs encoding**: transcript text is double-encoded UTF-8. `_fix_encoding()` uses segment-by-segment approach because text can also contain non-Latin-1 chars (smart quotes, em dashes)
- **HubSpot associations**: notes→companies = 190, calls→companies = **182** (NOT 220)
- **HubSpot Files API**: `folderPath` is a separate form field, NOT inside the `options` JSON
- **Mappers** (`app/mappers/`) are pure functions — no I/O, no side effects, easy to test
- **Schemas** use modern `str | None` syntax (Python 3.11+), Pydantic BaseModel throughout

## Environment variables

Required: `HUBSPOT_ACCESS_TOKEN`, `GOOGLE_PLACES_API_KEY`
Optional: `TRIPADVISOR_API_KEY` (empty string disables), `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_PHONE_NUMBER_ID`, `OVERWRITE_EXISTING` (default false), `LOG_LEVEL` (default INFO)

## Deployment

Railway auto-deploys from `master`. Procfile: `web: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`
