import asyncio
import logging
import re
from datetime import datetime, timezone

from app.mappers.address_mapper import parse_address_components
from app.mappers.call_note_builder import build_prospeccion_note
from app.mappers.note_builder import build_error_note
from app.schemas.elevenlabs import ConversationResponse
from app.schemas.hubspot import HubSpotCompany, HubSpotContact, HubSpotEmail, HubSpotNote
from app.schemas.responses import CallAttempt, ExtractedCallData, ProspeccionResponse
from app.services.elevenlabs import ElevenLabsService
from app.services.google_places import GooglePlacesService, build_search_query
from app.services.hubspot import HubSpotService

logger = logging.getLogger(__name__)

POLL_INTERVAL = 5  # seconds
POLL_TIMEOUT = 300  # seconds
TERMINAL_STATUSES = {"done", "failed"}


def _fix_encoding(text: str) -> str:
    """Fix double-encoded UTF-8 (UTF-8 bytes decoded as Latin-1).

    Handles mixed content where some characters are > U+00FF (e.g. smart
    quotes, em dashes) and can't be Latin-1 encoded.  Those are passed
    through as-is while the Latin-1-encodable segments are decoded as UTF-8.
    """
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass

    # Fallback: fix Latin-1 segments, pass through non-Latin-1 chars
    result: list[str] = []
    buf = bytearray()
    for ch in text:
        if ord(ch) <= 0xFF:
            buf.append(ord(ch))
        else:
            if buf:
                try:
                    result.append(buf.decode("utf-8"))
                except UnicodeDecodeError:
                    result.append(buf.decode("latin-1"))
                buf = bytearray()
            result.append(ch)
    if buf:
        try:
            result.append(buf.decode("utf-8"))
        except UnicodeDecodeError:
            result.append(buf.decode("latin-1"))

    return "".join(result)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _truncate(text: str, max_len: int = 200) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _split_name(full_name: str) -> tuple[str, str]:
    """Split a full name into (firstname, lastname).

    "Juan García" → ("Juan", "García")
    "Juan" → ("Juan", "")
    "María de los Angeles López" → ("María", "de los Angeles López")
    """
    parts = full_name.strip().split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    if len(parts) == 1:
        return parts[0], ""
    return "", ""


def _normalize_phone(phone: str) -> str:
    """Ensure phone number starts with '+' for E.164 format."""
    phone = phone.strip()
    if not phone.startswith("+"):
        phone = "+" + phone
    return phone


def _parse_num_rooms(raw: str) -> int | None:
    """Extract the first integer from a num_rooms string."""
    match = re.search(r"\d+", raw)
    return int(match.group()) if match else None


def _compute_market_fit(num_rooms: int) -> str:
    """Classify hotel by room count."""
    if num_rooms <= 13:
        return "Hormiga"
    if num_rooms <= 27:
        return "Conejo"
    return "Elefante"


def _describe_error(exc: Exception) -> str:
    """Return a human-readable description for common call exceptions."""
    import httpx

    if isinstance(exc, httpx.ReadTimeout):
        return "Tiempo de espera agotado (ReadTimeout)"
    if isinstance(exc, httpx.ConnectTimeout):
        return "No se pudo conectar al servidor (ConnectTimeout)"
    if isinstance(exc, httpx.ConnectError):
        return "Error de conexión"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    # Generic fallback: prefer str(exc), else class name
    text = str(exc).strip()
    if text:
        return text
    return type(exc).__name__


