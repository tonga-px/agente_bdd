import json
import logging
import re

import httpx

from app.schemas.booking import BookingData

logger = logging.getLogger(__name__)

API_URL = "https://api.perplexity.ai/chat/completions"
MODEL = "sonar"

_SYSTEM_PROMPT = (
    "You are a hotel data extraction assistant. "
    "Return ONLY valid JSON, no markdown fences, no explanation."
)

_USER_PROMPT_TEMPLATE = (
    'Find the Booking.com listing for the hotel "{hotel_name}"'
    "{location}. "
    "Return a JSON object with exactly these fields: "
    '"url" (the full Booking.com URL or null), '
    '"rating" (number out of 10 or null), '
    '"review_count" (integer or null), '
    '"hotel_name" (name as listed on Booking.com or null). '
    "If you cannot find a Booking.com listing, return all nulls."
)

# Match a JSON object in the response (in case there's surrounding text)
_JSON_RE = re.compile(r"\{[^{}]*\}")


class PerplexityService:
    def __init__(self, client: httpx.AsyncClient, api_key: str):
        self._client = client
        self._api_key = api_key

    async def search_booking_data(
        self,
        hotel_name: str,
        city: str | None = None,
        country: str | None = None,
    ) -> BookingData:
        """Ask Perplexity for Booking.com data about a hotel."""
        location_parts = [p for p in (city, country) if p]
        location = f" in {', '.join(location_parts)}" if location_parts else ""

        prompt = _USER_PROMPT_TEMPLATE.format(
            hotel_name=hotel_name, location=location
        )

        try:
            resp = await self._client.post(
                API_URL,
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                },
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
            resp.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException):
            logger.exception("Perplexity API call failed for %s", hotel_name)
            return BookingData()

        return self._parse_response(resp.json(), hotel_name)

    def _parse_response(self, data: dict, hotel_name: str) -> BookingData:
        """Extract BookingData from Perplexity API response."""
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            logger.warning("Unexpected Perplexity response structure for %s", hotel_name)
            return BookingData()

        # Try to parse the content as JSON directly
        parsed = self._try_parse_json(content)
        if not parsed:
            logger.warning("Could not parse JSON from Perplexity response for %s", hotel_name)
            return BookingData()

        result = BookingData()

        url = parsed.get("url")
        if url and isinstance(url, str) and "booking.com" in url.lower():
            result.url = url

        rating = parsed.get("rating")
        if rating is not None:
            try:
                result.rating = float(rating)
            except (ValueError, TypeError):
                pass

        review_count = parsed.get("review_count")
        if review_count is not None:
            try:
                result.review_count = int(review_count)
            except (ValueError, TypeError):
                pass

        bk_name = parsed.get("hotel_name")
        if bk_name and isinstance(bk_name, str):
            result.hotel_name = bk_name

        if result.url or result.rating is not None:
            logger.info(
                "Perplexity found Booking data for %s: rating=%s, reviews=%s",
                hotel_name, result.rating, result.review_count,
            )
        else:
            logger.info("Perplexity found no Booking data for %s", hotel_name)

        return result

    @staticmethod
    def _try_parse_json(text: str) -> dict | None:
        """Try to extract a JSON object from text."""
        # First try direct parse
        try:
            obj = json.loads(text.strip())
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: find JSON object in the text
        match = _JSON_RE.search(text)
        if match:
            try:
                return json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError):
                pass

        return None
