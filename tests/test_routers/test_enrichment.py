import asyncio
import json

import respx
from httpx import AsyncClient, Response


HUBSPOT_SEARCH_URL = "https://api.hubapi.com/crm/v3/objects/companies/search"
HUBSPOT_COMPANY_URL = "https://api.hubapi.com/crm/v3/objects/companies/12345"
HUBSPOT_GET_COMPANY_URL = "https://api.hubapi.com/crm/v3/objects/companies/67890"
HUBSPOT_NOTES_URL = "https://api.hubapi.com/crm/v3/objects/notes"
GOOGLE_PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
TA_SEARCH_URL = "https://api.content.tripadvisor.com/api/v1/location/search"
TA_DETAILS_URL = "https://api.content.tripadvisor.com/api/v1/location/999/details"
TA_PHOTOS_URL = "https://api.content.tripadvisor.com/api/v1/location/{location_id}/photos"


def _mock_website(url="https://acme.cl"):
    """Mock website scraping — return empty HTML (no contacts)."""
    respx.get(url).mock(
        return_value=Response(
            200,
            html="<html><body><p>Hotel website</p></body></html>",
            headers={"content-type": "text/html"},
        )
    )


def _mock_ta_search(location_id="999", name="Acme Corp"):
    """Mock TripAdvisor search returning one result."""
    respx.get(TA_SEARCH_URL).mock(
        return_value=Response(
            200,
            json={"data": [{"location_id": location_id, "name": name}]},
        )
    )


def _mock_ta_details(location_id="999"):
    """Mock TripAdvisor details."""
    respx.get(
        f"https://api.content.tripadvisor.com/api/v1/location/{location_id}/details"
    ).mock(
        return_value=Response(
            200,
            json={
                "location_id": location_id,
                "name": "Acme Corp",
                "rating": "4.5",
                "num_reviews": "1234",
                "ranking_data": {"ranking_string": "#3 of 245 hotels in Santiago"},
                "price_level": "$$",
                "category": {"name": "Hotel"},
                "subcategory": [{"name": "Boutique"}],
                "web_url": "https://www.tripadvisor.com/Hotel_Review-999",
                "description": "A lovely hotel in Santiago.",
                "awards": [{"display_name": "Travellers' Choice 2024"}],
                "amenities": ["WiFi", "Pool", "Spa"],
                "trip_types": [{"name": "Parejas", "value": "45"}],
                "review_rating_count": {"5": 500, "4": 200, "3": 50, "2": 10, "1": 5},
                "phone": "+56 2 1234 5678",
                "email": "info@acmehotel.cl",
            },
        )
    )


def _mock_ta_photos(location_id="999"):
    """Mock TripAdvisor photos endpoint."""
    respx.get(
        TA_PHOTOS_URL.format(location_id=location_id)
    ).mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "id": "1",
                        "caption": "Pool",
                        "images": {"small": {"url": "https://img.ta/1.jpg", "width": 150, "height": 150}},
                    },
                ]
            },
        )
    )


def _mock_ta_empty():
    """Mock TripAdvisor search returning no results."""
    respx.get(TA_SEARCH_URL).mock(
        return_value=Response(200, json={"data": []})
    )


def _mock_notes():
    """Mock HubSpot notes creation."""
    respx.post(HUBSPOT_NOTES_URL).mock(
        return_value=Response(200, json={"id": "note-1"})
    )


