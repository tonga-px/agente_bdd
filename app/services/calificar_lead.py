import asyncio
import logging
import re
from datetime import datetime, timezone

from app.mappers.market_fit import compute_market_fit, compute_market_fit_with_type
from app.mappers.note_builder import build_calificar_lead_note, build_error_note
from app.schemas.hubspot import HubSpotCompany, HubSpotContact, HubSpotEmail, HubSpotLead, HubSpotNote
from app.schemas.responses import CalificarLeadResponse, LeadAction
from app.services.claude import ClaudeService
from app.services.hubspot import HubSpotService
from app.services.tavily import TavilyService

logger = logging.getLogger(__name__)

VALID_MARKET_FITS = {"No es FIT", "Hormiga", "Conejo", "Elefante"}

NO_FIT_STAGE_ID = "1178022266"

VALID_TIPO_EMPRESA = {
    "Hotel", "Apart hotel", "Hostel", "Resort", "Boutique hotel",
    "Motel", "Bed and breakfasts", "Campamento / Glamping",
    "Cadena hotelera", "Agencia de viaje", "Otro",
}

_SYSTEM_PROMPT = (
    "Eres un asistente de calificación de leads hoteleros. "
    "Analiza toda la información disponible del hotel y determina:\n"
    "1. cantidad_de_habitaciones: número estimado de habitaciones (string numérico o null si no se puede determinar)\n"
    "2. market_fit: una de estas categorías exactas: "
    '"No es FIT" (menos de 5 habitaciones o no es hotel), '
    '"Hormiga" (5-13 habitaciones), '
    '"Conejo" (14-27 habitaciones), '
    '"Elefante" (28+ habitaciones)\n'
    "3. razonamiento: breve explicación en español de por qué llegaste a esa conclusión\n"
    "4. tipo_de_empresa: una de estas opciones exactas: "
    '"Hotel", "Apart hotel", "Hostel", "Resort", "Boutique hotel", '
    '"Motel", "Bed and breakfasts", "Campamento / Glamping", '
    '"Cadena hotelera", "Agencia de viaje", "Otro"\n'
    "5. resumen_interacciones: resumen en bullets (uno por línea con guión) "
    "del historial de interacciones con el hotel (llamadas, emails, WhatsApp, notas relevantes). "
    "Si no hay interacciones significativas, devuelve null.\n\n"
    "Responde SOLO con JSON válido, sin markdown ni explicación adicional. "
    "Ejemplo: "
    '{"cantidad_de_habitaciones": "15", "market_fit": "Conejo", '
    '"razonamiento": "Según la nota de enriquecimiento, el hotel tiene 15 habitaciones.", '
    '"tipo_de_empresa": "Hotel", '
    '"resumen_interacciones": "- Se realizó llamada el 2024-01-15, contactaron al director\\n'
    '- Email de seguimiento enviado el 2024-01-20"}'
)


