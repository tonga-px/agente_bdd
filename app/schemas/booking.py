from pydantic import BaseModel


class BookingData(BaseModel):
    url: str | None = None
    rating: float | None = None  # 1-10 scale (e.g. 8.4)
    review_count: int | None = None  # e.g. 1567
    price_range: str | None = None  # from JSON-LD priceRange
    hotel_name: str | None = None  # name on Booking (may differ)
