import logging
from datetime import datetime, timezone

import httpx

from app.exceptions.custom import HubSpotError, RateLimitError
from app.schemas.hubspot import HubSpotCompany, HubSpotContact, HubSpotEmail, HubSpotNote

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.hubapi.com/crm/v3/objects/companies/search"
COMPANY_URL = "https://api.hubapi.com/crm/v3/objects/companies"

CALLS_URL = "https://api.hubapi.com/crm/v3/objects/calls"
MERGE_URL = "https://api.hubapi.com/crm/v3/objects/companies/merge"
FILES_URL = "https://api.hubapi.com/files/v3/files"
NOTES_URL = "https://api.hubapi.com/crm/v3/objects/notes"
TASKS_URL = "https://api.hubapi.com/crm/v3/objects/tasks"
CONTACTS_URL = "https://api.hubapi.com/crm/v3/objects/contacts"
EMAILS_URL = "https://api.hubapi.com/crm/v3/objects/emails"
ASSOCIATIONS_URL = "https://api.hubapi.com/crm/v4/objects/companies"

CONTACT_PROPERTIES = [
    "firstname", "lastname", "email", "phone", "mobilephone", "jobtitle",
    "hs_whatsapp_phone_number",
]

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
    "market_fit",
    "plaza",
]


class HubSpotService:
    def __init__(self, client: httpx.AsyncClient, access_token: str):
        self._client = client
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        self._email_fetch_disabled = False

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
            "limit": 1,
        }

        resp = await self._client.post(
            SEARCH_URL, json=payload, headers=self._headers
        )

        if resp.status_code == 429:
            raise RateLimitError("HubSpot")
        if resp.status_code >= 400:
            raise HubSpotError(resp.text, status_code=resp.status_code)

        data = resp.json()
        companies = [HubSpotCompany(**r) for r in data.get("results", [])]

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

    async def merge_companies(self, primary_id: str, merge_id: str) -> None:
        """Merge merge_id INTO primary_id. The primary survives."""
        resp = await self._client.post(
            MERGE_URL,
            json={"primaryObjectId": primary_id, "objectIdToMerge": merge_id},
            headers=self._headers,
        )

        if resp.status_code == 429:
            raise RateLimitError("HubSpot")
        if resp.status_code >= 400:
            raise HubSpotError(resp.text, status_code=resp.status_code)

        logger.info("Merged company %s into %s", merge_id, primary_id)

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
                "hs_timestamp": datetime.now(timezone.utc).isoformat(),
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

    async def _get_associated_ids(
        self, company_id: str, to_object_type: str
    ) -> list[str]:
        url = f"{ASSOCIATIONS_URL}/{company_id}/associations/{to_object_type}"
        resp = await self._client.get(url, headers=self._headers)

        if resp.status_code == 429:
            raise RateLimitError("HubSpot")
        if resp.status_code >= 400:
            raise HubSpotError(resp.text, status_code=resp.status_code)

        return [r["toObjectId"] for r in resp.json().get("results", [])]

    async def get_associated_contacts(
        self, company_id: str
    ) -> list[HubSpotContact]:
        ids = await self._get_associated_ids(company_id, "contacts")
        contacts: list[HubSpotContact] = []
        for obj_id in ids:
            url = f"{CONTACTS_URL}/{obj_id}"
            resp = await self._client.get(
                url,
                params={"properties": ",".join(CONTACT_PROPERTIES)},
                headers=self._headers,
            )
            if resp.status_code >= 400:
                logger.warning("Failed to fetch contact %s: %s", obj_id, resp.status_code)
                continue
            contacts.append(HubSpotContact(**resp.json()))
        logger.info("Fetched %d contacts for company %s", len(contacts), company_id)
        return contacts

    async def get_associated_notes(
        self, company_id: str, limit: int = 10
    ) -> list[HubSpotNote]:
        ids = await self._get_associated_ids(company_id, "notes")
        notes: list[HubSpotNote] = []
        for obj_id in ids[:limit]:
            url = f"{NOTES_URL}/{obj_id}"
            resp = await self._client.get(
                url,
                params={"properties": "hs_note_body,hs_timestamp"},
                headers=self._headers,
            )
            if resp.status_code >= 400:
                logger.warning("Failed to fetch note %s: %s", obj_id, resp.status_code)
                continue
            notes.append(HubSpotNote(**resp.json()))
        logger.info("Fetched %d notes for company %s", len(notes), company_id)
        return notes

    async def get_associated_emails(
        self, company_id: str, limit: int = 10
    ) -> list[HubSpotEmail]:
        if self._email_fetch_disabled:
            return []

        ids = await self._get_associated_ids(company_id, "emails")
        emails: list[HubSpotEmail] = []
        for obj_id in ids[:limit]:
            url = f"{EMAILS_URL}/{obj_id}"
            resp = await self._client.get(
                url,
                params={"properties": "hs_email_subject,hs_email_direction,hs_timestamp"},
                headers=self._headers,
            )
            if resp.status_code == 403:
                logger.info(
                    "Email fetch returned 403 (missing scope), disabling for this session"
                )
                self._email_fetch_disabled = True
                return emails
            if resp.status_code >= 400:
                logger.warning("Failed to fetch email %s: %s", obj_id, resp.status_code)
                continue
            emails.append(HubSpotEmail(**resp.json()))
        logger.info("Fetched %d emails for company %s", len(emails), company_id)
        return emails

    async def create_contact(
        self, company_id: str, properties: dict[str, str]
    ) -> str:
        payload = {
            "properties": properties,
            "associations": [
                {
                    "to": {"id": company_id},
                    "types": [
                        {
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": 1,
                        }
                    ],
                }
            ],
        }

        resp = await self._client.post(
            CONTACTS_URL, json=payload, headers=self._headers
        )

        if resp.status_code == 429:
            raise RateLimitError("HubSpot")
        if resp.status_code >= 400:
            raise HubSpotError(resp.text, status_code=resp.status_code)

        contact_id = resp.json().get("id", "")
        logger.info("Created contact %s for company %s", contact_id, company_id)
        return contact_id

    async def update_contact(
        self, contact_id: str, properties: dict[str, str]
    ) -> None:
        url = f"{CONTACTS_URL}/{contact_id}"
        resp = await self._client.patch(
            url, json={"properties": properties}, headers=self._headers
        )

        if resp.status_code == 429:
            raise RateLimitError("HubSpot")
        if resp.status_code >= 400:
            raise HubSpotError(resp.text, status_code=resp.status_code)

        logger.info("Updated contact %s", contact_id)

    async def delete_contact(self, contact_id: str) -> None:
        url = f"{CONTACTS_URL}/{contact_id}"
        resp = await self._client.delete(url, headers=self._headers)

        if resp.status_code == 429:
            raise RateLimitError("HubSpot")
        if resp.status_code >= 400:
            raise HubSpotError(resp.text, status_code=resp.status_code)

        logger.info("Deleted contact %s", contact_id)

    async def upload_file(
        self, filename: str, content: bytes, content_type: str = "audio/mpeg"
    ) -> str:
        import json as _json

        headers = {"Authorization": self._headers["Authorization"]}
        resp = await self._client.post(
            FILES_URL,
            headers=headers,
            files={"file": (filename, content, content_type)},
            data={
                "options": _json.dumps({"access": "PRIVATE"}),
                "folderPath": "/calls",
            },
            timeout=120.0,
        )

        if resp.status_code == 429:
            raise RateLimitError("HubSpot")
        if resp.status_code >= 400:
            raise HubSpotError(resp.text, status_code=resp.status_code)

        file_url = resp.json().get("url", "")
        logger.info("Uploaded file %s → %s", filename, file_url)
        return file_url

    async def create_call(
        self, company_id: str, properties: dict
    ) -> None:
        payload = {
            "properties": properties,
            "associations": [
                {
                    "to": {"id": company_id},
                    "types": [
                        {
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": 182,
                        }
                    ],
                }
            ],
        }

        resp = await self._client.post(
            CALLS_URL, json=payload, headers=self._headers
        )

        if resp.status_code == 429:
            raise RateLimitError("HubSpot")
        if resp.status_code >= 400:
            raise HubSpotError(resp.text, status_code=resp.status_code)

        logger.info("Created call for company %s", company_id)

    async def create_task(
        self, company_id: str, properties: dict[str, str]
    ) -> str:
        payload = {
            "properties": properties,
            "associations": [
                {
                    "to": {"id": company_id},
                    "types": [
                        {
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": 192,
                        }
                    ],
                }
            ],
        }

        resp = await self._client.post(
            TASKS_URL, json=payload, headers=self._headers
        )

        if resp.status_code == 429:
            raise RateLimitError("HubSpot")
        if resp.status_code >= 400:
            raise HubSpotError(resp.text, status_code=resp.status_code)

        task_id = resp.json().get("id", "")
        logger.info("Created task %s for company %s", task_id, company_id)
        return task_id
