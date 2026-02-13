import logging
import unicodedata

import httpx

from app.exceptions.custom import RateLimitError, TripAdvisorError
from app.schemas.tripadvisor import (
    TripAdvisorLocation,
    TripAdvisorSearchResponse,
)

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.content.tripadvisor.com/api/v1/location/search"
DETAILS_URL = "https://api.content.tripadvisor.com/api/v1/location/{location_id}/details"

# Words too generic to count as a name match
_STOP_WORDS = frozenset({
    "hotel", "hotels", "hostel", "hostels", "cabana", "cabanas",
    "complejo", "apart", "aparthotel", "suites", "suite", "posada",
    "boutique", "resort", "lodge", "inn", "motel", "residencia",
    "de", "del", "la", "las", "los", "el", "en", "y", "the", "and",
})


def _normalize(text: str) -> str:
    """Lowercase and strip accents."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _significant_tokens(name: str) -> set[str]:
    """Extract meaningful words from a name."""
    return {w for w in _normalize(name).split() if len(w) > 2 and w not in _STOP_WORDS}


def names_match(company_name: str, ta_name: str) -> bool:
    """Check if there is meaningful word overlap between two names."""
    company_tokens = _significant_tokens(company_name)
    ta_tokens = _significant_tokens(ta_name)
    if not company_tokens or not ta_tokens:
        return False
    return bool(company_tokens & ta_tokens)


class TripAdvisorService:
    def __init__(self, client: httpx.AsyncClient, api_key: str, referer: str = "https://web-production-705c.up.railway.app"):
        self._client = client
        self._api_key = api_key
        self._headers = {"Referer": referer}

    async def search(self, query: str, company_name: str | None = None, lat_long: str | None = None) -> str | None:
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

        # Validate name match if company_name provided
        if company_name:
            for result in data.data:
                if result.name and names_match(company_name, result.name):
                    logger.info("TripAdvisor name match: '%s' ~ '%s'", company_name, result.name)
                    return result.location_id
            # No match found
            best = data.data[0].name
            logger.info("TripAdvisor no name match: '%s' vs '%s' (and %d others), skipping",
                        company_name, best, len(data.data) - 1)
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

    async def search_and_get_details(self, query: str, company_name: str | None = None, lat_long: str | None = None) -> TripAdvisorLocation | None:
        """Search by query and return full details, or None."""
        location_id = await self.search(query, company_name=company_name, lat_long=lat_long)
        if location_id is None:
            return None
        return await self.get_details(location_id)
