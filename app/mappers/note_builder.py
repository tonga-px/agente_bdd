from datetime import datetime, timezone
from html import escape

from app.schemas.booking import BookingData
from app.schemas.google_places import GooglePlace
from app.schemas.instagram import InstagramData
from app.schemas.tripadvisor import TripAdvisorLocation, TripAdvisorPhoto
from app.schemas.website import WebScrapedData

_PRICE_LEVEL_MAP = {
    "PRICE_LEVEL_INEXPENSIVE": "\U0001f4b0",
    "PRICE_LEVEL_MODERATE": "\U0001f4b0\U0001f4b0",
    "PRICE_LEVEL_EXPENSIVE": "\U0001f4b0\U0001f4b0\U0001f4b0",
    "PRICE_LEVEL_VERY_EXPENSIVE": "\U0001f4b0\U0001f4b0\U0001f4b0\U0001f4b0",
}

def _to_e164(phone: str) -> str:
    """Normalize phone to E.164 for display: strip non-digits, prepend '+'."""
    digits = "".join(c for c in phone if c.isdigit())
    return f"+{digits}" if digits else ""


_BUSINESS_STATUS_MAP = {
    "OPERATIONAL": ("\u2705", "Operativo"),
    "CLOSED_TEMPORARILY": ("\u26a0\ufe0f", "Cerrado temporalmente"),
    "CLOSED_PERMANENTLY": ("\u274c", "Cerrado permanentemente"),
}


def _format_google_section(place: GooglePlace) -> str | None:
    rows: list[str] = []

    # Display name
    if place.displayName and place.displayName.text:
        rows.append(
            f"<li><strong>Nombre:</strong> {escape(place.displayName.text)}</li>"
        )

    # Rating + reviews
    if place.rating is not None:
        rating_text = f"\u2b50 {place.rating}/5"
        if place.userRatingCount is not None:
            rating_text += f" ({place.userRatingCount:,} reviews)"
        rows.append(f"<li><strong>Rating:</strong> {rating_text}</li>")

    # Business status
    if place.businessStatus:
        emoji, label = _BUSINESS_STATUS_MAP.get(
            place.businessStatus, ("", place.businessStatus)
        )
        rows.append(f"<li><strong>Estado:</strong> {emoji} {escape(label)}</li>")

    # Price level
    if place.priceLevel and place.priceLevel in _PRICE_LEVEL_MAP:
        rows.append(
            f"<li><strong>Precio:</strong> {_PRICE_LEVEL_MAP[place.priceLevel]}</li>"
        )

    # Address
    if place.formattedAddress:
        rows.append(
            f"<li><strong>Direccion:</strong> {escape(place.formattedAddress)}</li>"
        )

    # Phone
    phone = place.nationalPhoneNumber or place.internationalPhoneNumber
    if phone:
        rows.append(f"<li><strong>Telefono:</strong> {escape(_to_e164(phone))}</li>")

    # Website
    if place.websiteUri:
        url = escape(place.websiteUri)
        rows.append(f'<li><strong>Website:</strong> <a href="{url}">{url}</a></li>')

    # Google Maps link
    if place.googleMapsUri:
        maps_url = escape(place.googleMapsUri)
        rows.append(
            f'<li><strong>Google Maps:</strong> <a href="{maps_url}">Ver en Google Maps</a></li>'
        )

    if not rows:
        return None
    return f"<h3>Google Places</h3><ul>{''.join(rows)}</ul>"


