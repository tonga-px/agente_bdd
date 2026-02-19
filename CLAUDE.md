# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (dev includes pytest, respx)
pip install -e ".[dev]"

# Run locally
uvicorn app.main:app --reload --port 8000

# Tests (320 total)
pytest
pytest tests/test_services/test_enrichment.py -v          # single file
pytest tests/test_services/test_enrichment.py::test_name  # single test
pytest --cov=app                                          # with coverage
```

## Architecture

FastAPI async app deployed on Railway. Single web process, no DB, no background workers. All state is in-memory (`JobStore`, max 1000 jobs).

**Three main flows:**

1. **Enrichment** (`POST /datos` → 202, `POST /datos/sync` → 200): HubSpot search → set agente="pendiente" → Google Places text_search → Instagram via Perplexity (if website is instagram.com) → TripAdvisor (optional) → **parallel optional enrichment** (website extract, Booking.com, room count, reputation — all via Tavily when available, else fallback to WebsiteScraperService/PerplexityService) → mappers → HubSpot update (with id_hotel conflict handling, auto-set cantidad_de_habitaciones + market_fit from Tavily rooms) + note + contacts → agente=""
2. **Prospeccion** (`POST /llamada_prospeccion` → 202): HubSpot lookup → set agente="pendiente" → build phone list → ElevenLabs outbound call → poll for result → extract data → HubSpot update + note + decision-maker contact + call recording → agente=""
3. **CalificarLead** (`POST /calificar_lead` → 202): HubSpot search agente="calificar_lead" → set agente="pendiente" → fetch notas, llamadas, emails, contactos in parallel → Claude analyzes context → determines cantidad_de_habitaciones + market_fit → HubSpot update → if "No es FIT": update associated leads pipeline stage + create verification tasks → create summary note → agente=""

Jobs are polled via `GET /jobs/{job_id}`. Duplicate jobs for the same task+company are rejected with 409.

**Key patterns:**

- **Graceful degradation**: TripAdvisor, website scraping, Tavily, Perplexity/Booking, contact creation, call recording, id_hotel conflicts — all wrapped in try/except, failures logged but never block the main flow.
- **Smart merge**: only fills empty HubSpot fields. Exception: `id_hotel`, `name`, `city`, `state`, and `plaza` are always force-written from Google Places.
- **id_hotel conflict handling**: when `update_company` fails with `VALIDATION_ERROR` because `id_hotel` already belongs to another company, enrichment detects the conflict via regex (`_extract_conflicting_id`), fetches the other company, compares with `_is_same_company` (name substring + city + country). If same company → `merge_companies` + retry update. If different → drop `id_hotel` and continue. Enrichment note always created; additional merge or conflict note appended. Falls back to dropping `id_hotel` on any failure.
- **agente lifecycle**: `"datos"`/`"llamada_prospeccion"`/`"calificar_lead"` → `"pendiente"` (immediately on start) → `""` (on completion or error). Prevents duplicate processing.
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
- **Instagram via Perplexity**: when hotel's website URL is `instagram.com`, `InstagramService` uses Perplexity `sonar-pro` model with `search_domain_filter: ["instagram.com"]` to extract profile data (name, bio, phones, email, WhatsApp, followers). Data goes to enrichment note + creates "/ Instagram" contact. `sonar` model refuses Instagram content; only `sonar-pro` works. Uses `PERPLEXITY_API_KEY` (same key as Booking search).
- **Tavily integration** (`TavilyService`): uses `AsyncTavilyClient` (its own HTTP client, NOT the shared httpx — same pattern as ClaudeService). 4 methods: `extract_website` (Extract API, replaces WebsiteScraperService), `search_booking_data` (Search API with `include_domains=["booking.com"]`, replaces PerplexityService), `search_room_count` (Search API + regex for room numbers), `search_reputation` (Search API for multi-platform ratings). All run in parallel via `asyncio.gather`. When `TAVILY_API_KEY` is not set, falls back to WebsiteScraperService and PerplexityService.
- **Booking.com via Tavily/Perplexity**: Tavily preferred when available (`include_domains=["booking.com"]`). Falls back to Perplexity Sonar. `PerplexityService` still exists for Instagram. `app/services/booking.py` still exists but is no longer used.
- **Room count auto-detection**: when Tavily finds room count and company's `cantidad_de_habitaciones` is empty, enrichment auto-sets `cantidad_de_habitaciones`, `habitaciones`, and `market_fit` (via shared `compute_market_fit` from `app/mappers/market_fit.py`).
- **Reputation search**: Tavily searches for Google/TripAdvisor/Booking ratings across multiple sources. Results shown in enrichment note under "Reputacion" section. Schema: `ReputationData` in `app/schemas/tavily.py`.
- **Claude via Anthropic SDK**: `ClaudeService` uses `AsyncAnthropic` (its own HTTP client, NOT the shared httpx). Model: `claude-sonnet-4-20250514`. JSON parsing uses same pattern as `PerplexityService`: strip markdown fences → direct parse → regex fallback.
- **CalificarLead market_fit values**: "No es FIT" (<5 rooms), "Hormiga" (5-13), "Conejo" (14-27), "Elefante" (28+). Ranges defined in shared `compute_market_fit()` from `app/mappers/market_fit.py` (used by both CalificarLead and enrichment auto-classification). When "No es FIT", associated leads get pipeline stage `1178022266` and a verification task is created for the lead's owner.
- **HubSpot Leads API**: `LEADS_URL = crm/v3/objects/leads`. Association company→leads via `_get_associated_ids`. `update_lead()` uses PATCH.
- **Mappers** (`app/mappers/`) are pure functions — no I/O, no side effects, easy to test
- **Schemas** use modern `str | None` syntax (Python 3.11+), Pydantic BaseModel throughout

## Environment variables

Required: `HUBSPOT_ACCESS_TOKEN`, `GOOGLE_PLACES_API_KEY`
Optional: `TRIPADVISOR_API_KEY` (empty string disables), `PERPLEXITY_API_KEY` (empty string disables Booking.com search via Perplexity; still needed for Instagram), `TAVILY_API_KEY` (empty string disables; when set, Tavily is preferred over WebsiteScraperService and PerplexityService for enrichment), `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_PHONE_NUMBER_ID`, `ANTHROPIC_API_KEY` (empty string disables CalificarLead), `OVERWRITE_EXISTING` (default false), `LOG_LEVEL` (default INFO)

## Deployment

Railway auto-deploys from `master`. Procfile: `web: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`