async def submit_and_wait(client: AsyncClient, json=None, timeout: float = 5.0):
    """POST /datos → 202, then poll GET /jobs/{job_id} until terminal state."""
    resp = await client.post("/datos", json=json)
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
async def test_enrich_full_flow(client):
    # Mock HubSpot search
    respx.post(HUBSPOT_SEARCH_URL).mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {
                        "id": "12345",
                        "properties": {
                            "name": "Acme Corp",
                            "domain": None,
                            "phone": None,
                            "website": None,
                            "address": None,
                            "city": "Santiago",
                            "state": None,
                            "zip": None,
                            "country": "Chile",
                            "agente": "datos",
                        },
                    }
                ]
            },
        )
    )

    # Mock Google Places search
    respx.post(GOOGLE_PLACES_URL).mock(
        return_value=Response(
            200,
            json={
                "places": [
                    {
                        "id": "ChIJN1t_tDeuEmsRUsoyG83frY4",
                        "displayName": {"text": "Acme Corp Hotel"},
                        "formattedAddress": "Av. Providencia 123, Santiago, Chile",
                        "nationalPhoneNumber": "+56 2 1234 5678",
                        "websiteUri": "https://acme.cl",
                        "rating": 4.3,
                        "userRatingCount": 1234,
                        "googleMapsUri": "https://maps.google.com/?cid=123",
                        "priceLevel": "PRICE_LEVEL_MODERATE",
                        "businessStatus": "OPERATIONAL",
                        "addressComponents": [
                            {"longText": "123", "shortText": "123", "types": ["street_number"]},
                            {"longText": "Av. Providencia", "shortText": "Av. Providencia", "types": ["route"]},
                            {"longText": "Santiago", "shortText": "Santiago", "types": ["locality"]},
                            {"longText": "Región Metropolitana", "shortText": "RM", "types": ["administrative_area_level_1"]},
                            {"longText": "7500000", "shortText": "7500000", "types": ["postal_code"]},
                            {"longText": "Chile", "shortText": "CL", "types": ["country"]},
                        ],
                    }
                ]
            },
        )
    )

    # Mock TripAdvisor
    _mock_ta_search()
    _mock_ta_details()
    _mock_ta_photos()

    # Mock website
    _mock_website("https://acme.cl")

    # Mock HubSpot update
    respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))

    # Mock notes
    _mock_notes()

    job = await submit_and_wait(client)
    assert job["status"] == "completed"

    data = job["result"]
    assert data["total_found"] == 1
    assert data["enriched"] == 1
    assert data["no_results"] == 0
    assert data["errors"] == 0

    result = data["results"][0]
    assert result["company_id"] == "12345"
    assert result["status"] == "enriched"
    assert len(result["changes"]) > 0
    assert "Fotos TripAdvisor" in result["note"]

    # Verify id_tripadvisor, id_hotel, and name were sent to HubSpot
    patch_calls = [c for c in respx.calls if c.request.method == "PATCH"]
    assert len(patch_calls) == 1
    body = json.loads(patch_calls[0].request.content)
    assert body["properties"]["id_tripadvisor"] == "999"
    assert body["properties"]["id_hotel"] == "ChIJN1t_tDeuEmsRUsoyG83frY4"
    assert body["properties"]["name"] == "Acme Corp Hotel"


@respx.mock
async def test_enrich_no_companies(client):
    respx.post(HUBSPOT_SEARCH_URL).mock(
        return_value=Response(200, json={"results": []})
    )

    job = await submit_and_wait(client)
    assert job["status"] == "completed"

    data = job["result"]
    assert data["total_found"] == 0
    assert data["enriched"] == 0


@respx.mock
async def test_enrich_no_google_results(client):
    respx.post(HUBSPOT_SEARCH_URL).mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {
                        "id": "99999",
                        "properties": {
                            "name": "Unknown Corp",
                            "domain": None,
                            "phone": None,
                            "website": None,
                            "address": None,
                            "city": None,
                            "state": None,
                            "zip": None,
                            "country": None,
                            "agente": "datos",
                        },
                    }
                ]
            },
        )
    )

    respx.post(GOOGLE_PLACES_URL).mock(
        return_value=Response(200, json={"places": []})
    )

    # Mock TripAdvisor — also no results
    _mock_ta_empty()

    # Mock HubSpot update (clearing agente)
    respx.patch("https://api.hubapi.com/crm/v3/objects/companies/99999").mock(
        return_value=Response(200, json={})
    )

    job = await submit_and_wait(client)
    assert job["status"] == "completed"

    data = job["result"]
    assert data["total_found"] == 1
    assert data["no_results"] == 1
    assert data["results"][0]["status"] == "no_results"


