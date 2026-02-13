from pydantic import BaseModel, Field


class TripAdvisorSearchResult(BaseModel):
    location_id: str
    name: str | None = None
    address_obj: dict | None = None


class TripAdvisorSearchResponse(BaseModel):
    data: list[TripAdvisorSearchResult] = []


class TripAdvisorLocation(BaseModel):
    location_id: str = ""
    name: str | None = None
    rating: str | None = None
    num_reviews: str | None = None
    ranking_data: dict | None = None
    price_level: str | None = None
    category: dict | None = None
    subcategory: list[dict] = []
    web_url: str | None = None
    description: str | None = None
    awards: list[dict] = []
    amenities: list[str] = []
    trip_types: list[dict] = []
    review_rating_count: dict | None = None
    phone: str | None = None
    email: str | None = None