def _format_tripadvisor_section(ta: TripAdvisorLocation) -> str | None:
    rows: list[str] = []

    # Rating + reviews
    if ta.rating and ta.num_reviews:
        rows.append(
            f"<li><strong>Rating:</strong> \u2b50 {escape(ta.rating)}/5 "
            f"({escape(ta.num_reviews)} reviews)</li>"
        )
    elif ta.rating:
        rows.append(f"<li><strong>Rating:</strong> \u2b50 {escape(ta.rating)}/5</li>")

    # Ranking
    if ta.ranking_data:
        ranking = ta.ranking_data.get("ranking_string", "")
        if ranking:
            rows.append(f"<li><strong>Ranking:</strong> {escape(ranking)}</li>")

    # Price level
    if ta.price_level:
        rows.append(f"<li><strong>Precio:</strong> {escape(ta.price_level)}</li>")

    # Category
    category_parts: list[str] = []
    if ta.category:
        cat_name = ta.category.get("name", "")
        if cat_name:
            category_parts.append(cat_name)
    if ta.subcategory:
        sub_names = [s.get("name", "") for s in ta.subcategory if s.get("name")]
        category_parts.extend(sub_names)
    if category_parts:
        rows.append(
            f"<li><strong>Categoria:</strong> {escape(' > '.join(category_parts))}</li>"
        )

    # Awards
    if ta.awards:
        award_names = [
            a.get("display_name", "") for a in ta.awards if a.get("display_name")
        ]
        if award_names:
            rows.append(
                f"<li><strong>Awards:</strong> \U0001f3c6 {escape(', '.join(award_names))}</li>"
            )

    # Amenities (first 10)
    if ta.amenities:
        shown = ta.amenities[:10]
        rows.append(
            f"<li><strong>Amenities:</strong> {escape(', '.join(shown))}</li>"
        )

    # Trip types
    if ta.trip_types:
        trip_parts: list[str] = []
        for tt in ta.trip_types:
            name = tt.get("name") or tt.get("localized_name", "")
            value = tt.get("value", "")
            if name and value:
                trip_parts.append(f"{name} {value}%")
        if trip_parts:
            rows.append(
                f"<li><strong>Trip Types:</strong> {escape(', '.join(trip_parts))}</li>"
            )

    # Rating breakdown
    if ta.review_rating_count:
        breakdown_parts: list[str] = []
        for stars in ("5", "4", "3", "2", "1"):
            count = ta.review_rating_count.get(stars)
            if count is not None:
                breakdown_parts.append(f"{stars}\u2b50: {count}")
        if breakdown_parts:
            rows.append(
                f"<li><strong>Reviews:</strong> {' | '.join(breakdown_parts)}</li>"
            )

    # Description (truncated to 200 chars)
    if ta.description:
        desc = ta.description
        if len(desc) > 200:
            desc = desc[:200] + "..."
        rows.append(f"<li><strong>Descripcion:</strong> {escape(desc)}</li>")

    # Phone
    if ta.phone:
        rows.append(f"<li><strong>Telefono:</strong> {escape(_to_e164(ta.phone))}</li>")

    # Email
    if ta.email:
        rows.append(f"<li><strong>Email:</strong> {escape(ta.email)}</li>")

    # URL
    if ta.web_url:
        ta_url = escape(ta.web_url)
        rows.append(
            f'<li><strong>URL:</strong> <a href="{ta_url}">Ver en TripAdvisor</a></li>'
        )

    if not rows:
        return None
    return f"<h3>TripAdvisor</h3><ul>{''.join(rows)}</ul>"


def _format_tripadvisor_photos(photos: list[TripAdvisorPhoto]) -> str | None:
    urls: list[str] = []
    for photo in photos:
        url = photo.images.get("small", {}).get("url")
        if url:
            urls.append(url)
        if len(urls) >= 10:
            break
    if not urls:
        return None
    cols = 5
    rows: list[str] = []
    for i in range(0, len(urls), cols):
        cells = "".join(
            f'<td style="padding:4px;"><img src="{escape(u)}" width="150" height="150" /></td>'
            for u in urls[i:i + cols]
        )
        rows.append(f"<tr>{cells}</tr>")
    return f'<h3>Fotos TripAdvisor</h3><table>{"".join(rows)}</table>'