@respx.mock
async def test_enrich_with_id_hotel_uses_text_search(client):
    """Even when id_hotel exists, enrichment always uses text_search."""
    respx.post(HUBSPOT_SEARCH_URL).mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {
                        "id": "12345",
                        "properties": {
                            "name": "Acme Corp",
                            "domain": None,
                            "phone": None,
                            "website": None,
                            "address": None,
                            "city": "Santiago",
                            "state": None,
                            "zip": None,
                            "country": "Chile",
                            "agente": "datos",
                            "id_hotel": "ChIJN1t_tDeuEmsRUsoyG83frY4",
                        },
                    }
                ]
            },
        )
    )

    # Mock Google Places POST text_search (NOT GET details)
    respx.post(GOOGLE_PLACES_URL).mock(
        return_value=Response(
            200,
            json={
                "places": [
                    {
                        "id": "ChIJ_NEW_PLACE_ID",
                        "displayName": {"text": "Acme Corp Hotel"},
                        "formattedAddress": "Av. Providencia 123, Santiago, Chile",
                        "nationalPhoneNumber": "+56 2 1234 5678",
                        "websiteUri": "https://acme.cl",
                        "addressComponents": [
                            {"longText": "123", "shortText": "123", "types": ["street_number"]},
                            {"longText": "Av. Providencia", "shortText": "Av. Providencia", "types": ["route"]},
                            {"longText": "Santiago", "shortText": "Santiago", "types": ["locality"]},
                            {"longText": "Región Metropolitana", "shortText": "RM", "types": ["administrative_area_level_1"]},
                            {"longText": "7500000", "shortText": "7500000", "types": ["postal_code"]},
                            {"longText": "Chile", "shortText": "CL", "types": ["country"]},
                        ],
                    }
                ]
            },
        )
    )

    # Mock TripAdvisor
    _mock_ta_search()
    _mock_ta_details()
    _mock_ta_photos()

    # Mock website
    _mock_website("https://acme.cl")

    # Mock HubSpot update
    respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))

    # Mock notes
    _mock_notes()

    job = await submit_and_wait(client)
    assert job["status"] == "completed"

    data = job["result"]
    assert data["total_found"] == 1
    assert data["enriched"] == 1
    assert data["errors"] == 0

    result = data["results"][0]
    assert result["company_id"] == "12345"
    assert result["status"] == "enriched"
    assert len(result["changes"]) > 0

    # Verify id_hotel is updated to the NEW place_id from text_search
    patch_calls = [c for c in respx.calls if c.request.method == "PATCH"]
    assert len(patch_calls) == 1
    body = json.loads(patch_calls[0].request.content)
    assert body["properties"]["id_hotel"] == "ChIJ_NEW_PLACE_ID"
    assert body["properties"]["name"] == "Acme Corp Hotel"


@respx.mock
async def test_enrich_with_company_id_in_body(client):
    # Mock HubSpot GET single company (no search needed)
    respx.get(HUBSPOT_GET_COMPANY_URL).mock(
        return_value=Response(
            200,
            json={
                "id": "67890",
                "properties": {
                    "name": "Single Corp",
                    "domain": None,
                    "phone": None,
                    "website": None,
                    "address": None,
                    "city": "Lima",
                    "state": None,
                    "zip": None,
                    "country": "Peru",
                    "agente": "",
                    "id_hotel": None,
                },
            },
        )
    )

    # Mock Google Places text search
    respx.post(GOOGLE_PLACES_URL).mock(
        return_value=Response(
            200,
            json={
                "places": [
                    {
                        "id": "ChIJ_single_corp",
                        "displayName": {"text": "Single Corp Hotel"},
                        "formattedAddress": "Av. Javier Prado 456, Lima, Peru",
                        "nationalPhoneNumber": "+51 1 987 6543",
                        "websiteUri": "https://singlecorp.pe",
                        "addressComponents": [
                            {"longText": "456", "shortText": "456", "types": ["street_number"]},
                            {"longText": "Av. Javier Prado", "shortText": "Av. Javier Prado", "types": ["route"]},
                            {"longText": "Lima", "shortText": "Lima", "types": ["locality"]},
                            {"longText": "Lima", "shortText": "Lima", "types": ["administrative_area_level_1"]},
                            {"longText": "15000", "shortText": "15000", "types": ["postal_code"]},
                            {"longText": "Peru", "shortText": "PE", "types": ["country"]},
                        ],
                    }
                ]
            },
        )
    )

    # Mock TripAdvisor
    _mock_ta_search(name="Single Corp")
    _mock_ta_details()
    _mock_ta_photos()

    # Mock website
    _mock_website("https://singlecorp.pe")

    # Mock HubSpot update
    respx.patch("https://api.hubapi.com/crm/v3/objects/companies/67890").mock(
        return_value=Response(200, json={})
    )

    # Mock notes
    _mock_notes()

    job = await submit_and_wait(client, json={"company_id": "67890"})
    assert job["status"] == "completed"

    data = job["result"]
    assert data["total_found"] == 1
    assert data["enriched"] == 1
    assert data["errors"] == 0

    result = data["results"][0]
    assert result["company_id"] == "67890"
    assert result["company_name"] == "Single Corp"
    assert result["status"] == "enriched"
    assert len(result["changes"]) > 0


