from pydantic import BaseModel


class ReputationData(BaseModel):
    google_rating: float | None = None  # /5
    google_review_count: int | None = None
    tripadvisor_rating: float | None = None  # /5
    tripadvisor_review_count: int | None = None
    booking_rating: float | None = None  # /10
    booking_review_count: int | None = None
    summary: str | None = None  # Tavily answer summary
