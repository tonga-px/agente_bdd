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
    ta_rating: str | None = None
    ta_reviews_count: str | None = None
    ta_ranking: str | None = None
    ta_price_level: str | None = None
    ta_category: str | None = None
    ta_subcategory: str | None = None
    ta_url: str | None = None


class HubSpotCompany(BaseModel):
    id: str
    properties: HubSpotCompanyProperties
