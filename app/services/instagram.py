import logging
import re
from urllib.parse import urlparse, parse_qs

import httpx

from app.schemas.instagram import InstagramData
from app.services.enrichment import _normalize_phone

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_USERNAME_RE = re.compile(r"instagram\.com/([^/?#]+)")
_PHONE_RE = re.compile(r"(?:\+?\d[\d \t\-().]{5,}\d)")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_WA_ME_RE = re.compile(r"wa\.me/(\d+)")
_WA_API_RE = re.compile(r"api\.whatsapp\.com/send\?phone=(\d+)")
_URL_RE = re.compile(r"https?://[^\s<>\"']+")

# Follower count patterns: "1,500 followers", "1.5K followers", "15M seguidores"
_FOLLOWER_RE = re.compile(
    r"([\d][.\d,]*)\s*([KkMm])?\s*(?:follower|seguidor)",
    re.IGNORECASE,
)

_BLOCKED_EMAIL_DOMAINS = frozenset({
    "google.com", "facebook.com", "twitter.com", "instagram.com",
    "youtube.com", "linkedin.com", "sentry.io", "example.com",
    "wixpress.com", "w3.org",
})

# Minimum chars from Extract to consider it useful
_MIN_EXTRACT_LENGTH = 50


def is_instagram_url(url: str) -> bool:
    """Check if URL points to an Instagram profile."""
    parsed = urlparse(url)
    return parsed.netloc in ("www.instagram.com", "instagram.com")


def _extract_username(url: str) -> str | None:
    """Extract username from an Instagram URL."""
    m = _USERNAME_RE.search(url)
    if not m:
        return None
    username = m.group(1).lower().rstrip("/")
    # Skip non-profile paths
    if username in ("p", "reel", "stories", "explore", "accounts", "api"):
        return None
    return username


def _extract_phones(biography: str | None, business_phone: str | None) -> list[str]:
    """Extract and normalize phones from biography text, deduping against business_phone."""
    if not biography:
        return []
    seen_digits: set[str] = set()
    if business_phone:
        seen_digits.add("".join(c for c in business_phone if c.isdigit()))
    phones: list[str] = []
    for match in _PHONE_RE.findall(biography):
        normalized = _normalize_phone(match)
        if normalized:
            digits = "".join(c for c in normalized if c.isdigit())
            if digits not in seen_digits:
                seen_digits.add(digits)
                phones.append(normalized)
    return phones


def _extract_emails(biography: str | None, business_email: str | None) -> list[str]:
    """Extract emails from biography text, deduping against business_email."""
    if not biography:
        return []
    seen: set[str] = set()
    if business_email:
        seen.add(business_email.lower())
    emails: list[str] = []
    for match in _EMAIL_RE.findall(biography):
        email = match.lower()
        if email not in seen:
            _, _, domain = email.partition("@")
            if domain not in _BLOCKED_EMAIL_DOMAINS:
                seen.add(email)
                emails.append(email)
    return emails


def _parse_follower_count(text: str) -> int | None:
    """Parse follower count from text. Handles '1,500', '1.5K', '15M', etc."""
    m = _FOLLOWER_RE.search(text)
    if not m:
        return None
    raw_num = m.group(1).replace(",", "")
    suffix = (m.group(2) or "").upper()
    try:
        num = float(raw_num)
    except ValueError:
        return None
    if suffix == "K":
        num *= 1_000
    elif suffix == "M":
        num *= 1_000_000
    return int(num)


def _extract_external_urls(text: str) -> str | None:
    """Find the first non-Instagram, non-WhatsApp URL in text."""
    for url_match in _URL_RE.findall(text):
        lower = url_match.lower()
        if "instagram.com" in lower:
            continue
        if "wa.me/" in lower or "wa.link/" in lower or "whatsapp.com" in lower:
            continue
        return url_match
    return None


def _parse_profile_text(text: str, username: str, profile_url: str) -> InstagramData:
    """Parse raw profile text into InstagramData using regex extraction."""
    # Full name: heuristic — first non-empty line that looks like a name
    full_name: str | None = None
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for line in lines:
        # Skip lines that are too long (likely content), too short, or look like URLs/metadata
        if len(line) > 80 or len(line) < 2:
            continue
        if line.startswith("http") or "@" in line:
            continue
        if any(kw in line.lower() for kw in ("follower", "seguidor", "post", "following")):
            continue
        full_name = line
        break

    # Biography: try to find bio-like content (lines after name, before stats)
    biography: str | None = None
    bio_lines: list[str] = []
    for line in lines:
        if line.startswith("http"):
            continue
        if any(kw in line.lower() for kw in ("follower", "seguidor", "post", "following")):
            continue
        if line == full_name:
            continue
        if len(line) > 10:
            bio_lines.append(line)
        if len(bio_lines) >= 3:
            break
    if bio_lines:
        biography = " ".join(bio_lines)

    # Phones and emails from full text
    all_phones = _PHONE_RE.findall(text)
    business_phone: str | None = None
    if all_phones:
        normalized = _normalize_phone(all_phones[0])
        if normalized:
            business_phone = normalized

    all_emails = _EMAIL_RE.findall(text)
    business_email: str | None = None
    for e in all_emails:
        _, _, domain = e.lower().partition("@")
        if domain not in _BLOCKED_EMAIL_DOMAINS:
            business_email = e.lower()
            break

    # Bio phones/emails (deduped against business fields)
    bio_phones = _extract_phones(text, business_phone)
    bio_emails = _extract_emails(text, business_email)

    # Follower count
    follower_count = _parse_follower_count(text)

    # WhatsApp from text
    whatsapp: str | None = None
    wa_m = _WA_ME_RE.search(text)
    if wa_m and len(wa_m.group(1)) >= 7:
        whatsapp = f"+{wa_m.group(1)}"
    if not whatsapp:
        wa_m = _WA_API_RE.search(text)
        if wa_m and len(wa_m.group(1)) >= 7:
            whatsapp = f"+{wa_m.group(1)}"

    # External URL
    external_url = _extract_external_urls(text)

    return InstagramData(
        username=username,
        full_name=full_name,
        biography=biography,
        profile_url=profile_url,
        external_url=external_url,
        follower_count=follower_count,
        business_email=business_email,
        business_phone=business_phone,
        bio_phones=bio_phones,
        bio_emails=bio_emails,
        whatsapp=whatsapp,
    )


