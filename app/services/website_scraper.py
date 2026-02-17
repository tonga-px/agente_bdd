import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.schemas.website import WebScrapedData

logger = logging.getLogger(__name__)

_MAX_BODY = 2 * 1024 * 1024  # 2 MB
_TIMEOUT = 10.0
_USER_AGENT = "AgenteBDD/1.0 (+https://github.com/agente-bdd)"

# Email domains to block
_BLOCKED_EMAIL_DOMAINS = frozenset({
    "google.com", "facebook.com", "twitter.com", "instagram.com",
    "youtube.com", "linkedin.com", "sentry.io", "example.com",
    "wixpress.com", "w3.org",
})

# Email prefixes to block
_BLOCKED_EMAIL_PREFIXES = frozenset({
    "noreply", "no-reply", "admin", "webmaster", "postmaster",
    "mailer-daemon", "root", "abuse",
})

# Email preference ranking (lower = better)
_EMAIL_RANK = {
    "reserva": 0, "reservas": 0,
    "info": 1, "informacion": 1, "informaciones": 1,
    "contacto": 2, "contact": 2,
    "recepcion": 3, "reception": 3, "front": 3,
    "booking": 4, "bookings": 4,
}

# Contact page paths to try
_CONTACT_PATHS = ("/contacto", "/contact", "/contact-us", "/contactanos")

# Phone regex: sequences of 7+ digits (with optional separators)
_PHONE_RE = re.compile(
    r"(?:\+?\d[\d\s\-().]{5,}\d)",
)

# Email regex
_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)

# WhatsApp URL patterns
_WA_RE = re.compile(
    r"(?:https?://)?(?:wa\.me/|api\.whatsapp\.com/send\?phone=)(\d+)",
)


def _normalize_phone(phone: str) -> str:
    """Normalize phone to E.164: strip non-digits, prepend '+'."""
    digits = "".join(c for c in phone if c.isdigit())
    return f"+{digits}" if digits else ""


def _digits_only(phone: str) -> str:
    return "".join(c for c in phone if c.isdigit())


def _is_valid_phone(phone: str) -> bool:
    """Phone must have at least 7 digits."""
    return len(_digits_only(phone)) >= 7


def _email_rank(email: str) -> int:
    """Return preference rank for an email (lower = better)."""
    local = email.split("@")[0].lower()
    for prefix, rank in _EMAIL_RANK.items():
        if prefix in local:
            return rank
    return 99


def _is_blocked_email(email: str) -> bool:
    """Check if email should be filtered out."""
    lower = email.lower()
    local, _, domain = lower.partition("@")
    if domain in _BLOCKED_EMAIL_DOMAINS:
        return True
    for prefix in _BLOCKED_EMAIL_PREFIXES:
        if local == prefix or local.startswith(prefix + "."):
            return True
    return False


class WebsiteScraperService:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def scrape(self, url: str) -> WebScrapedData:
        """Scrape a hotel website for contact info. Best-effort, never raises."""
        try:
            return await self._do_scrape(url)
        except Exception:
            logger.exception("Website scrape failed for %s", url)
            return WebScrapedData(source_url=url)

    async def _do_scrape(self, url: str) -> WebScrapedData:
        html = await self._fetch_page(url)
        if not html:
            return WebScrapedData(source_url=url)

        soup = BeautifulSoup(html, "html.parser")
        phones = self._extract_phones(soup)
        whatsapp = self._extract_whatsapp(soup)
        emails = self._extract_emails(soup)

        # If no email on main page, try contact pages
        if not emails:
            contact_url = self._find_contact_link(soup, url)
            if contact_url:
                contact_html = await self._fetch_page(contact_url)
                if contact_html:
                    contact_soup = BeautifulSoup(contact_html, "html.parser")
                    emails = self._extract_emails(contact_soup)
                    # Also pick up additional phones/whatsapp from contact page
                    if not phones:
                        phones = self._extract_phones(contact_soup)
                    if not whatsapp:
                        whatsapp = self._extract_whatsapp(contact_soup)

        return WebScrapedData(
            phones=phones,
            whatsapp=whatsapp,
            emails=emails,
            source_url=url,
        )

    async def _fetch_page(self, url: str) -> str | None:
        """Fetch a page, return HTML string or None."""
        try:
            resp = await self._client.get(
                url,
                follow_redirects=True,
                timeout=_TIMEOUT,
                headers={"User-Agent": _USER_AGENT},
            )
            resp.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException):
            logger.debug("Failed to fetch %s", url)
            return None

        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type:
            logger.debug("Skipping non-HTML %s (content-type: %s)", url, content_type)
            return None

        if len(resp.content) > _MAX_BODY:
            logger.debug("Skipping oversized page %s (%d bytes)", url, len(resp.content))
            return None

        return resp.text

    def _extract_phones(self, soup: BeautifulSoup) -> list[str]:
        """Extract phones from tel: links first, then regex in text."""
        seen_digits: set[str] = set()
        phones: list[str] = []

        # Priority: tel: links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("tel:"):
                raw = href[4:].strip()
                if _is_valid_phone(raw):
                    normalized = _normalize_phone(raw)
                    d = _digits_only(normalized)
                    if d not in seen_digits:
                        seen_digits.add(d)
                        phones.append(normalized)

        # Fallback: regex in page text
        text = soup.get_text(separator=" ")
        for match in _PHONE_RE.findall(text):
            if _is_valid_phone(match):
                normalized = _normalize_phone(match)
                d = _digits_only(normalized)
                if d not in seen_digits:
                    seen_digits.add(d)
                    phones.append(normalized)

        return phones

    def _extract_whatsapp(self, soup: BeautifulSoup) -> str | None:
        """Extract WhatsApp number from wa.me or api.whatsapp.com links."""
        for a in soup.find_all("a", href=True):
            href = a["href"]
            m = _WA_RE.search(href)
            if m:
                digits = m.group(1)
                if len(digits) >= 7:
                    return f"+{digits}"
        return None

    def _extract_emails(self, soup: BeautifulSoup) -> list[str]:
        """Extract emails from mailto: links first, then regex. Filtered and ranked."""
        seen: set[str] = set()
        emails: list[str] = []

        # Priority: mailto: links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("mailto:"):
                email = href[7:].split("?")[0].strip().lower()
                if email and "@" in email and email not in seen and not _is_blocked_email(email):
                    seen.add(email)
                    emails.append(email)

        # Fallback: regex in page text
        text = soup.get_text(separator=" ")
        for match in _EMAIL_RE.findall(text):
            email = match.lower()
            if email not in seen and not _is_blocked_email(email):
                seen.add(email)
                emails.append(email)

        # Sort by preference
        emails.sort(key=_email_rank)
        return emails

    def _find_contact_link(self, soup: BeautifulSoup, base_url: str) -> str | None:
        """Find a contact page link on the same domain."""
        base_domain = urlparse(base_url).netloc

        # First check for contact links in the page
        for a in soup.find_all("a", href=True):
            href = a["href"]
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            if parsed.netloc != base_domain:
                continue
            path = parsed.path.rstrip("/").lower()
            if path in _CONTACT_PATHS:
                return full_url

        return None
