import respx
from httpx import Response


HUBSPOT_SEARCH_URL = "https://api.hubapi.com/crm/v3/objects/companies/search"
HUBSPOT_COMPANY_URL = "https://api.hubapi.com/crm/v3/objects/companies/12345"
GOOGLE_PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
GOOGLE_DETAILS_URL = "https://places.googleapis.com/v1/places/ChIJN1t_tDeuEmsRUsoyG83frY4"


@respx.mock
def test_enrich_full_flow(client):
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

    # Mock HubSpot update
    respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))

    resp = client.post("/datos")
    assert resp.status_code == 200

    data = resp.json()
    assert data["total_found"] == 1
    assert data["enriched"] == 1
    assert data["no_results"] == 0
    assert data["errors"] == 0

    result = data["results"][0]
    assert result["company_id"] == "12345"
    assert result["status"] == "enriched"
    assert len(result["changes"]) > 0


@respx.mock
def test_enrich_no_companies(client):
    respx.post(HUBSPOT_SEARCH_URL).mock(
        return_value=Response(200, json={"results": []})
    )

    resp = client.post("/datos")
    assert resp.status_code == 200

    data = resp.json()
    assert data["total_found"] == 0
    assert data["enriched"] == 0


@respx.mock
def test_enrich_no_google_results(client):
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

    # Mock HubSpot update (clearing agente)
    respx.patch("https://api.hubapi.com/crm/v3/objects/companies/99999").mock(
        return_value=Response(200, json={})
    )

    resp = client.post("/datos")
    assert resp.status_code == 200

    data = resp.json()
    assert data["total_found"] == 1
    assert data["no_results"] == 1
    assert data["results"][0]["status"] == "no_results"


@respx.mock
def test_enrich_with_id_hotel(client):
    # Mock HubSpot search — company has id_hotel
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

    # Mock Google Places GET (details, not text search)
    respx.get(GOOGLE_DETAILS_URL).mock(
        return_value=Response(
            200,
            json={
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
            },
        )
    )

    # Mock HubSpot update
    respx.patch(HUBSPOT_COMPANY_URL).mock(return_value=Response(200, json={}))

    resp = client.post("/datos")
    assert resp.status_code == 200

    data = resp.json()
    assert data["total_found"] == 1
    assert data["enriched"] == 1
    assert data["errors"] == 0

    result = data["results"][0]
    assert result["company_id"] == "12345"
    assert result["status"] == "enriched"
    assert len(result["changes"]) > 0
