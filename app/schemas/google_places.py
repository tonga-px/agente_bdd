from pydantic import BaseModel


class AddressComponent(BaseModel):
    longText: str | None = None
    shortText: str | None = None
    types: list[str] = []


class LatLng(BaseModel):
    latitude: float = 0.0
    longitude: float = 0.0


class DisplayName(BaseModel):
    text: str | None = None


class GooglePlace(BaseModel):
    id: str | None = None
    displayName: DisplayName | None = None
    formattedAddress: str | None = None
    nationalPhoneNumber: str | None = None
    internationalPhoneNumber: str | None = None
    websiteUri: str | None = None
    addressComponents: list[AddressComponent] = []
    location: LatLng | None = None
    rating: float | None = None
    userRatingCount: int | None = None
    googleMapsUri: str | None = None
    priceLevel: str | None = None
    businessStatus: str | None = None


class TextSearchResponse(BaseModel):
    places: list[GooglePlace] = []