class ProspeccionService:
    def __init__(
        self,
        hubspot: HubSpotService,
        elevenlabs: ElevenLabsService,
        google_places: GooglePlacesService | None = None,
    ):
        self._hubspot = hubspot
        self._elevenlabs = elevenlabs
        self._google = google_places

    async def run(self, company_id: str | None = None) -> ProspeccionResponse:
        if company_id:
            company = await self._hubspot.get_company(company_id)
        else:
            companies = await self._hubspot.search_companies(
                agente_value="llamada_prospeccion"
            )
            if not companies:
                return ProspeccionResponse(
                    company_id="",
                    status="error",
                    message="No companies found with agente='llamada_prospeccion'",
                )
            company = companies[0]

        try:
            return await self._process_company(company)
        except Exception as exc:
            logger.exception("Error processing company %s", company.id)
            error_msg = str(exc)
            try:
                await self._hubspot.update_company(company.id, {"agente": ""})
            except Exception:
                logger.exception("Failed to clear agente for company %s", company.id)
            try:
                note = build_error_note("Llamada Prospeccion", company.properties.name, "error", error_msg)
                await self._hubspot.create_note(company.id, note)
            except Exception:
                logger.exception("Failed to create error note for company %s", company.id)
            return ProspeccionResponse(
                company_id=company.id,
                company_name=company.properties.name,
                status="error",
                message=error_msg,
            )

    async def _process_company(
        self, company: HubSpotCompany
    ) -> ProspeccionResponse:
        # Fetch context in parallel
        results = await asyncio.gather(
            self._hubspot.get_associated_notes(company.id),
            self._hubspot.get_associated_emails(company.id),
            self._hubspot.get_associated_contacts(company.id),
            return_exceptions=True,
        )

        notes: list[HubSpotNote] = results[0] if not isinstance(results[0], BaseException) else []
        emails: list[HubSpotEmail] = results[1] if not isinstance(results[1], BaseException) else []
        contacts: list[HubSpotContact] = results[2] if not isinstance(results[2], BaseException) else []

        for i, res in enumerate(results):
            if isinstance(res, BaseException):
                logger.warning("Context fetch %d failed: %s", i, res)

        # Build phone list
        phone_list = self._build_phone_list(company, contacts)
        if not phone_list:
            await self._hubspot.update_company(company.id, {"agente": ""})
            no_phone_msg = "No phone numbers found for company or contacts"
            try:
                note = build_error_note("Llamada Prospeccion", company.properties.name, "no_phone", no_phone_msg)
                await self._hubspot.create_note(company.id, note)
            except Exception:
                logger.exception("Failed to create error note for company %s", company.id)
            return ProspeccionResponse(
                company_id=company.id,
                company_name=company.properties.name,
                status="no_phone",
                message=no_phone_msg,
            )

        # Build dynamic variables for ElevenLabs agent
        dynamic_vars = self._build_dynamic_variables(
            company, notes, emails, contacts
        )

        # Try each phone number
        call_attempts: list[CallAttempt] = []
        successful_conversation: ConversationResponse | None = None

        for phone, source in phone_list:
            attempt = await self._try_call(phone, source, dynamic_vars)
            call_attempts.append(attempt)

            if attempt.status == "connected" and attempt.conversation_id:
                successful_conversation = await self._fetch_with_analysis(
                    attempt.conversation_id
                )
                break

        # All phones failed
        if successful_conversation is None:
            await self._hubspot.update_company(company.id, {"agente": ""})
            note_body = build_prospeccion_note(
                company.properties.name, call_attempts, None, None,
            )
            try:
                await self._hubspot.create_note(company.id, note_body)
            except Exception:
                logger.exception("Failed to create error note for company %s", company.id)
            return ProspeccionResponse(
                company_id=company.id,
                company_name=company.properties.name,
                status="all_failed",
                message="All phone numbers failed",
                call_attempts=call_attempts,
                note=note_body,
            )

        # Extract data from conversation
        extracted = self._extract_data(successful_conversation)

        # Format transcript
        transcript_text = self._format_transcript(successful_conversation)

        # Update HubSpot
        hubspot_updates = self._build_hubspot_updates(extracted, company)
        hubspot_updates["agente"] = ""

        # State lookup (best-effort, only if empty)
        if not (company.properties.state or "").strip():
            state = await self._lookup_state(company)
            if state:
                hubspot_updates["state"] = state

        await self._hubspot.update_company(company.id, hubspot_updates)

        # Build and create note
        note_body = build_prospeccion_note(
            company.properties.name, call_attempts, extracted, transcript_text
        )
        try:
            await self._hubspot.create_note(company.id, note_body)
        except Exception:
            logger.exception(
                "Failed to create note for company %s, prospeccion still succeeded",
                company.id,
            )

        # Create or update decision maker contact (best-effort)
        await self._upsert_decision_maker_contact(
            company.id, extracted, contacts
        )

        # Register call recording in HubSpot (best-effort)
        successful_attempt = next(
            (a for a in call_attempts if a.status == "connected"), None
        )
        if successful_attempt and successful_attempt.conversation_id:
            await self._register_call(
                company, successful_conversation, successful_attempt, extracted
            )

        return ProspeccionResponse(
            company_id=company.id,
            company_name=company.properties.name,
            status="completed",
            call_attempts=call_attempts,
            extracted_data=extracted,
            transcript=transcript_text,
            note=note_body,
        )

    def _build_phone_list(
        self,
        company: HubSpotCompany,
        contacts: list[HubSpotContact],
    ) -> list[tuple[str, str]]:
        seen: set[str] = set()
        phones: list[tuple[str, str]] = []

        def _add(phone_raw: str, source: str) -> None:
            phone = _normalize_phone(phone_raw)
            digits = re.sub(r"\D", "", phone)
            if digits not in seen:
                seen.add(digits)
                phones.append((phone, source))

        # Company phone first
        if company.properties.phone and company.properties.phone.strip():
            _add(company.properties.phone, "company")

        # Then contact phones
        for contact in contacts:
            props = contact.properties
            if props.phone and props.phone.strip():
                _add(props.phone, f"contact:{contact.id}:phone")
            if props.mobilephone and props.mobilephone.strip():
                _add(props.mobilephone, f"contact:{contact.id}:mobile")

        return phones

    def _build_dynamic_variables(
        self,
        company: HubSpotCompany,
        notes: list[HubSpotNote],
        emails: list[HubSpotEmail],
        contacts: list[HubSpotContact],
    ) -> dict:
        props = company.properties

        # Known contacts summary
        contact_summaries: list[str] = []
        for c in contacts[:3]:
            cp = c.properties
            name_parts = [p for p in [cp.firstname, cp.lastname] if p]
            name = " ".join(name_parts) if name_parts else "Sin nombre"
            if cp.jobtitle:
                name += f" ({cp.jobtitle})"
            contact_summaries.append(name)

        # Recent notes summary
        note_summaries: list[str] = []
        for n in notes[:3]:
            body = n.properties.get("hs_note_body", "")
            if body:
                note_summaries.append(_truncate(_strip_html(body)))

        # Recent email subjects
        email_subjects: list[str] = []
        for e in emails[:3]:
            subject = e.properties.get("hs_email_subject", "")
            if subject:
                email_subjects.append(subject)

        return {
            "hotel_name": props.name or "",
            "hotel_city": props.city or "",
            "hotel_country": props.country or "",
            "hotel_website": props.website or "",
            "hotel_address": props.address or "",
            "known_contacts": ", ".join(contact_summaries) if contact_summaries else "Ninguno",
            "recent_notes": " | ".join(note_summaries) if note_summaries else "Ninguna",
            "recent_emails": ", ".join(email_subjects) if email_subjects else "Ninguno",
        }

    async def _try_call(
        self, phone: str, source: str, dynamic_vars: dict
    ) -> CallAttempt:
        try:
            call_resp = await self._elevenlabs.start_outbound_call(
                phone, dynamic_vars
            )
            if not call_resp.conversation_id:
                return CallAttempt(
                    phone_number=phone,
                    source=source,
                    status="failed",
                    error=call_resp.message or "Call not started",
                )

            # ElevenLabs returns success=False with SIP errors but still
            # includes a conversation_id — no need to poll in that case.
            if not call_resp.success and call_resp.message:
                return CallAttempt(
                    phone_number=phone,
                    source=source,
                    conversation_id=call_resp.conversation_id,
                    status="failed",
                    error=call_resp.message,
                )

            conversation = await self._poll_conversation(
                call_resp.conversation_id
            )

            if conversation.status == "done":
                return CallAttempt(
                    phone_number=phone,
                    source=source,
                    conversation_id=call_resp.conversation_id,
                    status="connected",
                )
            else:
                return CallAttempt(
                    phone_number=phone,
                    source=source,
                    conversation_id=call_resp.conversation_id,
                    status="failed",
                    error=f"Conversation ended with status: {conversation.status}",
                )

        except Exception as exc:
            logger.warning("Call to %s failed: %s: %s", phone, type(exc).__name__, exc)
            return CallAttempt(
                phone_number=phone,
                source=source,
                status="error",
                error=_describe_error(exc),
            )

    async def _fetch_with_analysis(
        self, conversation_id: str, retries: int = 6, delay: float = 5.0
    ) -> ConversationResponse:
        """Fetch conversation, retrying until analysis data is populated."""
        for attempt in range(retries):
            if attempt > 0:
                await asyncio.sleep(delay)
            conversation = await self._elevenlabs.get_conversation(
                conversation_id
            )
            if conversation.analysis and (
                conversation.analysis.data_collection_results
                or conversation.analysis.extracted_data
            ):
                logger.info(
                    "Conversation %s analysis ready after %d attempts",
                    conversation_id,
                    attempt + 1,
                )
                return conversation
            logger.info(
                "Conversation %s analysis not ready yet (attempt %d/%d)",
                conversation_id,
                attempt + 1,
                retries,
            )
        logger.warning(
            "Conversation %s analysis not available after %d retries, proceeding without",
            conversation_id,
            retries,
        )
        return conversation

    async def _poll_conversation(
        self, conversation_id: str
    ) -> ConversationResponse:
        elapsed = 0.0
        while elapsed < POLL_TIMEOUT:
            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL

            conversation = await self._elevenlabs.get_conversation(
                conversation_id
            )
            logger.info(
                "Conversation %s status: %s (elapsed: %.0fs)",
                conversation_id,
                conversation.status,
                elapsed,
            )
            if conversation.status in TERMINAL_STATUSES:
                return conversation

        # Timeout — return last known state
        logger.warning(
            "Conversation %s timed out after %ds", conversation_id, POLL_TIMEOUT
        )
        return ConversationResponse(
            conversation_id=conversation_id, status="failed"
        )

    def _extract_data(
        self, conversation: ConversationResponse
    ) -> ExtractedCallData:
        raw: dict = {}
        if conversation.analysis:
            # data_collection_results: {"key": {"value": "...", ...}}
            dcr = conversation.analysis.data_collection_results
            if dcr:
                raw = {k: v.get("value") if isinstance(v, dict) else v for k, v in dcr.items()}
            elif conversation.analysis.extracted_data:
                raw = conversation.analysis.extracted_data

        def _get(key: str) -> str | None:
            val = raw.get(key)
            return _fix_encoding(val) if val else val

        return ExtractedCallData(
            hotel_name=_get("hotel_name"),
            num_rooms=_get("num_rooms"),
            decision_maker_name=_get("decision_maker_name"),
            decision_maker_phone=_get("decision_maker_phone"),
            decision_maker_email=_get("decision_maker_email"),
            date_and_time=_get("date_and_time"),
        )

    def _format_transcript(
        self, conversation: ConversationResponse
    ) -> str:
        lines: list[str] = []
        for entry in conversation.transcript:
            role = "Agente" if entry.role == "agent" else "Hotel"
            msg = _fix_encoding(entry.message) if entry.message else ""
            lines.append(f"{role}: {msg}")
        return "\n".join(lines)

    def _build_hubspot_updates(
        self, extracted: ExtractedCallData, company: HubSpotCompany
    ) -> dict[str, str]:
        updates: dict[str, str] = {}

        if extracted.num_rooms and not (company.properties.market_fit or "").strip():
            parsed = _parse_num_rooms(extracted.num_rooms)
            if parsed is not None:
                updates["market_fit"] = _compute_market_fit(parsed)

        return updates

    async def _lookup_state(self, company: HubSpotCompany) -> str | None:
        """Look up state via Google Places (best-effort)."""
        if self._google is None:
            return None
        try:
            props = company.properties
            place = None
            if props.id_hotel and props.id_hotel.strip():
                try:
                    place = await self._google.get_place_details(props.id_hotel.strip())
                except Exception:
                    logger.warning(
                        "Google Place ID %s failed, falling back to text search",
                        props.id_hotel,
                    )
            if place is None:
                query = build_search_query(props.name, props.city, props.country)
                place = await self._google.text_search(query)
            if place and place.addressComponents:
                parsed = parse_address_components(place.addressComponents)
                return parsed.state
        except Exception:
            logger.exception("State lookup failed for company %s", company.id)
        return None

    async def _upsert_decision_maker_contact(
        self,
        company_id: str,
        extracted: ExtractedCallData,
        contacts: list[HubSpotContact],
    ) -> None:
        """Create or update a contact for the decision maker (best-effort)."""
        try:
            has_data = extracted.decision_maker_name or extracted.decision_maker_phone or extracted.decision_maker_email
            if not has_data:
                return

            # Build properties from extracted data
            props: dict[str, str] = {}
            if extracted.decision_maker_name:
                first, last = _split_name(extracted.decision_maker_name)
                if first:
                    props["firstname"] = first
                if last:
                    props["lastname"] = last
            if extracted.decision_maker_phone:
                props["phone"] = extracted.decision_maker_phone
            if extracted.decision_maker_email:
                props["email"] = extracted.decision_maker_email

            # Check if a contact with that email already exists in the company
            existing: HubSpotContact | None = None
            if extracted.decision_maker_email:
                email_lower = extracted.decision_maker_email.lower()
                for c in contacts:
                    if c.properties.email and c.properties.email.lower() == email_lower:
                        existing = c
                        break

            if existing:
                # Update only empty fields
                update_props: dict[str, str] = {}
                for key, value in props.items():
                    existing_val = getattr(existing.properties, key, None)
                    if not existing_val:
                        update_props[key] = value
                if update_props:
                    await self._hubspot.update_contact(existing.id, update_props)
                    logger.info("Updated existing contact %s for company %s", existing.id, company_id)
                else:
                    logger.info("Contact %s already has all fields, skipping update", existing.id)
            else:
                contact_id = await self._hubspot.create_contact(company_id, props)
                logger.info("Created new contact %s for company %s", contact_id, company_id)
        except Exception:
            logger.exception(
                "Failed to upsert decision maker contact for company %s, prospeccion still succeeded",
                company_id,
            )

    async def _register_call(
        self,
        company: HubSpotCompany,
        conversation: ConversationResponse,
        attempt: CallAttempt,
        extracted: ExtractedCallData,
    ) -> None:
        try:
            # 1. Download audio from ElevenLabs
            audio_bytes = await self._elevenlabs.get_conversation_audio(
                conversation.conversation_id
            )

            # 2. Upload to HubSpot File Manager
            filename = f"call_{company.id}_{conversation.conversation_id}.mp3"
            file_url = await self._hubspot.upload_file(filename, audio_bytes)

            # 3. Build call body from extracted data
            body_parts: list[str] = []
            if extracted.hotel_name:
                body_parts.append(f"Hotel: {extracted.hotel_name}")
            if extracted.num_rooms:
                body_parts.append(f"Habitaciones: {extracted.num_rooms}")
            if extracted.decision_maker_name:
                body_parts.append(f"Contacto: {extracted.decision_maker_name}")
            if extracted.date_and_time:
                body_parts.append(f"Disponibilidad demo: {extracted.date_and_time}")
            call_body = ". ".join(body_parts) if body_parts else ""

            # 4. Compute duration in ms from conversation metadata
            duration_ms = self._get_call_duration_ms(conversation)

            # 5. Create Call object in HubSpot
            properties = {
                "hs_timestamp": datetime.now(timezone.utc).isoformat(),
                "hs_call_title": f"Llamada de Prospeccion - {company.properties.name or company.id}",
                "hs_call_body": call_body,
                "hs_call_status": "COMPLETED",
                "hs_call_direction": "OUTBOUND",
                "hs_call_to_number": attempt.phone_number,
                "hs_call_recording_url": file_url,
            }
            if duration_ms:
                properties["hs_call_duration"] = str(duration_ms)

            await self._hubspot.create_call(company.id, properties)
            logger.info(
                "Registered call for company %s (conversation %s)",
                company.id,
                conversation.conversation_id,
            )
        except Exception:
            logger.exception(
                "Failed to register call for company %s, prospeccion still succeeded",
                company.id,
            )

    @staticmethod
    def _get_call_duration_ms(conversation: ConversationResponse) -> int | None:
        if not conversation.metadata:
            return None
        start = conversation.metadata.get("start_time_unix_secs")
        end = conversation.metadata.get("end_time_unix_secs")
        if start is not None and end is not None:
            try:
                return int((float(end) - float(start)) * 1000)
            except (ValueError, TypeError):
                return None
        return None
