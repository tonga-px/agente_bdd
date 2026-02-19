import logging
import re

from tavily import AsyncTavilyClient

from app.schemas.booking import BookingData
from app.schemas.tavily import ReputationData
from app.schemas.website import WebScrapedData

logger = logging.getLogger(__name__)

# Reuse regex patterns from website_scraper for consistency
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s\-().]{5,}\d)")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_WA_RE = re.compile(r"(?:https?://)?(?:wa\.me/|api\.whatsapp\.com/send\?phone=)(\d+)")

_BLOCKED_EMAIL_DOMAINS = frozenset({
    "google.com", "facebook.com", "twitter.com", "instagram.com",
    "youtube.com", "linkedin.com", "sentry.io", "example.com",
    "wixpress.com", "w3.org",
})

_CONTACT_PATHS = ("/contacto", "/contact")

_ROOM_RE = re.compile(r"(\d+)\s*(?:habitacion|room|cuarto|suite|chambre|quarto)", re.IGNORECASE)

_INSTAGRAM_URL_RE = re.compile(r"https?://(?:www\.)?instagram\.com/([a-zA-Z0-9_.]+)")
_NON_PROFILE_PATHS = frozenset({"p", "reel", "stories", "explore", "accounts", "api"})

# Rating patterns for reputation parsing
# Rating: X.X/5 (or /10 for Booking) near platform name
_GOOGLE_RATING_RE = re.compile(r"google.{0,80}?(\d[.,]\d)\s*/?\s*5", re.IGNORECASE)
_TA_RATING_RE = re.compile(r"tripadvisor.{0,80}?(\d[.,]\d)\s*/?\s*5", re.IGNORECASE)
_BOOKING_RATING_RE = re.compile(r"booking.{0,80}?(\d[.,]\d)\s*/?\s*10", re.IGNORECASE)
# Review count: use greedy .* before (\d...) reviews to skip past rating numbers
_GOOGLE_REVIEWS_RE = re.compile(r"google.{0,120}?(\d[\d,. ]*\d)\s*(?:review|rese)", re.IGNORECASE)
_TA_REVIEWS_RE = re.compile(r"tripadvisor.{0,120}?(\d[\d,. ]*\d)\s*(?:review|rese)", re.IGNORECASE)
_BOOKING_REVIEWS_RE = re.compile(r"booking.{0,120}?(\d[\d,. ]*\d)\s*(?:review|rese)", re.IGNORECASE)


def _normalize_phone(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    return f"+{digits}" if digits else ""


def _is_valid_phone(phone: str) -> bool:
    digits = "".join(c for c in phone if c.isdigit())
    return len(digits) >= 7


def _is_blocked_email(email: str) -> bool:
    lower = email.lower()
    _, _, domain = lower.partition("@")
    return domain in _BLOCKED_EMAIL_DOMAINS


def _extract_instagram_url(text: str) -> str | None:
    """Find first valid Instagram profile URL in text."""
    for m in _INSTAGRAM_URL_RE.finditer(text):
        username = m.group(1).lower().rstrip("/")
        if username not in _NON_PROFILE_PATHS:
            return m.group(0)
    return None


def _extract_phones(text: str) -> list[str]:
    seen: set[str] = set()
    phones: list[str] = []
    for match in _PHONE_RE.findall(text):
        if _is_valid_phone(match):
            normalized = _normalize_phone(match)
            digits = "".join(c for c in normalized if c.isdigit())
            if digits not in seen:
                seen.add(digits)
                phones.append(normalized)
    return phones


def _extract_emails(text: str) -> list[str]:
    seen: set[str] = set()
    emails: list[str] = []
    for match in _EMAIL_RE.findall(text):
        email = match.lower()
        if email not in seen and not _is_blocked_email(email):
            seen.add(email)
            emails.append(email)
    return emails


def _extract_whatsapp(text: str) -> str | None:
    m = _WA_RE.search(text)
    if m:
        digits = m.group(1)
        if len(digits) >= 7:
            return f"+{digits}"
    return None


def _parse_float(s: str) -> float | None:
    try:
        return float(s.replace(",", "."))
    except (ValueError, TypeError):
        return None


def _parse_int(s: str) -> int | None:
    try:
        return int(s.replace(",", "").replace(".", ""))
    except (ValueError, TypeError):
        return None


class TavilyService:
    def __init__(self, api_key: str):
        self._client = AsyncTavilyClient(api_key=api_key)

    async def extract_website(self, url: str) -> WebScrapedData:
        """Extract contact data from a hotel website using Tavily Extract API."""
        try:
            return await self._do_extract(url)
        except Exception:
            logger.exception("Tavily extract failed for %s", url)
            return WebScrapedData(source_url=url)

    async def _do_extract(self, url: str) -> WebScrapedData:
        result = await self._client.extract(urls=[url])
        raw_content = self._get_extract_content(result)

        phones = _extract_phones(raw_content) if raw_content else []
        emails = _extract_emails(raw_content) if raw_content else []
        whatsapp = _extract_whatsapp(raw_content) if raw_content else None
        instagram_url = _extract_instagram_url(raw_content) if raw_content else None

        # If no emails found, try contact pages
        if not emails:
            for path in _CONTACT_PATHS:
                contact_url = url.rstrip("/") + path
                try:
                    contact_result = await self._client.extract(urls=[contact_url])
                    contact_content = self._get_extract_content(contact_result)
                    if contact_content:
                        emails = _extract_emails(contact_content)
                        if not phones:
                            phones = _extract_phones(contact_content)
                        if not whatsapp:
                            whatsapp = _extract_whatsapp(contact_content)
                    if emails:
                        break
                except Exception:
                    logger.debug("Tavily extract failed for contact page %s", contact_url)
                    continue

        return WebScrapedData(
            phones=phones,
            whatsapp=whatsapp,
            emails=emails,
            instagram_url=instagram_url,
            source_url=url,
        )

    @staticmethod
    def _get_extract_content(result: dict) -> str:
        """Get text content from Tavily extract response."""
        results = result.get("results", [])
        if results:
            return results[0].get("raw_content", "") or ""
        return ""

    async def search_booking_data(
        self,
        hotel_name: str,
        city: str | None = None,
        country: str | None = None,
    ) -> BookingData:
        """Search Booking.com data using Tavily Search API."""
        try:
            return await self._do_search_booking(hotel_name, city, country)
        except Exception:
            logger.exception("Tavily booking search failed for %s", hotel_name)
            return BookingData()

    async def _do_search_booking(
        self, hotel_name: str, city: str | None, country: str | None,
    ) -> BookingData:
        location_parts = [p for p in (city, country) if p]
        location = " ".join(location_parts)
        query = f"{hotel_name} {location} booking.com".strip()

        result = await self._client.search(
            query=query,
            include_domains=["booking.com"],
            max_results=3,
        )

        booking = BookingData()
        results = result.get("results", [])
        if not results:
            logger.info("Tavily found no Booking results for %s", hotel_name)
            return booking

        # Use the first result's URL
        first = results[0]
        url = first.get("url", "")
        if url and "booking.com" in url.lower():
            booking.url = url

        # Extract data from all result contents
        all_content = " ".join(r.get("content", "") for r in results)

        # Try to extract rating (X.X/10 or X.X pattern)
        rating_match = re.search(r"(\d[.,]\d)\s*/\s*10", all_content)
        if not rating_match:
            rating_match = re.search(
                r"(?:rating|puntuaci|calificaci|score)[^\d]*(\d[.,]\d)",
                all_content, re.IGNORECASE,
            )
        if rating_match:
            booking.rating = _parse_float(rating_match.group(1))

        # Try to extract review count
        review_match = re.search(
            r"(\d[\d,. ]+)\s*(?:review|rese|opinion|comentario)",
            all_content, re.IGNORECASE,
        )
        if review_match:
            booking.review_count = _parse_int(review_match.group(1).strip())

        # Hotel name from first result title
        title = first.get("title", "")
        if title:
            booking.hotel_name = title.split("|")[0].split("-")[0].strip()

        if booking.url or booking.rating is not None:
            logger.info(
                "Tavily found Booking data for %s: rating=%s, reviews=%s",
                hotel_name, booking.rating, booking.review_count,
            )
        else:
            logger.info("Tavily found no useful Booking data for %s", hotel_name)

        return booking

    async def search_room_count(
        self,
        hotel_name: str,
        city: str | None = None,
        country: str | None = None,
    ) -> str | None:
        """Search for hotel room count using Tavily Search API."""
        try:
            return await self._do_search_rooms(hotel_name, city, country)
        except Exception:
            logger.exception("Tavily room count search failed for %s", hotel_name)
            return None

    async def _do_search_rooms(
        self, hotel_name: str, city: str | None, country: str | None,
    ) -> str | None:
        location_parts = [p for p in (city, country) if p]
        location = " ".join(location_parts)
        query = f'"{hotel_name}" {location} habitaciones rooms cantidad'.strip()

        result = await self._client.search(
            query=query,
            max_results=5,
            include_answer=True,
        )

        # First try Tavily's LLM answer
        answer = result.get("answer", "")
        if answer:
            m = _ROOM_RE.search(answer)
            if m:
                rooms = m.group(1)
                logger.info("Tavily answer found %s rooms for %s", rooms, hotel_name)
                return rooms

        # Fallback: search in result contents
        for r in result.get("results", []):
            content = r.get("content", "")
            m = _ROOM_RE.search(content)
            if m:
                rooms = m.group(1)
                logger.info("Tavily content found %s rooms for %s", rooms, hotel_name)
                return rooms

        logger.info("Tavily found no room count for %s", hotel_name)
        return None

    async def search_reputation(
        self,
        hotel_name: str,
        city: str | None = None,
        country: str | None = None,
    ) -> ReputationData | None:
        """Search for multi-platform reputation data using Tavily Search API."""
        try:
            return await self._do_search_reputation(hotel_name, city, country)
        except Exception:
            logger.exception("Tavily reputation search failed for %s", hotel_name)
            return None

    async def _do_search_reputation(
        self, hotel_name: str, city: str | None, country: str | None,
    ) -> ReputationData | None:
        location_parts = [p for p in (city, country) if p]
        location = " ".join(location_parts)
        query = f'"{hotel_name}" {location} reviews rating opiniones'.strip()

        result = await self._client.search(
            query=query,
            max_results=5,
            include_answer=True,
        )

        all_content = " ".join(r.get("content", "") for r in result.get("results", []))
        answer = result.get("answer", "")
        full_text = f"{answer} {all_content}"

        data = ReputationData()

        # Google
        m = _GOOGLE_RATING_RE.search(full_text)
        if m:
            data.google_rating = _parse_float(m.group(1))
        m = _GOOGLE_REVIEWS_RE.search(full_text)
        if m:
            data.google_review_count = _parse_int(m.group(1))

        # TripAdvisor
        m = _TA_RATING_RE.search(full_text)
        if m:
            data.tripadvisor_rating = _parse_float(m.group(1))
        m = _TA_REVIEWS_RE.search(full_text)
        if m:
            data.tripadvisor_review_count = _parse_int(m.group(1))

        # Booking
        m = _BOOKING_RATING_RE.search(full_text)
        if m:
            data.booking_rating = _parse_float(m.group(1))
        m = _BOOKING_REVIEWS_RE.search(full_text)
        if m:
            data.booking_review_count = _parse_int(m.group(1))

        # Summary from Tavily answer
        if answer:
            data.summary = answer[:500]

        has_data = any([
            data.google_rating, data.tripadvisor_rating, data.booking_rating,
            data.summary,
        ])
        if not has_data:
            logger.info("Tavily found no reputation data for %s", hotel_name)
            return None

        logger.info("Tavily found reputation data for %s", hotel_name)
        return data

    async def extract_instagram_profile(self, profile_url: str) -> str | None:
        """Tavily Extract on Instagram profile URL. Returns raw text or None."""
        try:
            result = await self._client.extract(urls=[profile_url])
            content = self._get_extract_content(result)
            if content:
                logger.info(
                    "Tavily extracted %d chars from Instagram %s",
                    len(content), profile_url,
                )
                return content
            logger.info("Tavily extract returned no content for Instagram %s", profile_url)
            return None
        except Exception:
            logger.exception("Tavily extract failed for Instagram %s", profile_url)
            return None

    async def search_instagram_profile(
        self,
        username: str,
        hotel_name: str | None = None,
        city: str | None = None,
    ) -> str | None:
        """Tavily Search with include_domains=["instagram.com"]. Returns combined text or None."""
        try:
            context_parts = [p for p in (hotel_name, city) if p]
            context = " ".join(context_parts)
            query = f"@{username} Instagram {context}".strip()

            result = await self._client.search(
                query=query,
                include_domains=["instagram.com"],
                max_results=3,
                include_answer=True,
            )

            parts: list[str] = []
            answer = result.get("answer", "")
            if answer:
                parts.append(answer)
            for r in result.get("results", []):
                content = r.get("content", "")
                if content:
                    parts.append(content)

            if not parts:
                logger.info("Tavily search found no Instagram data for @%s", username)
                return None

            combined = "\n".join(parts)
            logger.info(
                "Tavily search found %d chars for Instagram @%s",
                len(combined), username,
            )
            return combined
        except Exception:
            logger.exception("Tavily search failed for Instagram @%s", username)
            return None

    async def search_instagram_url(self, website_url: str) -> str | None:
        """Search for the Instagram profile associated with a website URL."""
        try:
            result = await self._client.search(
                query=f"cual es la cuenta de instagram que sale en {website_url}",
                search_depth="advanced",
                max_results=3,
            )
            for r in result.get("results", []):
                url = r.get("url", "")
                m = _INSTAGRAM_URL_RE.search(url)
                if m:
                    username = m.group(1).lower().rstrip("/")
                    if username not in _NON_PROFILE_PATHS:
                        logger.info(
                            "Tavily search found Instagram @%s for %s",
                            username, website_url,
                        )
                        return m.group(0)
            logger.info("Tavily search found no Instagram profile for %s", website_url)
            return None
        except Exception:
            logger.exception("Tavily Instagram URL search failed for %s", website_url)
            return None
