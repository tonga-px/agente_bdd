from pydantic import BaseModel


class AddressComponent(BaseModel):
    longText: str
    shortText: str
    types: list[str]


class GooglePlace(BaseModel):
    formattedAddress: str | None = None
    nationalPhoneNumber: str | None = None
    internationalPhoneNumber: str | None = None
    websiteUri: str | None = None
    addressComponents: list[AddressComponent] = []


class TextSearchResponse(BaseModel):
    places: list[GooglePlace] = []