def _fix_encoding(text: str) -> str:
    """Fix double-encoded UTF-8 (UTF-8 bytes decoded as Latin-1)."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass

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


def _truncate(text: str, max_len: int = 500) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


_compute_market_fit = compute_market_fit  # backward-compat alias


class CalificarLeadService:
    def __init__(
        self,
        hubspot: HubSpotService,
        claude: ClaudeService,
        tavily: TavilyService | None = None,
    ):
        self._hubspot = hubspot
        self._claude = claude
        self._tavily = tavily

    async def resolve_next_company_id(self) -> str | None:
        companies = await self._hubspot.search_companies(
            agente_value="calificar_lead"
        )
        if companies:
            return companies[0].id
        return None

    async def run(self, company_id: str | None = None) -> CalificarLeadResponse:
        if company_id:
            company = await self._hubspot.get_company(company_id)
        else:
            companies = await self._hubspot.search_companies(
                agente_value="calificar_lead"
            )
            if not companies:
                return CalificarLeadResponse(
                    company_id="",
                    status="error",
                    message="No companies found with agente='calificar_lead'",
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
                note = build_error_note(
                    "Calificar Lead", company.properties.name, "error", error_msg
                )
                await self._hubspot.create_note(company.id, note)
            except Exception:
                logger.exception("Failed to create error note for company %s", company.id)
            return CalificarLeadResponse(
                company_id=company.id,
                company_name=company.properties.name,
                status="error",
                message=error_msg,
            )

    async def _process_company(
        self, company: HubSpotCompany
    ) -> CalificarLeadResponse:
        # Mark as pendiente immediately
        try:
            await self._hubspot.update_company(company.id, {"agente": "pendiente"})
        except Exception:
            logger.warning("Failed to set agente=pendiente for company %s", company.id)

        # Fetch context in parallel
        results = await asyncio.gather(
            self._hubspot.get_associated_notes(company.id),
            self._hubspot.get_associated_calls(company.id),
            self._hubspot.get_associated_emails(company.id),
            self._hubspot.get_associated_contacts(company.id),
            self._hubspot.get_associated_communications(company.id),
            return_exceptions=True,
        )

        notes: list[HubSpotNote] = results[0] if not isinstance(results[0], BaseException) else []
        calls: list[dict] = results[1] if not isinstance(results[1], BaseException) else []
        emails: list[HubSpotEmail] = results[2] if not isinstance(results[2], BaseException) else []
        contacts: list[HubSpotContact] = results[3] if not isinstance(results[3], BaseException) else []
        communications: list[dict] = results[4] if not isinstance(results[4], BaseException) else []

        for i, res in enumerate(results):
            if isinstance(res, BaseException):
                logger.warning("Context fetch %d failed: %s", i, res)

        # Filter WhatsApp communications
        whatsapp_msgs = [
            c for c in communications
            if (c.get("properties", {}).get("hs_communication_channel_type") or "").upper() == "WHATS_APP"
        ]

        # Fetch hoteles.com data if Tavily is available (best-effort)
        hoteles_data: str | None = None
        if self._tavily and company.properties.name:
            try:
                hoteles_data = await self._tavily.search_hoteles_data(
                    company.properties.name,
                    company.properties.city,
                    company.properties.country,
                )
            except Exception:
                logger.exception("Failed to fetch hoteles.com data for %s", company.id)

        # Build prompt and call Claude
        user_prompt = self._build_user_prompt(
            company, notes, calls, emails, contacts,
            whatsapp_msgs=whatsapp_msgs,
            hoteles_data=hoteles_data,
        )
        analysis = await self._claude.analyze(_SYSTEM_PROMPT, user_prompt)

        if not analysis:
            await self._hubspot.update_company(company.id, {"agente": ""})
            return CalificarLeadResponse(
                company_id=company.id,
                company_name=company.properties.name,
                status="error",
                message="Claude analysis returned no results",
            )

        # Extract and validate results (fix double-encoded UTF-8 from note context)
        rooms_str = analysis.get("cantidad_de_habitaciones")
        reasoning = analysis.get("razonamiento", "")
        if reasoning:
            reasoning = _fix_encoding(reasoning)

        # Extract new fields
        tipo_de_empresa = analysis.get("tipo_de_empresa")
        if tipo_de_empresa and tipo_de_empresa not in VALID_TIPO_EMPRESA:
            logger.warning("Invalid tipo_de_empresa from Claude: %s", tipo_de_empresa)
            tipo_de_empresa = None

        resumen_interacciones = analysis.get("resumen_interacciones")
        if resumen_interacciones:
            resumen_interacciones = _fix_encoding(resumen_interacciones)

        # Compute market_fit with booking validation and type exception
        has_booking = bool(company.properties.booking_url)
        rooms_int: int | None = None
        if rooms_str:
            try:
                rooms_int = int(rooms_str)
            except (ValueError, TypeError):
                pass

        market_fit = compute_market_fit_with_type(rooms_int, tipo_de_empresa, has_booking)

        # Validate market_fit
        if market_fit not in VALID_MARKET_FITS:
            market_fit = None

        # Compute lifecyclestage
        lifecyclestage = "subscriber" if market_fit == "No es FIT" else "lead"

        # Update company
        update_props: dict[str, str] = {"agente": ""}
        if rooms_str:
            update_props["cantidad_de_habitaciones"] = rooms_str
            update_props["habitaciones"] = rooms_str
        if market_fit:
            update_props["market_fit"] = market_fit
        if tipo_de_empresa:
            update_props["tipo_de_empresa"] = tipo_de_empresa
        update_props["lifecyclestage"] = lifecyclestage

        await self._hubspot.update_company(company.id, update_props)

        # Handle No es FIT leads
        lead_actions: list[LeadAction] = []
        if market_fit == "No es FIT":
            lead_actions = await self._handle_no_fit_leads(company)

        # Create note (best-effort)
        note_body: str | None = None
        try:
            note_body = build_calificar_lead_note(
                company.properties.name, market_fit, rooms_str, reasoning, lead_actions,
                tipo_de_empresa=tipo_de_empresa,
                resumen_interacciones=resumen_interacciones,
                lifecyclestage=lifecyclestage,
            )
            await self._hubspot.create_note(company.id, note_body)
        except Exception:
            logger.exception("Failed to create note for company %s", company.id)

        return CalificarLeadResponse(
            company_id=company.id,
            company_name=company.properties.name,
            status="completed",
            market_fit=market_fit,
            rooms=rooms_str,
            reasoning=reasoning,
            tipo_de_empresa=tipo_de_empresa,
            resumen_interacciones=resumen_interacciones,
            lifecyclestage=lifecyclestage,
            lead_actions=lead_actions,
            note=note_body,
        )

    async def _handle_no_fit_leads(
        self, company: HubSpotCompany
    ) -> list[LeadAction]:
        actions: list[LeadAction] = []
        try:
            leads = await self._hubspot.get_associated_leads(company.id)
        except Exception:
            logger.exception("Failed to fetch leads for company %s", company.id)
            return actions

        if not leads:
            return actions

        hotel_name = company.properties.name or "Hotel"

        for lead in leads:
            try:
                # Update pipeline stage
                await self._hubspot.update_lead(
                    lead.id, {"hs_pipeline_stage": NO_FIT_STAGE_ID}
                )
                action = LeadAction(
                    lead_id=lead.id,
                    lead_name=lead.properties.hs_lead_name,
                    action="stage_updated",
                    message=f"Pipeline stage updated to {NO_FIT_STAGE_ID}",
                )
                actions.append(action)

                # Create verification task if lead has an owner
                if lead.properties.hubspot_owner_id:
                    try:
                        task_props = {
                            "hs_task_subject": f"\U0001f50e Verificar {hotel_name}",
                            "hs_task_body": (
                                f"El agente calificó a {hotel_name} como 'No es FIT'. "
                                "Verificar si la clasificación es correcta."
                            ),
                            "hs_task_status": "NOT_STARTED",
                            "hs_task_priority": "MEDIUM",
                            "hs_timestamp": datetime.now(timezone.utc).isoformat(),
                            "hs_task_type": "TODO",
                            "hubspot_owner_id": lead.properties.hubspot_owner_id,
                        }
                        await self._hubspot.create_task(company.id, task_props)
                        actions.append(LeadAction(
                            lead_id=lead.id,
                            lead_name=lead.properties.hs_lead_name,
                            action="task_created",
                            message=f"Verification task created for owner {lead.properties.hubspot_owner_id}",
                        ))
                    except Exception:
                        logger.exception(
                            "Failed to create verification task for lead %s", lead.id
                        )
            except Exception:
                logger.exception("Failed to process lead %s", lead.id)
                actions.append(LeadAction(
                    lead_id=lead.id,
                    lead_name=lead.properties.hs_lead_name,
                    action="error",
                    message="Failed to update lead",
                ))

        return actions

    def _build_user_prompt(
        self,
        company: HubSpotCompany,
        notes: list[HubSpotNote],
        calls: list[dict],
        emails: list[HubSpotEmail],
        contacts: list[HubSpotContact],
        whatsapp_msgs: list[dict] | None = None,
        hoteles_data: str | None = None,
    ) -> str:
        props = company.properties
        parts: list[str] = []

        parts.append("## Datos del Hotel")
        parts.append(f"- Nombre: {props.name or 'N/A'}")
        parts.append(f"- Ciudad: {props.city or 'N/A'}")
        parts.append(f"- País: {props.country or 'N/A'}")
        parts.append(f"- Estado/Provincia: {props.state or 'N/A'}")
        parts.append(f"- Website: {props.website or 'N/A'}")
        parts.append(f"- Teléfono: {props.phone or 'N/A'}")
        parts.append(f"- Booking URL: {props.booking_url or 'N/A'}")
        if props.tipo_de_empresa:
            parts.append(f"- Tipo de Empresa (dato existente): {props.tipo_de_empresa}")
        if props.cantidad_de_habitaciones:
            parts.append(f"- Habitaciones (dato existente): {props.cantidad_de_habitaciones}")
        if props.market_fit:
            parts.append(f"- Market Fit (dato existente): {props.market_fit}")

        if notes:
            parts.append("\n## Notas")
            for n in notes[:10]:
                body = n.properties.get("hs_note_body", "")
                if body:
                    clean = _fix_encoding(_truncate(_strip_html(body)))
                    ts = n.properties.get("hs_timestamp", "")
                    parts.append(f"- [{ts}] {clean}")

        if calls:
            parts.append("\n## Llamadas")
            for c in calls[:10]:
                c_props = c.get("properties", {})
                body = c_props.get("hs_call_body", "")
                direction = c_props.get("hs_call_direction", "")
                ts = c_props.get("hs_timestamp", "")
                status = c_props.get("hs_call_status", "")
                line = f"- [{ts}] {direction} ({status})"
                if body:
                    line += f": {_fix_encoding(_truncate(_strip_html(body), 300))}"
                parts.append(line)

        if emails:
            parts.append("\n## Emails")
            for e in emails[:10]:
                subject = e.properties.get("hs_email_subject", "")
                direction = e.properties.get("hs_email_direction", "")
                ts = e.properties.get("hs_timestamp", "")
                parts.append(f"- [{ts}] {direction}: {subject}")

        if whatsapp_msgs:
            parts.append("\n## WhatsApp")
            for msg in whatsapp_msgs[:15]:
                m_props = msg.get("properties", {})
                ts = m_props.get("hs_timestamp", "")
                body = m_props.get("hs_communication_body") or m_props.get("hs_body_preview") or ""
                if body:
                    clean = _fix_encoding(_truncate(_strip_html(body), 300))
                    parts.append(f"- [{ts}] {clean}")
                else:
                    parts.append(f"- [{ts}] (sin contenido)")

        if contacts:
            parts.append("\n## Contactos")
            for c in contacts[:10]:
                cp = c.properties
                name_parts = [p for p in [cp.firstname, cp.lastname] if p]
                name = " ".join(name_parts) if name_parts else "Sin nombre"
                if cp.jobtitle:
                    name += f" ({cp.jobtitle})"
                parts.append(f"- {name}")

        if hoteles_data:
            parts.append("\n## Datos de Hoteles.com")
            parts.append(_truncate(hoteles_data, 1000))

        return "\n".join(parts)
