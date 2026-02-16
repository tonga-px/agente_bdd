import asyncio
import logging

from app.exceptions.custom import GooglePlacesError, RateLimitError
from app.mappers.address_mapper import parse_address_components
from app.mappers.field_merger import merge_fields
from app.mappers.note_builder import build_enrichment_note
from app.schemas.responses import CompanyResult, EnrichmentResponse
from app.services.google_places import GooglePlacesService, build_search_query
from app.services.hubspot import HubSpotService
from app.services.tripadvisor import TripAdvisorService, clean_name

logger = logging.getLogger(__name__)

HUBSPOT_DELAY = 0.5  # seconds between HubSpot calls
MAX_COMPANIES_PER_REQUEST = 1


class EnrichmentService:
    def __init__(
        self,
        hubspot: HubSpotService,
        google_places: GooglePlacesService,
        tripadvisor: TripAdvisorService | None = None,
        overwrite: bool = False,
    ):
        self._hubspot = hubspot
        self._google = google_places
        self._tripadvisor = tripadvisor
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
                results.append(
                    CompanyResult(
                        company_id=company.id,
                        company_name=company.properties.name,
                        status="error",
                        message=f"Rate limit: {exc.service}",
                    )
                )
                errors += 1
                break

            except Exception as exc:
                logger.exception("Error processing company %s", company.id)
                results.append(
                    CompanyResult(
                        company_id=company.id,
                        company_name=company.properties.name,
                        status="error",
                        message=str(exc),
                    )
                )
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

        # --- Google Places ---
        place = None
        query = build_search_query(props.name, props.city, props.country)

        if props.id_hotel and props.id_hotel.strip():
            place_id = props.id_hotel.strip()
            logger.info("Looking up Google Place ID: %s", place_id)
            try:
                place = await self._google.get_place_details(place_id)
            except GooglePlacesError as exc:
                logger.warning(
                    "Google Place ID %s failed (status=%s), falling back to text search",
                    place_id, exc.status_code,
                )

        if place is None:
            logger.info("Searching Google Places for: %s", query)
            place = await self._google.text_search(query)

        # --- TripAdvisor (isolated, never blocks enrichment) ---
        ta_location = None
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

        if ta_location and ta_location.location_id:
            if self._overwrite or not (props.id_tripadvisor and props.id_tripadvisor.strip()):
                updates["id_tripadvisor"] = ta_location.location_id

        # Always clear agente
        updates["agente"] = ""

        await self._hubspot.update_company(company.id, updates)

        # --- Create enrichment note ---
        note_body = build_enrichment_note(props.name, place, ta_location)
        try:
            await self._hubspot.create_note(company.id, note_body)
        except Exception:
            logger.exception(
                "Failed to create note for company %s, enrichment still succeeded",
                company.id,
            )

        return CompanyResult(
            company_id=company.id,
            company_name=props.name,
            status="enriched",
            changes=changes,
            note=note_body,
        )
