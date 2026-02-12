import asyncio
import logging

from app.exceptions.custom import RateLimitError
from app.mappers.address_mapper import parse_address_components
from app.mappers.field_merger import merge_fields
from app.schemas.responses import CompanyResult, EnrichmentResponse
from app.services.google_places import GooglePlacesService, build_search_query
from app.services.hubspot import HubSpotService

logger = logging.getLogger(__name__)

HUBSPOT_DELAY = 0.1  # seconds between HubSpot calls


class EnrichmentService:
    def __init__(
        self,
        hubspot: HubSpotService,
        google_places: GooglePlacesService,
        overwrite: bool = False,
    ):
        self._hubspot = hubspot
        self._google = google_places
        self._overwrite = overwrite

    async def run(self) -> EnrichmentResponse:
        companies = await self._hubspot.search_companies()
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
        query = build_search_query(props.name, props.city, props.country)
        logger.info("Searching Google Places for: %s", query)

        place = await self._google.text_search(query)

        if place is None:
            # Still clear agente so it's not reprocessed
            await self._hubspot.update_company(company.id, {"agente": ""})
            return CompanyResult(
                company_id=company.id,
                company_name=props.name,
                status="no_results",
                message=f"No Google Places results for: {query}",
            )

        parsed = parse_address_components(place.addressComponents)
        updates, changes = merge_fields(props, place, parsed, self._overwrite)

        # Always clear agente
        updates["agente"] = ""

        await self._hubspot.update_company(company.id, updates)

        return CompanyResult(
            company_id=company.id,
            company_name=props.name,
            status="enriched",
            changes=changes,
        )