@respx.mock
async def test_enrich_tripadvisor_failure_still_enriches(client):
    """TripAdvisor failure should not prevent Google Places enrichment."""
    respx.post(HUBSPOT_SEARCH_URL).mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {
                        "id": "12345",
                        "properties": {
                            "name": "Acme Corp",
                            "domain": None,
                            "phone": None,
                            "website": None,
                            "address": None,
                            "city": "Santiago",
                            "state": None,
                            "zip": None,
                            "country": "Chile",
                            "agente": "datos",
                        },
                    }
                ]
            },
        )
    )

    # Mock Google Places search — succeeds
    respx.post(GOOGLE_PLACES_URL).mock(
        return_value=Response(
            200,
            json={
                "places": [
                    {
                        "id": "ChIJN1t_tDeuEmsRUsoyG83frY4",
                        "displayName": {"text": "Acme Corp Hotel"},
                        "formattedAddress": "Av. Providencia 123, Santiago, Chile",
                        "nationalPhoneNumber": "+56 2 1234 5678",
                        "websiteUri": "https://acme.cl",
                        "addressComponents": [
                            {"longText": "123", "shortText": "123", "types": ["street_number"]},
                            {"longText": "Av. Providencia", "shortText": "Av. Providencia", "types": ["route"]},
                            {"longText": "Santiago", "shortText": "Santiago", "types": ["locality"]},
                            {"longText": "Región Metropolitana", "shortText": "RM", "types": ["administrative_area_level_1"]},
                            {"longText": "7500000", "shortText": "7500000", "types": ["postal_code"]},
                            {"longText": "Chile", "shortText": "CL", "types": ["country"]},
                        ],
                    }
                ]
            },
        )
    )

    # Mock TripAdvisor — fails with 500
    respx.get(TA_SEARCH_URL).mock(
        return_value=Response(500, text="Internal Server Error")
    )

    # Mock website
    _mock_website("https://acme.cl")

    # Mock HubSpot update
    respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))

    # Mock notes
    _mock_notes()

    job = await submit_and_wait(client)
    assert job["status"] == "completed"

    data = job["result"]
    assert data["enriched"] == 1
    assert data["errors"] == 0

    result = data["results"][0]
    assert result["status"] == "enriched"


@respx.mock
async def test_enrich_id_hotel_ignored_uses_text_search(client):
    """Even with an invalid id_hotel, enrichment uses text_search (id_hotel is ignored)."""
    respx.post(HUBSPOT_SEARCH_URL).mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {
                        "id": "12345",
                        "properties": {
                            "name": "Salguero Suites",
                            "domain": None,
                            "phone": None,
                            "website": None,
                            "address": None,
                            "city": "Buenos Aires",
                            "state": None,
                            "zip": None,
                            "country": "Argentina",
                            "agente": "datos",
                            "id_hotel": "INVALID_PLACE_ID",
                        },
                    }
                ]
            },
        )
    )

    # Mock Google text search (no GET details call at all)
    respx.post(GOOGLE_PLACES_URL).mock(
        return_value=Response(
            200,
            json={
                "places": [
                    {
                        "id": "ChIJ_salguero_new",
                        "displayName": {"text": "Salguero Suites Hotel"},
                        "formattedAddress": "Salguero 1232, Buenos Aires, Argentina",
                        "nationalPhoneNumber": "+54 11 5555 1234",
                        "websiteUri": "https://salguerosuites.com",
                        "addressComponents": [
                            {"longText": "1232", "shortText": "1232", "types": ["street_number"]},
                            {"longText": "Salguero", "shortText": "Salguero", "types": ["route"]},
                            {"longText": "Buenos Aires", "shortText": "CABA", "types": ["locality"]},
                            {"longText": "Buenos Aires", "shortText": "BA", "types": ["administrative_area_level_1"]},
                            {"longText": "C1177", "shortText": "C1177", "types": ["postal_code"]},
                            {"longText": "Argentina", "shortText": "AR", "types": ["country"]},
                        ],
                    }
                ]
            },
        )
    )

    # Mock TripAdvisor
    _mock_ta_search(name="Salguero Suites")
    _mock_ta_details()
    _mock_ta_photos()

    # Mock website
    _mock_website("https://salguerosuites.com")

    # Mock HubSpot update
    respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))

    # Mock notes
    _mock_notes()

    job = await submit_and_wait(client)
    assert job["status"] == "completed"

    data = job["result"]
    assert data["enriched"] == 1
    assert data["errors"] == 0

    result = data["results"][0]
    assert result["status"] == "enriched"
    assert len(result["changes"]) > 0

    # Verify id_hotel was overwritten with the new place_id
    patch_calls = [c for c in respx.calls if c.request.method == "PATCH"]
    body = json.loads(patch_calls[0].request.content)
    assert body["properties"]["id_hotel"] == "ChIJ_salguero_new"
    assert body["properties"]["name"] == "Salguero Suites Hotel"


async def test_get_job_nonexistent(client):
    """GET /jobs/{nonexistent} returns 404."""
    resp = await client.get("/jobs/does_not_exist")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Job not found"


