from pydantic import BaseModel


class ParsedAddress(BaseModel):
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    country: str | None = None
