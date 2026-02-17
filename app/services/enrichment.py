import asyncio
import logging

from app.exceptions.custom import RateLimitError
from app.mappers.address_mapper import parse_address_components
from app.mappers.field_merger import merge_fields
from app.mappers.note_builder import build_enrichment_note, build_error_note
from app.schemas.responses import CompanyResult, EnrichmentResponse
from app.services.google_places import GooglePlacesService, build_search_query
from app.services.hubspot import HubSpotService
from app.schemas.google_places import GooglePlace
from app.schemas.tripadvisor import TripAdvisorLocation
from app.schemas.website import WebScrapedData
from app.services.tripadvisor import TripAdvisorService, clean_name
from app.services.website_scraper import WebsiteScraperService

logger = logging.getLogger(__name__)


def _normalize_phone(phone: str) -> str:
    """Normalize phone to E.164: strip non-digits, prepend '+'."""
    digits = "".join(c for c in phone if c.isdigit())
    return f"+{digits}" if digits else ""

HUBSPOT_DELAY = 0.5  # seconds between HubSpot calls
MAX_COMPANIES_PER_REQUEST = 1


class EnrichmentService:
    def __init__(
        self,
        hubspot: HubSpotService,
        google_places: GooglePlacesService,
        tripadvisor: TripAdvisorService | None = None,
        website_scraper: WebsiteScraperService | None = None,
        overwrite: bool = False,
    ):
        self._hubspot = hubspot
        self._google = google_places
        self._tripadvisor = tripadvisor
        self._website_scraper = website_scraper
        self._overwrite = overwrite

    async def run(self, company_id: str | None = None) -> EnrichmentResponse:
        if company_id:
            company = await self._hubspot.get_company(company_id)
            companies = [company]
        else:
            all_companies = await self._hubspot.search_companies()
            companies = all_companies[:MAX_COMPANIES_PER_REQUEST]
        results: list[CompanyResult] = []
        enriched = 0
        no_results = 0
        errors = 0

        for company in companies:
            try:
                result = await self._process_company(company)
                results.append(result)

                if result.status == "enriched":
                    enriched += 1
                elif result.status == "no_results":
                    no_results += 1

            except RateLimitError as exc:
                logger.warning(
                    "Rate limit hit (%s), stopping with partial results", exc.service
                )
                error_msg = f"Rate limit: {exc.service}"
                results.append(
                    CompanyResult(
                        company_id=company.id,
                        company_name=company.properties.name,
                        status="error",
                        message=error_msg,
                    )
                )
                try:
                    note = build_error_note("Datos", company.properties.name, "error", error_msg)
                    await self._hubspot.create_note(company.id, note)
                except Exception:
                    logger.exception("Failed to create error note for company %s", company.id)
                errors += 1
                break

            except Exception as exc:
                logger.exception("Error processing company %s", company.id)
                error_msg = str(exc)
                results.append(
                    CompanyResult(
                        company_id=company.id,
                        company_name=company.properties.name,
                        status="error",
                        message=error_msg,
                    )
                )
                try:
                    note = build_error_note("Datos", company.properties.name, "error", error_msg)
                    await self._hubspot.create_note(company.id, note)
                except Exception:
                    logger.exception("Failed to create error note for company %s", company.id)
                errors += 1

            await asyncio.sleep(HUBSPOT_DELAY)

        return EnrichmentResponse(
            total_found=len(companies),
            enriched=enriched,
            no_results=no_results,
            errors=errors,
            results=results,
        )

    async def _process_company(self, company):
        props = company.properties

        # --- Google Places (always text_search) ---
        query = build_search_query(props.name, props.city, props.country)
        logger.info("Searching Google Places for: %s", query)
        place = await self._google.text_search(query)

        # --- TripAdvisor (isolated, never blocks enrichment) ---
        ta_location = None
        ta_photos = None
        if self._tripadvisor:
            try:
                if props.id_tripadvisor and props.id_tripadvisor.strip():
                    logger.info("Looking up TripAdvisor ID: %s", props.id_tripadvisor)
                    ta_location = await self._tripadvisor.get_details(
                        props.id_tripadvisor.strip()
                    )
                else:
                    ta_query = clean_name(props.name or "")
                    lat_long = None
                    if place and place.location:
                        lat_long = f"{place.location.latitude},{place.location.longitude}"
                    logger.info("Searching TripAdvisor for: %s (latLong=%s)", ta_query, lat_long)
                    ta_location = await self._tripadvisor.search_and_get_details(
                        ta_query, company_name=props.name, lat_long=lat_long,
                    )
            except Exception:
                logger.exception(
                    "TripAdvisor failed for company %s, continuing without it",
                    company.id,
                )

            # Fetch photos (separate try/except — photos failure never blocks)
            location_id = (
                ta_location.location_id if ta_location
                else (props.id_tripadvisor or "").strip()
            )
            if location_id:
                try:
                    ta_photos = await self._tripadvisor.get_photos(location_id)
                except Exception:
                    logger.exception(
                        "TripAdvisor photos failed for company %s, continuing without them",
                        company.id,
                    )

        # --- Website scraping (isolated, never blocks enrichment) ---
        web_data: WebScrapedData | None = None
        if self._website_scraper:
            website_url = None
            if place and place.websiteUri:
                website_url = place.websiteUri
            elif props.website and props.website.strip():
                website_url = props.website.strip()
            if website_url:
                try:
                    web_data = await self._website_scraper.scrape(website_url)
                except Exception:
                    logger.exception(
                        "Website scrape failed for company %s, continuing without it",
                        company.id,
                    )

        # --- Merge results ---
        if place is None and ta_location is None:
            await self._hubspot.update_company(company.id, {"agente": ""})
            return CompanyResult(
                company_id=company.id,
                company_name=props.name,
                status="no_results",
                message="No results from Google Places or TripAdvisor",
            )

        updates: dict[str, str] = {}
        changes = []

        if place is not None:
            parsed = parse_address_components(place.addressComponents)
            google_updates, google_changes = merge_fields(
                props, place, parsed, self._overwrite
            )
            updates.update(google_updates)
            changes.extend(google_changes)

            # Always write place_id and display name (no smart merge)
            if place.id:
                updates["id_hotel"] = place.id
            if place.displayName and place.displayName.text:
                updates["name"] = place.displayName.text

        # Normalize company phone to E.164
        if "phone" in updates:
            updates["phone"] = _normalize_phone(updates["phone"])
        elif props.phone and props.phone.strip():
            normalized = _normalize_phone(props.phone)
            if normalized != props.phone:
                updates["phone"] = normalized

        # Fill company phone from web scrape if still empty
        if "phone" not in updates and not (props.phone and props.phone.strip()):
            if web_data and web_data.phones:
                updates["phone"] = web_data.phones[0]

        if ta_location and ta_location.location_id:
            if self._overwrite or not (props.id_tripadvisor and props.id_tripadvisor.strip()):
                updates["id_tripadvisor"] = ta_location.location_id

        # Always clear agente
        updates["agente"] = ""

        await self._hubspot.update_company(company.id, updates)

        # --- Create enrichment note ---
        note_body = build_enrichment_note(
            props.name, place, ta_location, ta_photos=ta_photos, web_data=web_data,
        )
        try:
            await self._hubspot.create_note(company.id, note_body)
        except Exception:
            logger.exception(
                "Failed to create note for company %s, enrichment still succeeded",
                company.id,
            )

        # --- Create contacts from phone numbers and web data (best-effort) ---
        await self._create_contacts(company.id, props.name, place, ta_location, web_data)

        return CompanyResult(
            company_id=company.id,
            company_name=props.name,
            status="enriched",
            changes=changes,
            note=note_body,
        )

    async def _create_contacts(
        self,
        company_id: str,
        company_name: str | None,
        place: GooglePlace | None,
        ta_location: TripAdvisorLocation | None,
        web_data: WebScrapedData | None = None,
    ) -> None:
        """Create contacts from TripAdvisor and website data (best-effort).

        The Google phone is already on the company field, so we only create
        contacts for *different* phones or web-scraped emails.
        """
        try:
            def _digits(p: str) -> str:
                return "".join(ch for ch in p if ch.isdigit())

            name = company_name or "Hotel"

            # Get Google E.164 phone (for comparison)
            google_phone = ""
            if place:
                raw = place.internationalPhoneNumber or place.nationalPhoneNumber
                if raw and raw.strip():
                    google_phone = _normalize_phone(raw)

            # Track all known phones (digits) to avoid cross-source dupes
            all_known: set[str] = set()
            if google_phone:
                all_known.add(_digits(google_phone))

            # Check existing contacts
            need_existing = False
            ta_phone = ""
            if ta_location and ta_location.phone and ta_location.phone.strip():
                ta_phone = _normalize_phone(ta_location.phone)
                if ta_phone and _digits(ta_phone) not in all_known:
                    need_existing = True

            has_web_data = web_data and (web_data.phones or web_data.emails)
            if has_web_data:
                need_existing = True

            existing_phones: set[str] = set()
            existing_emails: set[str] = set()
            if need_existing:
                existing_contacts = await self._hubspot.get_associated_contacts(company_id)
                for c in existing_contacts:
                    for field in (c.properties.phone, c.properties.mobilephone):
                        if field and field.strip():
                            existing_phones.add(_digits(field))
                    if c.properties.email and c.properties.email.strip():
                        existing_emails.add(c.properties.email.strip().lower())

            all_known.update(existing_phones)

            # --- TripAdvisor phone contact ---
            if ta_phone and _digits(ta_phone) not in all_known:
                contact_props = {
                    "firstname": f"Recepcion {name}",
                    "lastname": "/ TripAdvisor",
                    "phone": ta_phone,
                }
                await self._hubspot.create_contact(company_id, contact_props)
                all_known.add(_digits(ta_phone))
                logger.info(
                    "Created TripAdvisor phone contact (%s) for company %s",
                    ta_phone, company_id,
                )

            # --- Website contacts ---
            if web_data:
                # Find first unique web phone
                web_phone = ""
                for p in (web_data.phones or []):
                    if _digits(p) not in all_known:
                        web_phone = p
                        break

                web_whatsapp = web_data.whatsapp or ""
                # Find first unique web email
                web_email = ""
                for e in (web_data.emails or []):
                    if e.lower() not in existing_emails:
                        web_email = e
                        break

                if web_email:
                    contact_props = {
                        "firstname": f"Recepcion {name}",
                        "lastname": "/ Website",
                        "email": web_email,
                    }
                    if web_phone:
                        contact_props["phone"] = web_phone
                    if web_whatsapp:
                        contact_props["mobilephone"] = web_whatsapp
                    await self._hubspot.create_contact(company_id, contact_props)
                    if web_phone:
                        all_known.add(_digits(web_phone))
                    logger.info(
                        "Created website email contact (%s) for company %s",
                        web_email, company_id,
                    )
                elif web_phone:
                    contact_props = {
                        "firstname": f"Recepcion {name}",
                        "lastname": "/ Website",
                        "phone": web_phone,
                    }
                    if web_whatsapp:
                        contact_props["mobilephone"] = web_whatsapp
                    await self._hubspot.create_contact(company_id, contact_props)
                    all_known.add(_digits(web_phone))
                    logger.info(
                        "Created website phone contact (%s) for company %s",
                        web_phone, company_id,
                    )

        except Exception:
            logger.exception(
                "Failed to create contacts for company %s, enrichment still succeeded",
                company_id,
            )