class InstagramService:
    def __init__(self, tavily: "TavilyService", client: httpx.AsyncClient):
        from app.services.tavily import TavilyService  # noqa: F811
        self._tavily: TavilyService = tavily
        self._client = client  # for _resolve_whatsapp (wa.link redirects)

    async def scrape(
        self,
        url: str,
        hotel_name: str | None = None,
        city: str | None = None,
    ) -> InstagramData:
        """Scrape an Instagram profile via Tavily. Never raises."""
        try:
            return await self._do_scrape(url, hotel_name, city)
        except Exception:
            logger.exception("Instagram scrape failed for %s", url)
            return InstagramData()

    async def _do_scrape(
        self,
        url: str,
        hotel_name: str | None = None,
        city: str | None = None,
    ) -> InstagramData:
        username = _extract_username(url)
        if not username:
            return InstagramData()

        profile_url = f"https://www.instagram.com/{username}/"

        # Step 1: Try Tavily Extract
        text = await self._tavily.extract_instagram_profile(profile_url)

        # Step 2: Fallback to Tavily Search if Extract failed or returned too little
        if not text or len(text) < _MIN_EXTRACT_LENGTH:
            logger.info(
                "Instagram Extract insufficient for @%s (%d chars), falling back to Search",
                username, len(text) if text else 0,
            )
            text = await self._tavily.search_instagram_profile(
                username, hotel_name, city,
            )

        # Step 3: If both failed, return minimal data
        if not text:
            logger.warning("No Instagram data found for @%s", username)
            return InstagramData(username=username, profile_url=profile_url)

        # Step 4: Parse text into InstagramData
        data = _parse_profile_text(text, username, profile_url)

        # Step 5: Resolve WhatsApp from found URLs
        urls_to_check: list[str] = []
        # Check for wa.link URLs in text that need redirect resolution
        wa_link_matches = re.findall(r"https?://wa\.link/\S+", text)
        urls_to_check.extend(wa_link_matches)
        if data.external_url:
            urls_to_check.append(data.external_url)

        if urls_to_check and not data.whatsapp:
            resolved = await self._resolve_whatsapp(urls_to_check)
            if resolved:
                data = data.model_copy(update={"whatsapp": resolved})

        logger.info(
            "Instagram @%s: name=%s, phones=%d, emails=%d, whatsapp=%s, followers=%s",
            username, data.full_name,
            len(data.bio_phones) + (1 if data.business_phone else 0),
            len(data.bio_emails) + (1 if data.business_email else 0),
            bool(data.whatsapp), data.follower_count,
        )

        return data

    async def _resolve_whatsapp(self, urls: list[str]) -> str | None:
        """Extract WhatsApp number from wa.me, api.whatsapp.com, or wa.link URLs."""
        for url in urls:
            if not url:
                continue

            # wa.me/DIGITS
            m = _WA_ME_RE.search(url)
            if m:
                digits = m.group(1)
                if len(digits) >= 7:
                    return f"+{digits}"

            # api.whatsapp.com/send?phone=DIGITS
            m = _WA_API_RE.search(url)
            if m:
                digits = m.group(1)
                if len(digits) >= 7:
                    return f"+{digits}"

            # wa.link/CODE → follow redirect
            if "wa.link/" in url:
                try:
                    resp = await self._client.get(
                        url,
                        follow_redirects=False,
                        timeout=5.0,
                        headers={"User-Agent": _USER_AGENT},
                    )
                    location = resp.headers.get("location", "")
                    if location:
                        # Try to parse phone from redirect URL
                        m = _WA_ME_RE.search(location)
                        if m and len(m.group(1)) >= 7:
                            return f"+{m.group(1)}"
                        m = _WA_API_RE.search(location)
                        if m and len(m.group(1)) >= 7:
                            return f"+{m.group(1)}"
                        # Try query param
                        parsed_url = urlparse(location)
                        phone_param = parse_qs(parsed_url.query).get("phone", [""])[0]
                        if phone_param:
                            digits = "".join(c for c in phone_param if c.isdigit())
                            if len(digits) >= 7:
                                return f"+{digits}"
                except Exception:
                    logger.debug("Failed to resolve wa.link URL: %s", url)

        return None