def _format_website_section(web_data: WebScrapedData) -> str | None:
    rows: list[str] = []

    # Phones (max 3)
    if web_data.phones:
        phones_str = ", ".join(escape(p) for p in web_data.phones[:3])
        rows.append(f"<li><strong>Telefonos:</strong> {phones_str}</li>")

    # WhatsApp
    if web_data.whatsapp:
        rows.append(f"<li><strong>WhatsApp:</strong> {escape(web_data.whatsapp)}</li>")

    # Emails (max 3)
    if web_data.emails:
        emails_str = ", ".join(escape(e) for e in web_data.emails[:3])
        rows.append(f"<li><strong>Emails:</strong> {emails_str}</li>")

    # Source URL
    if web_data.source_url:
        url = escape(web_data.source_url)
        rows.append(f'<li><strong>Fuente:</strong> <a href="{url}">{url}</a></li>')

    if not rows:
        return None
    return f"<h3>Website</h3><ul>{''.join(rows)}</ul>"


def _format_instagram_section(instagram: InstagramData) -> str | None:
    rows: list[str] = []
    if instagram.full_name:
        rows.append(f"<li><strong>Nombre:</strong> {escape(instagram.full_name)}</li>")
    if instagram.biography:
        bio = instagram.biography[:200] + ("..." if len(instagram.biography) > 200 else "")
        rows.append(f"<li><strong>Bio:</strong> {escape(bio)}</li>")
    if instagram.follower_count is not None:
        rows.append(f"<li><strong>Seguidores:</strong> {instagram.follower_count:,}</li>")
    if instagram.bio_phones:
        phones_str = ", ".join(escape(p) for p in instagram.bio_phones[:3])
        rows.append(f"<li><strong>Telefonos:</strong> {phones_str}</li>")
    if instagram.business_email:
        rows.append(f"<li><strong>Email:</strong> {escape(instagram.business_email)}</li>")
    if instagram.whatsapp:
        rows.append(f"<li><strong>WhatsApp:</strong> {escape(instagram.whatsapp)}</li>")
    if instagram.profile_url:
        url = escape(instagram.profile_url)
        rows.append(f'<li><strong>Perfil:</strong> <a href="{url}">@{escape(instagram.username or "")}</a></li>')
    if not rows:
        return None
    return f"<h3>Instagram</h3><ul>{''.join(rows)}</ul>"


def _format_booking_section(booking: BookingData) -> str | None:
    rows: list[str] = []

    # Rating + reviews
    if booking.rating is not None:
        rating_text = f"\u2b50 {booking.rating}/10"
        if booking.review_count is not None:
            rating_text += f" ({booking.review_count:,} reviews)"
        rows.append(f"<li><strong>Rating:</strong> {rating_text}</li>")

    # Price range
    if booking.price_range:
        rows.append(
            f"<li><strong>Precio:</strong> {escape(booking.price_range)}</li>"
        )

    # Hotel name
    if booking.hotel_name:
        rows.append(
            f"<li><strong>Nombre:</strong> {escape(booking.hotel_name)}</li>"
        )

    # URL
    if booking.url:
        url = escape(booking.url)
        rows.append(
            f'<li><strong>URL:</strong> <a href="{url}">Ver en Booking.com</a></li>'
        )

    if not rows:
        return None
    return f"<h3>Booking.com</h3><ul>{''.join(rows)}</ul>"


def build_merge_note(
    company_name: str | None,
    merged_id: str,
    merged_name: str | None,
) -> str:
    """Note when a duplicate company was merged."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title = escape(company_name or "Empresa")
    return (
        f"<h2>\U0001f501 Empresa Fusionada - {title}</h2>"
        f"<p><em>Fecha: {now}</em></p>"
        f"<ul>"
        f"<li><strong>Empresa fusionada:</strong> {escape(merged_name or 'Desconocida')} (ID: {escape(merged_id)})</li>"
        f"<li><strong>Resultado:</strong> Se detectó duplicado por id_hotel. La empresa {escape(merged_id)} fue fusionada en esta empresa.</li>"
        f"</ul>"
    )


def build_conflict_note(
    company_name: str | None,
    other_id: str,
    other_name: str | None,
    place_id: str | None,
) -> str:
    """Note when id_hotel conflicts with a different company."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title = escape(company_name or "Empresa")
    return (
        f"<h2>\u26a0\ufe0f Conflicto id_hotel - {title}</h2>"
        f"<p><em>Fecha: {now}</em></p>"
        f"<ul>"
        f"<li><strong>Empresa conflictiva:</strong> {escape(other_name or 'Desconocida')} (ID: {escape(other_id)})</li>"
        f"<li><strong>Google Place ID:</strong> {escape(place_id or 'N/A')}</li>"
        f"<li><strong>Resultado:</strong> El id_hotel no se actualizó porque ya pertenece a otra empresa diferente.</li>"
        f"</ul>"
    )


