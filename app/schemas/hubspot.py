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
    plaza: str | None = None
    cantidad_de_habitaciones: str | None = None
    habitaciones: str | None = None
    booking_url: str | None = None
    tipo_de_empresa: str | None = None
    lifecyclestage: str | None = None


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
    hs_whatsapp_phone_number: str | None = None


class HubSpotContact(BaseModel):
    id: str
    properties: HubSpotContactProperties


class HubSpotNote(BaseModel):
    id: str
    properties: dict


class HubSpotEmail(BaseModel):
    id: str
    properties: dict


class HubSpotLeadProperties(BaseModel):
    hubspot_owner_id: str | None = None
    hs_lead_name: str | None = None
    hs_pipeline_stage: str | None = None


class HubSpotLead(BaseModel):
    id: str
    properties: HubSpotLeadProperties
