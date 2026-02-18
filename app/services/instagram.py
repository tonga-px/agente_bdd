import logging
import re
from urllib.parse import urlparse, parse_qs

import httpx

from app.schemas.instagram import InstagramData
from app.services.enrichment import _normalize_phone

logger = logging.getLogger(__name__)

_IG_APP_ID = "936619743392459"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_API_URL = "https://www.instagram.com/api/v1/users/web_profile_info/"

_USERNAME_RE = re.compile(r"instagram\.com/([^/?#]+)")
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s\-().]{5,}\d)")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_WA_ME_RE = re.compile(r"wa\.me/(\d+)")
_WA_API_RE = re.compile(r"api\.whatsapp\.com/send\?phone=(\d+)")


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


class InstagramService:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def scrape(self, url: str) -> InstagramData:
        """Scrape an Instagram profile for contact info. Never raises."""
        try:
            return await self._do_scrape(url)
        except Exception:
            logger.exception("Instagram scrape failed for %s", url)
            return InstagramData()

    async def _do_scrape(self, url: str) -> InstagramData:
        username = _extract_username(url)
        if not username:
            return InstagramData()

        resp = await self._client.get(
            _API_URL,
            params={"username": username},
            headers={
                "User-Agent": _USER_AGENT,
                "X-IG-App-ID": _IG_APP_ID,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"https://www.instagram.com/{username}/",
            },
            timeout=10.0,
        )
        if resp.status_code in (404, 429):
            logger.warning("Instagram API returned %d for %s", resp.status_code, username)
            return InstagramData(username=username)
        resp.raise_for_status()

        data = resp.json()
        user = data.get("data", {}).get("user")
        if not user:
            return InstagramData(username=username)

        full_name = user.get("full_name") or None
        biography = user.get("biography") or None
        external_url = user.get("external_url") or None
        business_email = user.get("business_email") or None
        raw_biz_phone = user.get("business_phone_number") or None
        follower_count = None
        edge = user.get("edge_followed_by")
        if edge and isinstance(edge, dict):
            follower_count = edge.get("count")

        # Bio links
        bio_links = user.get("bio_links") or []
        bio_link_urls = [bl.get("url", "") for bl in bio_links if bl.get("url")]

        # Normalize business phone
        business_phone = _normalize_phone(raw_biz_phone) if raw_biz_phone else None

        # Extract phones from biography
        bio_phones: list[str] = []
        if biography:
            seen_digits: set[str] = set()
            if business_phone:
                seen_digits.add("".join(c for c in business_phone if c.isdigit()))
            for match in _PHONE_RE.findall(biography):
                normalized = _normalize_phone(match)
                if normalized:
                    digits = "".join(c for c in normalized if c.isdigit())
                    if digits not in seen_digits:
                        seen_digits.add(digits)
                        bio_phones.append(normalized)

        # Extract emails from biography
        bio_emails: list[str] = []
        if biography:
            seen_emails: set[str] = set()
            if business_email:
                seen_emails.add(business_email.lower())
            for match in _EMAIL_RE.findall(biography):
                email = match.lower()
                if email not in seen_emails:
                    seen_emails.add(email)
                    bio_emails.append(email)

        # Resolve WhatsApp from external_url and bio_links
        whatsapp = await self._resolve_whatsapp(
            [external_url] + bio_link_urls if external_url else bio_link_urls
        )

        profile_url = f"https://www.instagram.com/{username}/"

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
                        parsed = urlparse(location)
                        phone_param = parse_qs(parsed.query).get("phone", [""])[0]
                        if phone_param:
                            digits = "".join(c for c in phone_param if c.isdigit())
                            if len(digits) >= 7:
                                return f"+{digits}"
                except Exception:
                    logger.debug("Failed to resolve wa.link URL: %s", url)

        return None
