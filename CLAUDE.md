# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (dev includes pytest, respx)
pip install -e ".[dev]"

# Run locally
uvicorn app.main:app --reload --port 8000

# Tests (278 total)
pytest
pytest tests/test_services/test_enrichment.py -v          # single file
pytest tests/test_services/test_enrichment.py::test_name  # single test
pytest --cov=app                                          # with coverage
```

## Architecture

FastAPI async app deployed on Railway. Single web process, no DB, no background workers. All state is in-memory (`JobStore`, max 1000 jobs).

**Two main flows:**

1. **Enrichment** (`POST /datos` → 202, `POST /datos/sync` → 200): HubSpot search → set agente="pendiente" → Google Places text_search → TripAdvisor (optional) → website scraping (optional) → Booking.com via Perplexity (optional) → mappers → HubSpot update (with id_hotel conflict handling) + note + contacts → agente=""
2. **Prospeccion** (`POST /llamada_prospeccion` → 202): HubSpot lookup → set agente="pendiente" → build phone list → ElevenLabs outbound call → poll for result → extract data → HubSpot update + note + decision-maker contact + call recording → agente=""

Jobs are polled via `GET /jobs/{job_id}`. Duplicate jobs for the same task+company are rejected with 409.

**Key patterns:**

- **Graceful degradation**: TripAdvisor, website scraping, Perplexity/Booking, contact creation, call recording, id_hotel conflicts — all wrapped in try/except, failures logged but never block the main flow.
- **Smart merge**: only fills empty HubSpot fields. Exception: `id_hotel`, `name`, `city`, `state`, and `plaza` are always force-written from Google Places.
- **id_hotel conflict handling**: when `update_company` fails with `VALIDATION_ERROR` because `id_hotel` already belongs to another company, enrichment detects the conflict via regex (`_extract_conflicting_id`), fetches the other company, compares with `_is_same_company` (name substring + city + country). If same company → `merge_companies` + retry update. If different → drop `id_hotel` and continue. Enrichment note always created; additional merge or conflict note appended. Falls back to dropping `id_hotel` on any failure.
- **agente lifecycle**: `"datos"`/`"llamada_prospeccion"` → `"pendiente"` (immediately on start) → `""` (on completion or error). Prevents duplicate processing.
- **Duplicate job protection**: `JobStore.has_active_job(task_type, company_id)` → 409 if a pending/running job exists for the same task+company. Routers call `resolve_next_company_id()` before creating jobs so search-based and explicit requests share the same company_id in the store.
- **Cooldown**: `recently_completed_job()` rejects re-processing within 30 minutes of a completed/failed job.
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

- **Phones**: always normalized to E.164 (`+country digits`) via `_normalize_phone()`. Deduplication compares digits-only (ignores spaces/formatting).
- **SIP 486 retry**: Busy Here triggers up to 3 total attempts per number (10s delay). `_describe_error()` maps exceptions to Spanish descriptions.
- **Google Places**: enrichment always uses `text_search` (never `get_place_details` as primary). `plaza` field comes from `administrative_area_level_2` address component.
- **ElevenLabs encoding**: transcript text is double-encoded UTF-8. `_fix_encoding()` uses segment-by-segment approach because text can also contain non-Latin-1 chars (smart quotes, em dashes)
- **HubSpot associations**: notes→companies = 190, calls→companies = **182** (NOT 220)
- **HubSpot merge API**: `POST /crm/v3/objects/companies/merge` with `primaryObjectId` + `objectIdToMerge`. Used to merge duplicate companies detected via id_hotel conflict.
- **HubSpot Files API**: `folderPath` is a separate form field, NOT inside the `options` JSON
- **Booking.com via Perplexity**: replaced DuckDuckGo search + HTML scraping with a single Perplexity Sonar API call. `PerplexityService.search_booking_data()` asks for structured JSON (url, rating, review_count, hotel_name) and parses the response. Returns `BookingData` schema unchanged. `app/services/booking.py` still exists but is no longer used in the main flow.
- **Mappers** (`app/mappers/`) are pure functions — no I/O, no side effects, easy to test
- **Schemas** use modern `str | None` syntax (Python 3.11+), Pydantic BaseModel throughout

## Environment variables

Required: `HUBSPOT_ACCESS_TOKEN`, `GOOGLE_PLACES_API_KEY`
Optional: `TRIPADVISOR_API_KEY` (empty string disables), `PERPLEXITY_API_KEY` (empty string disables Booking.com search), `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_PHONE_NUMBER_ID`, `OVERWRITE_EXISTING` (default false), `LOG_LEVEL` (default INFO)

## Deployment

Railway auto-deploys from `master`. Procfile: `web: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`
