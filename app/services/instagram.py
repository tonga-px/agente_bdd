import json
import logging
import re
from urllib.parse import urlparse, parse_qs

import httpx

from app.schemas.instagram import InstagramData
from app.services.enrichment import _normalize_phone

logger = logging.getLogger(__name__)

_PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
_PERPLEXITY_MODEL = "sonar"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_USERNAME_RE = re.compile(r"instagram\.com/([^/?#]+)")
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s\-().]{5,}\d)")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_WA_ME_RE = re.compile(r"wa\.me/(\d+)")
_WA_API_RE = re.compile(r"api\.whatsapp\.com/send\?phone=(\d+)")
_JSON_RE = re.compile(r"\{[^{}]*\}")

_SYSTEM_PROMPT = (
    "You are a hotel data extraction assistant. "
    "Return ONLY valid JSON, no markdown fences, no explanation."
)

_USER_PROMPT_TEMPLATE = (
    'Search for the public Instagram profile @{username} '
    '(https://www.instagram.com/{username}/){context}. '
    'What is their display name, biography text, phone numbers, '
    'email, WhatsApp link, and follower count? '
    'Return a JSON object with exactly these fields: '
    '"full_name" (profile display name or null), '
    '"biography" (bio text or null), '
    '"external_url" (link in bio or null), '
    '"business_email" (contact email or null), '
    '"business_phone" (contact phone or null), '
    '"follower_count" (number of followers as integer, or null), '
    '"whatsapp_url" (any WhatsApp link wa.me/*, wa.link/*, '
    'api.whatsapp.com/* or null). '
    'If a field is not available, use null.'
)


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
            seen.add(email)
            emails.append(email)
    return emails


def _try_parse_json(text: str) -> dict | None:
    """Try to extract a JSON object from text, stripping markdown fences."""
    text = text.strip()
    # Strip markdown code fences (```json ... ```)
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    match = _JSON_RE.search(text)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            pass
    return None


class InstagramService:
    def __init__(self, client: httpx.AsyncClient, api_key: str):
        self._client = client
        self._api_key = api_key

    async def scrape(
        self,
        url: str,
        hotel_name: str | None = None,
        city: str | None = None,
    ) -> InstagramData:
        """Scrape an Instagram profile via Perplexity. Never raises."""
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
        context_parts = [p for p in (hotel_name, city) if p]
        context = f", which belongs to {', '.join(context_parts)}" if context_parts else ""
        prompt = _USER_PROMPT_TEMPLATE.format(username=username, context=context)

        try:
            resp = await self._client.post(
                _PERPLEXITY_URL,
                json={
                    "model": _PERPLEXITY_MODEL,
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
            logger.exception("Perplexity API call failed for Instagram %s", username)
            return InstagramData(username=username, profile_url=profile_url)

        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            logger.warning("Unexpected Perplexity response for Instagram %s", username)
            return InstagramData(username=username, profile_url=profile_url)

        logger.info("Perplexity raw response for Instagram %s: %s", username, content[:500])

        parsed = _try_parse_json(content)
        if not parsed:
            logger.warning("Could not parse JSON from Perplexity for Instagram %s", username)
            return InstagramData(username=username, profile_url=profile_url)

        full_name = parsed.get("full_name") or None
        biography = parsed.get("biography") or None
        external_url = parsed.get("external_url") or None
        business_email = parsed.get("business_email") or None
        raw_biz_phone = parsed.get("business_phone") or None
        follower_count = None
        raw_fc = parsed.get("follower_count")
        if raw_fc is not None:
            try:
                follower_count = int(raw_fc)
            except (ValueError, TypeError):
                pass
        whatsapp_url = parsed.get("whatsapp_url") or None

        # Normalize business phone
        business_phone = _normalize_phone(raw_biz_phone) if raw_biz_phone else None

        # Extract phones from biography
        bio_phones = _extract_phones(biography, business_phone)

        # Extract emails from biography
        bio_emails = _extract_emails(biography, business_email)

        # Resolve WhatsApp
        urls_to_check: list[str] = []
        if whatsapp_url:
            urls_to_check.append(whatsapp_url)
        if external_url and external_url != whatsapp_url:
            urls_to_check.append(external_url)
        whatsapp = await self._resolve_whatsapp(urls_to_check)

        logger.info(
            "Instagram %s: name=%s, phones=%d, emails=%d, whatsapp=%s",
            username, full_name, len(bio_phones), len(bio_emails),
            bool(whatsapp),
        )

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
                        parsed_url = urlparse(location)
                        phone_param = parse_qs(parsed_url.query).get("phone", [""])[0]
                        if phone_param:
                            digits = "".join(c for c in phone_param if c.isdigit())
                            if len(digits) >= 7:
                                return f"+{digits}"
                except Exception:
                    logger.debug("Failed to resolve wa.link URL: %s", url)

        return None
