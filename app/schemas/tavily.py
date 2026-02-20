from pydantic import BaseModel


class ReputationData(BaseModel):
    google_rating: float | None = None  # /5
    google_review_count: int | None = None
    tripadvisor_rating: float | None = None  # /5
    tripadvisor_review_count: int | None = None
    booking_rating: float | None = None  # /10
    booking_review_count: int | None = None
    summary: str | None = None  # Tavily answer summary


class ScrapedListingData(BaseModel):
    source: str  # "Booking.com" or "Hoteles.com"
    url: str | None = None
    rooms: int | None = None
    nightly_rate_usd: str | None = None  # e.g. "US$85", "$120"
    review_count: int | None = None
