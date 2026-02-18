import asyncio
import json
import logging
import re

import httpx
from bs4 import BeautifulSoup

from app.schemas.booking import BookingData

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
_MAX_BODY = 2 * 1024 * 1024  # 2 MB
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_BOOKING_URL_RE = re.compile(r'https?://(?:www\.)?booking\.com/hotel/[a-z]{2}/[^"\'<>\s]+')


class BookingScraperService:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def search_and_scrape(
        self,
        hotel_name: str,
        city: str | None,
        country: str | None,
        website_html: str | None = None,
    ) -> BookingData:
        """Find a Booking.com URL and scrape rating/reviews. Best-effort, never raises."""
        try:
            return await self._do_search_and_scrape(
                hotel_name, city, country, website_html
            )
        except Exception:
            logger.exception("Booking scrape failed for %s", hotel_name)
            return BookingData()

    async def _do_search_and_scrape(
        self,
        hotel_name: str,
        city: str | None,
        country: str | None,
        website_html: str | None,
    ) -> BookingData:
        # Tier 1: extract from hotel website HTML
        url = None
        if website_html:
            url = self._extract_booking_url_from_html(website_html)
            if url:
                logger.info("Found Booking URL in hotel website: %s", url)

        # Tier 2: DuckDuckGo search
        if not url:
            url = await self._search_booking_url(hotel_name, city, country)
            if url:
                logger.info("Found Booking URL via DuckDuckGo: %s", url)

        if not url:
            logger.debug("No Booking URL found for %s", hotel_name)
            return BookingData()

        html = await self._scrape_booking_page(url)
        if not html:
            return BookingData(url=url)

        return self._parse_booking_html(html, url)

    def _extract_booking_url_from_html(self, html: str) -> str | None:
        """Extract booking.com/hotel/ URL from raw HTML."""
        match = _BOOKING_URL_RE.search(html)
        if match:
            url = match.group(0)
            # Clean trailing punctuation or HTML artifacts
            url = url.rstrip("\"'>;)")
            return url
        return None

    async def _search_booking_url(
        self, hotel_name: str, city: str | None, country: str | None,
    ) -> str | None:
        """Search DuckDuckGo for booking.com URL. Returns first match or None."""
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            logger.warning("duckduckgo-search not installed, skipping Booking search")
            return None

        parts = [f'site:booking.com "{hotel_name}"']
        if city:
            parts.append(city)
        if country:
            parts.append(country)
        query = " ".join(parts)

        def _do_search():
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=3))

        try:
            results = await asyncio.to_thread(_do_search)
        except Exception:
            logger.exception("DuckDuckGo search failed for: %s", query)
            return None

        for result in results:
            href = result.get("href", "")
            if "booking.com/hotel/" in href:
                return href

        return None

    async def _scrape_booking_page(self, url: str) -> str | None:
        """Fetch Booking.com page HTML. Returns None on failure."""
        try:
            resp = await self._client.get(
                url,
                follow_redirects=True,
                timeout=_TIMEOUT,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept-Language": "es,en;q=0.9",
                },
            )
            resp.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException):
            logger.debug("Failed to fetch Booking page %s", url)
            return None

        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type:
            logger.debug("Booking page not HTML: %s", content_type)
            return None

        if len(resp.content) > _MAX_BODY:
            logger.debug("Booking page too large: %d bytes", len(resp.content))
            return None

        return resp.text

    def _parse_booking_html(self, html: str, url: str) -> BookingData:
        """Parse Booking.com HTML for JSON-LD Hotel data."""
        soup = BeautifulSoup(html, "html.parser")
        data = BookingData(url=url)

        # Try JSON-LD scripts
        for script in soup.find_all("script", type="application/ld+json"):
            text = script.string
            if not text:
                continue
            try:
                ld = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                continue

            # Handle array of JSON-LD objects
            items = ld if isinstance(ld, list) else [ld]
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("@type", "")
                if item_type in ("Hotel", "LodgingBusiness"):
                    self._extract_from_jsonld(item, data)
                    return data

        # Fallback: try og:title for hotel name
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            data.hotel_name = og_title["content"]

        return data

    def _extract_from_jsonld(self, item: dict, data: BookingData) -> None:
        """Extract fields from a JSON-LD Hotel object."""
        data.hotel_name = item.get("name")

        agg = item.get("aggregateRating")
        if isinstance(agg, dict):
            try:
                data.rating = float(agg["ratingValue"])
            except (KeyError, ValueError, TypeError):
                pass
            try:
                data.review_count = int(agg["reviewCount"])
            except (KeyError, ValueError, TypeError):
                pass

        price_range = item.get("priceRange")
        if price_range and isinstance(price_range, str):
            data.price_range = price_range
