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