def build_error_note(
    agent_name: str,
    company_name: str | None,
    status: str,
    message: str,
) -> str:
    """Build an HTML error note for a HubSpot company."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"<h2>\u26a0\ufe0f Error - Agente {escape(agent_name)}</h2>"
        f"<p><em>Fecha: {now}</em></p>"
        f"<p><strong>Estado:</strong> {escape(status)}</p>"
        f"<p><strong>Empresa:</strong> {escape(company_name or 'Desconocida')}</p>"
        f"<p><strong>Error:</strong> {escape(message)}</p>"
    )


def build_calificar_lead_note(
    company_name: str | None,
    market_fit: str | None,
    rooms: str | None,
    reasoning: str | None,
    lead_actions: list | None = None,
) -> str:
    """Build an HTML note summarizing lead qualification results."""
    from app.schemas.responses import LeadAction

    title = escape(company_name or "Empresa")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    emoji_map = {
        "No es FIT": "\u274c",
        "Hormiga": "\U0001f41c",
        "Conejo": "\U0001f430",
        "Elefante": "\U0001f418",
    }
    fit_emoji = emoji_map.get(market_fit or "", "\u2753")

    parts: list[str] = [
        f"<h2>{fit_emoji} Calificación - {title}</h2>",
        f"<p><em>Fecha: {now}</em></p>",
        "<ul>",
    ]

    if market_fit:
        parts.append(f"<li><strong>Market Fit:</strong> {escape(market_fit)}</li>")
    if rooms:
        parts.append(f"<li><strong>Habitaciones:</strong> {escape(rooms)}</li>")
    if reasoning:
        parts.append(f"<li><strong>Razonamiento:</strong> {escape(reasoning)}</li>")

    parts.append("</ul>")

    if lead_actions:
        typed_actions: list[LeadAction] = lead_actions
        parts.append("<h3>Acciones sobre Leads</h3><ul>")
        for action in typed_actions:
            name = escape(action.lead_name or action.lead_id)
            parts.append(f"<li>{name}: {escape(action.action)} — {escape(action.message or '')}</li>")
        parts.append("</ul>")

    return "".join(parts)


def build_enrichment_note(
    company_name: str | None,
    place: GooglePlace | None,
    ta_location: TripAdvisorLocation | None,
    ta_photos: list[TripAdvisorPhoto] | None = None,
    web_data: WebScrapedData | None = None,
    booking_data: BookingData | None = None,
    instagram_data: InstagramData | None = None,
) -> str:
    """Build an HTML enrichment summary for a HubSpot note."""
    title = escape(company_name or "Empresa")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts: list[str] = [
        f"<h2>Enrichment Summary - {title}</h2>",
        f"<p><em>Fecha: {now}</em></p>",
    ]

    if web_data:
        web_section = _format_website_section(web_data)
        if web_section:
            parts.append(web_section)

    if instagram_data:
        ig_section = _format_instagram_section(instagram_data)
        if ig_section:
            parts.append(ig_section)

    if booking_data:
        booking_section = _format_booking_section(booking_data)
        if booking_section:
            parts.append(booking_section)

    if ta_location:
        section = _format_tripadvisor_section(ta_location)
        if section:
            parts.append(section)

    if ta_photos:
        photos_section = _format_tripadvisor_photos(ta_photos)
        if photos_section:
            parts.append(photos_section)

    if place:
        section = _format_google_section(place)
        if section:
            parts.append(section)

    if not place and not ta_location:
        parts.append("<p>No se encontraron datos en ninguna fuente.</p>")

    return "".join(parts)