@respx.mock
async def test_enrich_does_not_overwrite_existing_tripadvisor_id(client):
    """When id_tripadvisor already has a value, it should not be overwritten."""
    respx.post(HUBSPOT_SEARCH_URL).mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {
                        "id": "12345",
                        "properties": {
                            "name": "Acme Corp",
                            "domain": None,
                            "phone": None,
                            "website": None,
                            "address": None,
                            "city": "Santiago",
                            "state": None,
                            "zip": None,
                            "country": "Chile",
                            "agente": "datos",
                            "id_tripadvisor": "888",
                        },
                    }
                ]
            },
        )
    )

    # Mock Google Places search
    respx.post(GOOGLE_PLACES_URL).mock(
        return_value=Response(
            200,
            json={
                "places": [
                    {
                        "id": "ChIJN1t_tDeuEmsRUsoyG83frY4",
                        "displayName": {"text": "Acme Corp Hotel"},
                        "formattedAddress": "Av. Providencia 123, Santiago, Chile",
                        "nationalPhoneNumber": "+56 2 1234 5678",
                        "websiteUri": "https://acme.cl",
                        "addressComponents": [
                            {"longText": "Santiago", "shortText": "Santiago", "types": ["locality"]},
                            {"longText": "Chile", "shortText": "CL", "types": ["country"]},
                        ],
                    }
                ]
            },
        )
    )

    # Mock TripAdvisor — uses get_details since id_tripadvisor exists
    _mock_ta_details(location_id="888")
    _mock_ta_photos(location_id="888")

    # Mock website
    _mock_website("https://acme.cl")

    # Mock HubSpot update
    respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))

    # Mock notes
    _mock_notes()

    job = await submit_and_wait(client)
    assert job["status"] == "completed"
    assert job["result"]["enriched"] == 1

    # Verify id_tripadvisor was NOT included in the update
    patch_calls = [c for c in respx.calls if c.request.method == "PATCH"]
    assert len(patch_calls) == 1
    body = json.loads(patch_calls[0].request.content)
    assert "id_tripadvisor" not in body["properties"]


@respx.mock
async def test_enrich_tripadvisor_failure_no_id_tripadvisor_in_update(client):
    """When TripAdvisor fails, id_tripadvisor should not appear in HubSpot update."""
    respx.post(HUBSPOT_SEARCH_URL).mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {
                        "id": "12345",
                        "properties": {
                            "name": "Acme Corp",
                            "domain": None,
                            "phone": None,
                            "website": None,
                            "address": None,
                            "city": "Santiago",
                            "state": None,
                            "zip": None,
                            "country": "Chile",
                            "agente": "datos",
                        },
                    }
                ]
            },
        )
    )

    # Mock Google Places search — succeeds
    respx.post(GOOGLE_PLACES_URL).mock(
        return_value=Response(
            200,
            json={
                "places": [
                    {
                        "id": "ChIJN1t_tDeuEmsRUsoyG83frY4",
                        "displayName": {"text": "Acme Corp Hotel"},
                        "formattedAddress": "Av. Providencia 123, Santiago, Chile",
                        "nationalPhoneNumber": "+56 2 1234 5678",
                        "websiteUri": "https://acme.cl",
                        "addressComponents": [
                            {"longText": "Santiago", "shortText": "Santiago", "types": ["locality"]},
                            {"longText": "Chile", "shortText": "CL", "types": ["country"]},
                        ],
                    }
                ]
            },
        )
    )

    # Mock TripAdvisor — fails
    respx.get(TA_SEARCH_URL).mock(
        return_value=Response(500, text="Internal Server Error")
    )

    # Mock website
    _mock_website("https://acme.cl")

    # Mock HubSpot update
    respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))

    # Mock notes
    _mock_notes()

    job = await submit_and_wait(client)
    assert job["status"] == "completed"
    assert job["result"]["enriched"] == 1

    # Verify id_tripadvisor was NOT included in the update
    patch_calls = [c for c in respx.calls if c.request.method == "PATCH"]
    assert len(patch_calls) == 1
    body = json.loads(patch_calls[0].request.content)
    assert "id_tripadvisor" not in body["properties"]


@respx.mock
async def test_sync_endpoint(client):
    """POST /datos/sync still works synchronously for backward compat."""
    respx.post(HUBSPOT_SEARCH_URL).mock(
        return_value=Response(200, json={"results": []})
    )

    resp = await client.post("/datos/sync")
    assert resp.status_code == 200

    data = resp.json()
    assert data["total_found"] == 0
    assert data["enriched"] == 0
