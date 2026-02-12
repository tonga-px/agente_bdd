from pydantic import BaseModel


class HubSpotCompanyProperties(BaseModel):
    name: str | None = None
    domain: str | None = None
    phone: str | None = None
    website: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    country: str | None = None
    agente: str | None = None


class HubSpotCompany(BaseModel):
    id: str
    properties: HubSpotCompanyProperties
