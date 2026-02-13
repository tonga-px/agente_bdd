import logging

import httpx

from app.exceptions.custom import HubSpotError, RateLimitError
from app.schemas.hubspot import HubSpotCompany

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.hubapi.com/crm/v3/objects/companies/search"
COMPANY_URL = "https://api.hubapi.com/crm/v3/objects/companies"

NOTES_URL = "https://api.hubapi.com/crm/v3/objects/notes"

SEARCH_PROPERTIES = [
    "name",
    "domain",
    "phone",
    "website",
    "address",
    "city",
    "state",
    "zip",
    "country",
    "agente",
    "id_hotel",
    "id_tripadvisor",
    "ta_rating",
    "ta_reviews_count",
    "ta_ranking",
    "ta_price_level",
    "ta_category",
    "ta_subcategory",
    "ta_url",
]


class HubSpotService:
    def __init__(self, client: httpx.AsyncClient, access_token: str):
        self._client = client
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    async def search_companies(self, agente_value: str = "datos") -> list[HubSpotCompany]:
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "agente",
                            "operator": "EQ",
                            "value": agente_value,
                        }
                    ]
                }
            ],
            "properties": SEARCH_PROPERTIES,
            "limit": 100,
        }

        companies: list[HubSpotCompany] = []
        after: str | None = None

        while True:
            if after:
                payload["after"] = after

            resp = await self._client.post(
                SEARCH_URL, json=payload, headers=self._headers
            )

            if resp.status_code == 429:
                raise RateLimitError("HubSpot")
            if resp.status_code >= 400:
                if companies:
                    logger.warning(
                        "HubSpot search returned %d on page fetch, "
                        "returning %d companies collected so far",
                        resp.status_code,
                        len(companies),
                    )
                    break
                raise HubSpotError(resp.text, status_code=resp.status_code)

            data = resp.json()
            for result in data.get("results", []):
                companies.append(HubSpotCompany(**result))

            paging = data.get("paging", {}).get("next")
            if paging:
                after = paging["after"]
            else:
                break

        logger.info("Found %d companies with agente='%s'", len(companies), agente_value)
        return companies

    async def get_company(self, company_id: str) -> HubSpotCompany:
        url = f"{COMPANY_URL}/{company_id}"
        resp = await self._client.get(
            url,
            params={"properties": ",".join(SEARCH_PROPERTIES)},
            headers=self._headers,
        )

        if resp.status_code == 429:
            raise RateLimitError("HubSpot")
        if resp.status_code >= 400:
            raise HubSpotError(resp.text, status_code=resp.status_code)

        logger.info("Fetched company %s", company_id)
        return HubSpotCompany(**resp.json())

    async def update_company(
        self, company_id: str, properties: dict[str, str]
    ) -> None:
        url = f"{COMPANY_URL}/{company_id}"
        resp = await self._client.patch(
            url, json={"properties": properties}, headers=self._headers
        )

        if resp.status_code == 429:
            raise RateLimitError("HubSpot")
        if resp.status_code >= 400:
            raise HubSpotError(resp.text, status_code=resp.status_code)

        logger.info("Updated company %s", company_id)

    async def create_note(self, company_id: str, body: str) -> None:
        payload = {
            "properties": {
                "hs_note_body": body,
                "hs_timestamp": None,
            },
            "associations": [
                {
                    "to": {"id": company_id},
                    "types": [
                        {
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": 190,
                        }
                    ],
                }
            ],
        }

        resp = await self._client.post(
            NOTES_URL, json=payload, headers=self._headers
        )

        if resp.status_code == 429:
            raise RateLimitError("HubSpot")
        if resp.status_code >= 400:
            raise HubSpotError(resp.text, status_code=resp.status_code)

        logger.info("Created note for company %s", company_id)
