import logging

import httpx

from app.exceptions.custom import GooglePlacesError, RateLimitError
from app.schemas.google_places import GooglePlace, TextSearchResponse

logger = logging.getLogger(__name__)

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places"

FIELD_MASK = (
    "places.formattedAddress,"
    "places.nationalPhoneNumber,"
    "places.internationalPhoneNumber,"
    "places.websiteUri,"
    "places.addressComponents,"
    "places.location"
)

DETAILS_FIELD_MASK = (
    "formattedAddress,"
    "nationalPhoneNumber,"
    "internationalPhoneNumber,"
    "websiteUri,"
    "addressComponents,"
    "location"
)


def build_search_query(
    name: str | None,
    city: str | None = None,
    country: str | None = None,
) -> str:
    parts = [p for p in (name, city, country) if p]
    return ", ".join(parts)


class GooglePlacesService:
    def __init__(self, client: httpx.AsyncClient, api_key: str):
        self._client = client
        self._api_key = api_key

    async def text_search(self, query: str) -> GooglePlace | None:
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        }
        payload = {"textQuery": query}

        resp = await self._client.post(SEARCH_URL, json=payload, headers=headers)

        if resp.status_code == 429:
            raise RateLimitError("Google Places")
        if resp.status_code >= 400:
            raise GooglePlacesError(resp.text, status_code=resp.status_code)

        data = TextSearchResponse(**resp.json())
        if not data.places:
            logger.info("No results for query: %s", query)
            return None

        return data.places[0]

    async def get_place_details(self, place_id: str) -> GooglePlace | None:
        headers = {
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": DETAILS_FIELD_MASK,
        }

        resp = await self._client.get(
            f"{DETAILS_URL}/{place_id}", headers=headers
        )

        if resp.status_code == 429:
            raise RateLimitError("Google Places")
        if resp.status_code >= 400:
            raise GooglePlacesError(resp.text, status_code=resp.status_code)

        return GooglePlace(**resp.json())
