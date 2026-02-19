from pydantic import BaseModel


class WebScrapedData(BaseModel):
    phones: list[str] = []  # E.164, priority-ordered
    whatsapp: str | None = None  # E.164
    emails: list[str] = []  # preference-ranked
    instagram_url: str | None = None  # Instagram profile URL found in website
    source_url: str | None = None
    raw_html: str | None = None  # preserved for downstream scrapers (e.g. Booking)
