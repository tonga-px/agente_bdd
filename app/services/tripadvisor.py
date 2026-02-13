import logging

import httpx

from app.exceptions.custom import RateLimitError, TripAdvisorError
from app.schemas.tripadvisor import (
    TripAdvisorLocation,
    TripAdvisorSearchResponse,
)

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.content.tripadvisor.com/api/v1/location/search"
DETAILS_URL = "https://api.content.tripadvisor.com/api/v1/location/{location_id}/details"


class TripAdvisorService:
    def __init__(self, client: httpx.AsyncClient, api_key: str, referer: str = "https://web-production-705c.up.railway.app"):
        self._client = client
        self._api_key = api_key
        self._headers = {"Referer": referer}

    async def search(self, query: str, lat_long: str | None = None) -> str | None:
        """Search for a location and return its location_id, or None."""
        params = {
            "key": self._api_key,
            "searchQuery": query,
            "category": "hotels",
            "language": "es",
        }
        if lat_long:
            params["latLong"] = lat_long

        resp = await self._client.get(SEARCH_URL, params=params, headers=self._headers)

        if resp.status_code == 429:
            raise RateLimitError("TripAdvisor")
        if resp.status_code >= 400:
            raise TripAdvisorError(resp.text, status_code=resp.status_code)

        data = TripAdvisorSearchResponse(**resp.json())
        if not data.data:
            logger.info("No TripAdvisor results for: %s", query)
            return None

        return data.data[0].location_id

    async def get_details(self, location_id: str) -> TripAdvisorLocation | None:
        """Get location details by location_id."""
        url = DETAILS_URL.format(location_id=location_id)
        params = {
            "key": self._api_key,
            "language": "es",
        }

        resp = await self._client.get(url, params=params, headers=self._headers)

        if resp.status_code == 429:
            raise RateLimitError("TripAdvisor")
        if resp.status_code >= 400:
            raise TripAdvisorError(resp.text, status_code=resp.status_code)

        return TripAdvisorLocation(**resp.json())

    async def search_and_get_details(self, query: str, lat_long: str | None = None) -> TripAdvisorLocation | None:
        """Search by query and return full details, or None."""
        location_id = await self.search(query, lat_long=lat_long)
        if location_id is None:
            return None
        return await self.get_details(location_id)
