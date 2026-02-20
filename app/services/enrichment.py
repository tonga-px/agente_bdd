import asyncio
import logging
import re

from app.exceptions.custom import HubSpotError, RateLimitError
from app.mappers.address_mapper import parse_address_components
from app.mappers.field_merger import merge_fields
from app.mappers.market_fit import compute_market_fit
from app.mappers.task_scheduler import (
    build_task_body,
    build_task_subject,
    compute_task_due_date,
)
from app.mappers.note_builder import (
    build_conflict_note,
    build_enrichment_note,
    build_error_note,
    build_merge_note,
)
from app.schemas.booking import BookingData
from app.schemas.instagram import InstagramData
from app.schemas.responses import CompanyResult, EnrichmentResponse
from app.schemas.tavily import ReputationData, ScrapedListingData
from app.services.perplexity import PerplexityService
from app.services.google_places import GooglePlacesService, build_search_query
from app.services.hubspot import HubSpotService
from app.schemas.google_places import GooglePlace
from app.schemas.hubspot import HubSpotCompanyProperties, HubSpotContact
from app.schemas.tripadvisor import TripAdvisorLocation
from app.schemas.website import WebScrapedData
from app.services.tripadvisor import TripAdvisorService, clean_name
from app.services.website_scraper import WebsiteScraperService

logger = logging.getLogger(__name__)


def _normalize_phone(phone: str) -> str:
    """Normalize phone to E.164: strip non-digits, prepend '+'.

    Returns "" for invalid numbers:
    - starts with 0 (local number without country code)
    - fewer than 7 or more than 15 digits (E.164 limits)
    """
    digits = "".join(c for c in phone if c.isdigit())
    if not digits:
        return ""
    if digits[0] == "0":
        logger.debug("Rejected local phone (starts with 0): %s", phone)
        return ""
    if len(digits) < 7 or len(digits) > 15:
        logger.debug("Rejected phone outside E.164 range (%d digits): %s", len(digits), phone)
        return ""
    return f"+{digits}"

_DUPLICATE_RE = re.compile(r"(\d+) already has that value")


def _extract_conflicting_id(error_message: str) -> str | None:
    """Extract the conflicting company ID from a HubSpot VALIDATION_ERROR."""
    match = _DUPLICATE_RE.search(error_message)
    return match.group(1) if match else None


def _is_same_company(
    props_a: HubSpotCompanyProperties,
    props_b: HubSpotCompanyProperties,
) -> bool:
    """Check if two companies likely represent the same hotel."""
    def _norm(s: str | None) -> str:
        return (s or "").strip().lower()

    name_a, name_b = _norm(props_a.name), _norm(props_b.name)
    if not name_a or not name_b:
        return False

    # Name must match: exact, or one contains the other
    names_match = name_a == name_b or name_a in name_b or name_b in name_a
    if not names_match:
        return False

    # City must match (if both have values)
    city_a, city_b = _norm(props_a.city), _norm(props_b.city)
    if city_a and city_b and city_a != city_b:
        return False

    # Country must match (if both have values)
    country_a, country_b = _norm(props_a.country), _norm(props_b.country)
    if country_a and country_b and country_a != country_b:
        return False

    return True


def _contact_identity_keys(contact: HubSpotContact) -> set[str]:
    """Return normalized identity keys for a contact (for dedup grouping)."""
    keys: set[str] = set()
    p = contact.properties
    if p.email and p.email.strip():
        keys.add(f"email:{p.email.strip().lower()}")
    for phone_val in (p.phone, p.mobilephone, p.hs_whatsapp_phone_number):
        if phone_val and phone_val.strip():
            digits = "".join(ch for ch in phone_val if ch.isdigit())
            if digits:
                keys.add(f"phone:{digits}")
    return keys


def _dedup_contacts(
    contacts: list[HubSpotContact],
) -> list[tuple[HubSpotContact, list[HubSpotContact]]]:
    """Group contacts that share any identity field (transitive via Union-Find).

    Returns list of (keeper, [duplicates]) for groups with duplicates.
    Keeper = most identity fields filled; ties broken by lowest ID (oldest).
    """
    if len(contacts) < 2:
        return []

    # Union-Find
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Build identity key → contact id mapping, and union contacts sharing keys
    key_to_cid: dict[str, str] = {}
    cid_keys: dict[str, set[str]] = {}

    for c in contacts:
        keys = _contact_identity_keys(c)
        cid_keys[c.id] = keys
        for k in keys:
            if k in key_to_cid:
                union(c.id, key_to_cid[k])
            else:
                key_to_cid[k] = c.id

    # Group contacts by root
    groups: dict[str, list[HubSpotContact]] = {}
    for c in contacts:
        root = find(c.id)
        groups.setdefault(root, []).append(c)

    # For each group with >1 member, pick keeper
    result: list[tuple[HubSpotContact, list[HubSpotContact]]] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        # Sort: most identity fields desc, then lowest ID asc
        members.sort(key=lambda c: (-len(cid_keys[c.id]), int(c.id)))
        keeper = members[0]
        duplicates = members[1:]
        result.append((keeper, duplicates))

    return result


