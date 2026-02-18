import logging
import re
import unicodedata

import httpx

from app.exceptions.custom import RateLimitError, TripAdvisorError
from app.schemas.tripadvisor import (
    TripAdvisorLocation,
    TripAdvisorPhoto,
    TripAdvisorPhotosResponse,
    TripAdvisorSearchResponse,
)

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.content.tripadvisor.com/api/v1/location/search"
DETAILS_URL = "https://api.content.tripadvisor.com/api/v1/location/{location_id}/details"
PHOTOS_URL = "https://api.content.tripadvisor.com/api/v1/location/{location_id}/photos"

# Words too generic to count as a name match
_STOP_WORDS = frozenset({
    "hotel", "hotels", "hostel", "hostels", "cabana", "cabanas",
    "complejo", "apart", "aparthotel", "suites", "suite", "posada",
    "boutique", "resort", "lodge", "inn", "motel", "residencia",
    "de", "del", "la", "las", "los", "el", "en", "y", "the", "and",
})


def clean_name(text: str) -> str:
    """Remove bracketed/parenthesized codes like [C81] or (code)."""
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"\(.*?\)", "", text)
    return text.strip()


def _normalize(text: str) -> str:
    """Lowercase, strip accents, and remove bracketed codes."""
    nfkd = unicodedata.normalize("NFKD", clean_name(text).lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _significant_tokens(name: str) -> set[str]:
    """Extract meaningful words from a name."""
    return {w for w in _normalize(name).split() if len(w) > 2 and w not in _STOP_WORDS}


def _compound_matches(tokens: set[str], other_tokens: set[str]) -> set[str]:
    """Find matches by concatenating adjacent pairs of sorted tokens.

    Example: tokens {"life", "style"} → "lifestyle" matches "lifestyle" in other_tokens.
    """
    if len(tokens) < 2:
        return set()
    sorted_tokens = sorted(tokens)
    matches = set()
    for i in range(len(sorted_tokens) - 1):
        compound = sorted_tokens[i] + sorted_tokens[i + 1]
        if compound in other_tokens:
            matches.add(compound)
    return matches


def names_match(company_name: str, ta_name: str) -> bool:
    """Check if there is meaningful word overlap between two names.

    If the company name has 2+ significant tokens, at least 2 must match
    to avoid false positives on generic words like 'lago', 'sol', etc.

    Also considers compound matches: "life" + "style" matches "lifestyle".
    Each compound match counts as 2 because it covers two constituent tokens.
    """
    company_tokens = _significant_tokens(company_name)
    ta_tokens = _significant_tokens(ta_name)
    if not company_tokens or not ta_tokens:
        return False
    direct_overlap = company_tokens & ta_tokens
    # Bidirectional compound matching (each counts as 2 tokens)
    compound_fwd = _compound_matches(company_tokens, ta_tokens)
    compound_rev = _compound_matches(ta_tokens, company_tokens)
    score = len(direct_overlap) + 2 * len(compound_fwd | compound_rev)
    required = min(2, len(company_tokens))
    return score >= required


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
            params["radius"] = "0.02"
            params["radiusUnit"] = "km"

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

    async def get_photos(self, location_id: str, limit: int = 10) -> list[TripAdvisorPhoto]:
        """Get photos for a location. Returns up to `limit` photos."""
        url = PHOTOS_URL.format(location_id=location_id)
        params = {
            "key": self._api_key,
            "language": "es",
            "limit": str(limit),
        }

        resp = await self._client.get(url, params=params, headers=self._headers)

        if resp.status_code == 429:
            raise RateLimitError("TripAdvisor")
        if resp.status_code >= 400:
            raise TripAdvisorError(resp.text, status_code=resp.status_code)

        return TripAdvisorPhotosResponse(**resp.json()).data

    async def search_and_get_details(self, query: str, company_name: str | None = None, lat_long: str | None = None) -> TripAdvisorLocation | None:
        """Search by query and return full details, or None."""
        location_id = await self.search(query, company_name=company_name, lat_long=lat_long)
        if location_id is None:
            return None
        return await self.get_details(location_id)
