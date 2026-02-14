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
    id_hotel: str | None = None
    id_tripadvisor: str | None = None
    market_fit: str | None = None


class HubSpotCompany(BaseModel):
    id: str
    properties: HubSpotCompanyProperties


class HubSpotContactProperties(BaseModel):
    firstname: str | None = None
    lastname: str | None = None
    email: str | None = None
    phone: str | None = None
    mobilephone: str | None = None
    jobtitle: str | None = None


class HubSpotContact(BaseModel):
    id: str
    properties: HubSpotContactProperties


class HubSpotNote(BaseModel):
    id: str
    properties: dict


class HubSpotEmail(BaseModel):
    id: str
    properties: dict