def _merge_contact_fields(
    keeper: HubSpotContact, duplicate: HubSpotContact,
) -> dict[str, str]:
    """Return identity fields the keeper is missing but the duplicate has."""
    updates: dict[str, str] = {}
    for field in ("email", "phone", "mobilephone", "hs_whatsapp_phone_number"):
        keeper_val = getattr(keeper.properties, field) or ""
        dup_val = getattr(duplicate.properties, field) or ""
        if not keeper_val.strip() and dup_val.strip():
            updates[field] = dup_val.strip()
    return updates


HUBSPOT_DELAY = 0.5  # seconds between HubSpot calls
MAX_COMPANIES_PER_REQUEST = 1


class EnrichmentService:
    def __init__(
        self,
        hubspot: HubSpotService,
        google_places: GooglePlacesService,
        tripadvisor: TripAdvisorService | None = None,
        website_scraper: WebsiteScraperService | None = None,
        instagram: "InstagramService | None" = None,
        perplexity: PerplexityService | None = None,
        tavily: "TavilyService | None" = None,
        overwrite: bool = False,
    ):
        self._hubspot = hubspot
        self._google = google_places
        self._tripadvisor = tripadvisor
        self._website_scraper = website_scraper
        self._instagram = instagram
        self._perplexity = perplexity
        self._tavily = tavily
        self._overwrite = overwrite

    async def resolve_next_company_id(self) -> str | None:
        """Search for the next company to enrich; return its ID or None."""
        companies = await self._hubspot.search_companies()
        if companies:
            return companies[0].id
        return None

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
                    await self._hubspot.update_company(company.id, {"agente": ""})
                except Exception:
                    logger.exception("Failed to clear agente for company %s", company.id)
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
                    await self._hubspot.update_company(company.id, {"agente": ""})
                except Exception:
                    logger.exception("Failed to clear agente for company %s", company.id)
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

        # Mark as "pendiente" immediately so it won't be picked up again
        try:
            await self._hubspot.update_company(company.id, {"agente": "pendiente"})
        except Exception:
            logger.warning("Failed to set agente=pendiente for company %s", company.id)

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

        # --- Determine website URL ---
        website_url = None
        if place and place.websiteUri:
            website_url = place.websiteUri
        elif props.website and props.website.strip():
            website_url = props.website.strip()

        # --- Instagram scraping (if URL is Instagram) ---
        from app.services.instagram import is_instagram_url

        instagram_data: InstagramData | None = None
        if self._instagram and website_url and is_instagram_url(website_url):
            try:
                instagram_data = await self._instagram.scrape(
                    website_url, hotel_name=props.name, city=props.city,
                )
            except Exception:
                logger.exception(
                    "Instagram scrape failed for company %s, continuing without it",
                    company.id,
                )
            website_url = None  # Don't web-scrape instagram.com

        # --- Parallel optional enrichment: website, booking, rooms, reputation ---
        web_task = self._fetch_website_data(website_url)
        booking_task = self._fetch_booking_data(props.name, props.city, props.country)
        rooms_task = self._fetch_room_count(props.name, props.city, props.country)
        reputation_task = self._fetch_reputation(props.name, props.city, props.country)

        gather_results = await asyncio.gather(
            web_task, booking_task, rooms_task, reputation_task,
            return_exceptions=True,
        )

        web_data: WebScrapedData | None = (
            gather_results[0] if not isinstance(gather_results[0], BaseException) else None
        )
        booking_data: BookingData | None = (
            gather_results[1] if not isinstance(gather_results[1], BaseException) else None
        )
        if booking_data and not booking_data.rating and not booking_data.review_count:
            booking_data = None
        rooms_str: str | None = (
            gather_results[2] if not isinstance(gather_results[2], BaseException) else None
        )
        reputation: ReputationData | None = (
            gather_results[3] if not isinstance(gather_results[3], BaseException) else None
        )

        for i, res in enumerate(gather_results):
            if isinstance(res, BaseException):
                logger.warning("Optional enrichment task %d failed: %s", i, res)

        # --- Instagram from website link or search (if not already scraped) ---
        if not instagram_data and self._instagram:
            ig_url = (web_data.instagram_url if web_data else None)
            if not ig_url and self._tavily and website_url:
                try:
                    ig_url = await self._tavily.search_instagram_url(website_url)
                except Exception:
                    logger.exception(
                        "Instagram URL search failed for company %s", company.id,
                    )
            if ig_url:
                try:
                    scraped = await self._instagram.scrape(
                        ig_url, hotel_name=props.name, city=props.city,
                    )
                    # Only keep reliable fields (profile + followers);
                    # contact data from search results is often from other profiles
                    instagram_data = InstagramData(
                        username=scraped.username,
                        profile_url=scraped.profile_url,
                        follower_count=scraped.follower_count,
                    )
                except Exception:
                    logger.exception(
                        "Instagram scrape (from website) failed for company %s",
                        company.id,
                    )

        # --- Phase 2: Scrape listing pages (Booking.com + Hoteles.com) ---
        scraped_listings: list[ScrapedListingData] = []
        if self._tavily:
            scrape_tasks: list = []
            if booking_data and booking_data.url:
                scrape_tasks.append(self._tavily.scrape_booking_page(booking_data.url))
            if props.name:
                scrape_tasks.append(
                    self._tavily.scrape_hoteles_page(props.name, props.city, props.country)
                )
            if scrape_tasks:
                scrape_results = await asyncio.gather(
                    *scrape_tasks, return_exceptions=True,
                )
                for i, res in enumerate(scrape_results):
                    if isinstance(res, BaseException):
                        logger.warning("Listing scrape task %d failed: %s", i, res)
                    elif res is not None:
                        scraped_listings.append(res)

            # Use scraped rooms as fallback if search_room_count returned nothing
            if not rooms_str:
                for listing in scraped_listings:
                    if listing.rooms is not None:
                        rooms_str = str(listing.rooms)
                        logger.info(
                            "Using room count %s from %s scrape",
                            rooms_str, listing.source,
                        )
                        break

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

            # Always write place_id, display name, city, state, plaza (no smart merge)
            if place.id:
                updates["id_hotel"] = place.id
            if place.displayName and place.displayName.text:
                updates["name"] = place.displayName.text
            if parsed.city:
                updates["city"] = parsed.city
            if parsed.state:
                updates["state"] = parsed.state
            if parsed.plaza:
                updates["plaza"] = parsed.plaza

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

        if booking_data and booking_data.url:
            updates["booking_url"] = booking_data.url

        # Room count from Tavily — always force-write rooms + market_fit
        auto_market_fit: str | None = None
        if rooms_str:
            updates["cantidad_de_habitaciones"] = rooms_str
            updates["habitaciones"] = rooms_str
            try:
                auto_market_fit = compute_market_fit(int(rooms_str))
                updates["market_fit"] = auto_market_fit
            except (ValueError, TypeError):
                pass

        # Always clear agente
        updates["agente"] = ""

        merge_info: tuple[str, str | None] | None = None  # (merged_id, merged_name)
        conflict_info: tuple[str, str | None, str] | None = None  # (other_id, other_name, place_id)

        try:
            await self._hubspot.update_company(company.id, updates)
        except HubSpotError as exc:
            conflict_id = _extract_conflicting_id(exc.message)
            if not conflict_id or "id_hotel" not in exc.message:
                raise

            logger.warning("id_hotel conflict: %s vs %s", company.id, conflict_id)
            place_id = updates.get("id_hotel", "")

            try:
                other = await self._hubspot.get_company(conflict_id)
                if _is_same_company(props, other.properties):
                    # Duplicate company — merge it
                    await self._hubspot.merge_companies(company.id, conflict_id)
                    logger.info("Merged duplicate %s into %s", conflict_id, company.id)
                    merge_info = (conflict_id, other.properties.name)
                    # Retry full update (id_hotel is now free)
                    await self._hubspot.update_company(company.id, updates)
                else:
                    # Different companies share same Google Place
                    conflict_info = (conflict_id, other.properties.name, place_id)
                    updates.pop("id_hotel", None)
                    await self._hubspot.update_company(company.id, updates)
            except Exception:
                logger.exception("Failed to resolve id_hotel conflict for %s", company.id)
                # Last resort: drop id_hotel and continue
                updates.pop("id_hotel", None)
                try:
                    await self._hubspot.update_company(company.id, updates)
                except Exception:
                    logger.exception("Failed to update company %s even without id_hotel", company.id)
                    raise

        # --- Create enrichment note (always) ---
        note_body = build_enrichment_note(
            props.name, place, ta_location, ta_photos=ta_photos,
            web_data=web_data, booking_data=booking_data,
            instagram_data=instagram_data,
            rooms_str=rooms_str, auto_market_fit=auto_market_fit,
            reputation=reputation,
            scraped_listings=scraped_listings or None,
        )
        try:
            await self._hubspot.create_note(company.id, note_body)
        except Exception:
            logger.exception(
                "Failed to create note for company %s, enrichment still succeeded",
                company.id,
            )

        # --- Create merge/conflict note (if applicable) ---
        if merge_info:
            try:
                merged_id, merged_name = merge_info
                mn = build_merge_note(props.name, merged_id, merged_name)
                await self._hubspot.create_note(company.id, mn)
            except Exception:
                logger.exception("Failed to create merge note for company %s", company.id)
        elif conflict_info:
            try:
                other_id, other_name, pid = conflict_info
                cn = build_conflict_note(props.name, other_id, other_name, pid)
                await self._hubspot.create_note(company.id, cn)
            except Exception:
                logger.exception("Failed to create conflict note for company %s", company.id)

        # --- Create contacts from phone numbers and web data (best-effort) ---
        await self._create_contacts(
            company.id, props.name, place, ta_location, web_data,
            instagram_data=instagram_data,
        )

        # --- Deduplicate contacts (best-effort) ---
        await self._deduplicate_contacts(company.id)

        # --- Create follow-up task (best-effort) ---
        await self._create_followup_task(
            company.id, props.name, props.city, props.country,
        )

        return CompanyResult(
            company_id=company.id,
            company_name=props.name,
            status="enriched",
            changes=changes,
            note=note_body,
        )

    async def _create_followup_task(
        self,
        company_id: str,
        company_name: str | None,
        city: str | None,
        country: str | None,
    ) -> None:
        """Create a HubSpot task for the next business day (best-effort)."""
        try:
            properties = {
                "hs_task_subject": build_task_subject(company_name),
                "hs_task_body": build_task_body(company_id, company_name, city, country),
                "hs_task_status": "NOT_STARTED",
                "hs_task_priority": "MEDIUM",
                "hs_timestamp": compute_task_due_date(country),
                "hs_task_type": "TODO",
            }
            await self._hubspot.create_task(company_id, properties)
        except Exception:
            logger.exception(
                "Failed to create follow-up task for company %s, enrichment still succeeded",
                company_id,
            )

    async def _create_contacts(
        self,
        company_id: str,
        company_name: str | None,
        place: GooglePlace | None,
        ta_location: TripAdvisorLocation | None,
        web_data: WebScrapedData | None = None,
        instagram_data: InstagramData | None = None,
    ) -> None:
        """Create contacts from Google, Instagram, TripAdvisor and website data (best-effort).

        Google phone also gets a contact (with web email/WhatsApp if available).
        Instagram, TripAdvisor and website contacts are created for *different* phones or emails.
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

            has_ig_data = instagram_data and (
                instagram_data.business_phone or instagram_data.bio_phones
                or instagram_data.business_email or instagram_data.bio_emails
            )
            if has_ig_data:
                need_existing = True

            if google_phone:
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

            # Track email/WhatsApp consumed by Google contact to avoid repeating
            used_email = ""
            used_whatsapp = ""

            # --- Google phone contact ---
            if google_phone and _digits(google_phone) not in existing_phones:
                contact_props: dict[str, str] = {
                    "firstname": f"Recepcion {name}",
                    "lastname": "/ Google",
                    "phone": google_phone,
                }
                # Attach first unique web email if available
                if web_data:
                    for e in (web_data.emails or []):
                        if e.lower() not in existing_emails:
                            contact_props["email"] = e
                            used_email = e
                            break
                    # Attach WhatsApp if available
                    if web_data.whatsapp:
                        contact_props["mobilephone"] = web_data.whatsapp
                        contact_props["hs_whatsapp_phone_number"] = web_data.whatsapp
                        used_whatsapp = web_data.whatsapp
                await self._hubspot.create_contact(company_id, contact_props)
                logger.info(
                    "Created Google phone contact (%s) for company %s",
                    google_phone, company_id,
                )

            # --- Instagram contact ---
            if instagram_data:
                ig_phones = []
                if instagram_data.business_phone:
                    ig_phones.append(instagram_data.business_phone)
                ig_phones.extend(instagram_data.bio_phones)

                ig_emails = []
                if instagram_data.business_email:
                    ig_emails.append(instagram_data.business_email)
                ig_emails.extend(instagram_data.bio_emails)

                ig_phone = ""
                for p in ig_phones:
                    if _digits(p) not in all_known:
                        ig_phone = p
                        break

                ig_email = ""
                for e in ig_emails:
                    if e.lower() not in existing_emails and e != used_email:
                        ig_email = e
                        break

                ig_whatsapp = instagram_data.whatsapp or ""
                if ig_whatsapp and ig_whatsapp == used_whatsapp:
                    ig_whatsapp = ""

                if ig_phone or ig_email:
                    contact_props = {
                        "firstname": f"Recepcion {name}",
                        "lastname": "/ Instagram",
                    }
                    if ig_phone:
                        contact_props["phone"] = ig_phone
                    if ig_email:
                        contact_props["email"] = ig_email
                    if ig_whatsapp:
                        contact_props["mobilephone"] = ig_whatsapp
                        contact_props["hs_whatsapp_phone_number"] = ig_whatsapp
                    await self._hubspot.create_contact(company_id, contact_props)
                    if ig_phone:
                        all_known.add(_digits(ig_phone))
                    if ig_email:
                        used_email = ig_email
                    if ig_whatsapp:
                        used_whatsapp = ig_whatsapp
                    logger.info(
                        "Created Instagram contact for company %s", company_id,
                    )

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
                # Find first unique web phone (normalized)
                web_phone = ""
                for p in (web_data.phones or []):
                    normalized_p = _normalize_phone(p)
                    if normalized_p and _digits(normalized_p) not in all_known:
                        web_phone = normalized_p
                        break

                # WhatsApp: skip if already used by Google contact
                web_whatsapp = web_data.whatsapp or ""
                if web_whatsapp and web_whatsapp == used_whatsapp:
                    web_whatsapp = ""

                # Find first unique web email (skip if already used by Google contact)
                web_email = ""
                for e in (web_data.emails or []):
                    if e.lower() not in existing_emails and e != used_email:
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

    async def _deduplicate_contacts(self, company_id: str) -> None:
        """Remove duplicate contacts for a company (best-effort, never blocks enrichment)."""
        try:
            contacts = await self._hubspot.get_associated_contacts(company_id)
            if len(contacts) < 2:
                return

            groups = _dedup_contacts(contacts)
            if not groups:
                return

            logger.info(
                "Deduplicating contacts for company %s: %d groups",
                company_id, len(groups),
            )

            for keeper, duplicates in groups:
                # Accumulate merge updates from all duplicates
                all_updates: dict[str, str] = {}
                for dup in duplicates:
                    for field, value in _merge_contact_fields(keeper, dup).items():
                        if field not in all_updates:
                            all_updates[field] = value

                # Update keeper with merged fields
                if all_updates:
                    try:
                        await self._hubspot.update_contact(keeper.id, all_updates)
                    except Exception:
                        logger.exception(
                            "Failed to update keeper contact %s during dedup",
                            keeper.id,
                        )

                # Delete duplicates
                for dup in duplicates:
                    try:
                        await self._hubspot.delete_contact(dup.id)
                        logger.info(
                            "Deleted duplicate contact %s (keeper: %s)",
                            dup.id, keeper.id,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to delete duplicate contact %s", dup.id,
                        )

        except Exception:
            logger.exception(
                "Contact dedup failed for company %s, enrichment still succeeded",
                company_id,
            )

    async def _fetch_website_data(self, url: str | None) -> WebScrapedData | None:
        """Fetch website data: prefer Tavily, fall back to WebsiteScraperService."""
        if not url:
            return None
        if self._tavily:
            return await self._tavily.extract_website(url)
        if self._website_scraper:
            return await self._website_scraper.scrape(url)
        return None

    async def _fetch_booking_data(
        self, name: str | None, city: str | None, country: str | None,
    ) -> BookingData | None:
        """Fetch Booking.com data: prefer Tavily, fall back to Perplexity."""
        if self._tavily:
            return await self._tavily.search_booking_data(
                hotel_name=name or "", city=city, country=country,
            )
        if self._perplexity:
            return await self._perplexity.search_booking_data(
                hotel_name=name or "", city=city, country=country,
            )
        return None

    async def _fetch_room_count(
        self, name: str | None, city: str | None, country: str | None,
    ) -> str | None:
        """Fetch room count via Tavily search."""
        if self._tavily:
            return await self._tavily.search_room_count(
                hotel_name=name or "", city=city, country=country,
            )
        return None

    async def _fetch_reputation(
        self, name: str | None, city: str | None, country: str | None,
    ) -> "ReputationData | None":
        """Fetch multi-platform reputation via Tavily search."""
        if self._tavily:
            return await self._tavily.search_reputation(
                hotel_name=name or "", city=city, country=country,
            )
        return None
