from html import escape

from app.schemas.google_places import GooglePlace
from app.schemas.tripadvisor import TripAdvisorLocation


def build_enrichment_note(
    company_name: str | None,
    place: GooglePlace | None,
    ta_location: TripAdvisorLocation | None,
) -> str:
    """Build an HTML enrichment summary for a HubSpot note."""
    title = escape(company_name or "Empresa")
    parts: list[str] = [f"<h2>Enrichment Summary - {title}</h2>"]

    if place:
        rows: list[str] = []
        if place.formattedAddress:
            rows.append(f"<li><strong>Direccion:</strong> {escape(place.formattedAddress)}</li>")
        phone = place.nationalPhoneNumber or place.internationalPhoneNumber
        if phone:
            rows.append(f"<li><strong>Telefono:</strong> {escape(phone)}</li>")
        if place.websiteUri:
            url = escape(place.websiteUri)
            rows.append(f'<li><strong>Website:</strong> <a href="{url}">{url}</a></li>')
        if not rows:
            rows.append("<li>Sin datos encontrados</li>")
        parts.append(f"<h3>Google Places</h3><ul>{''.join(rows)}</ul>")

    if ta_location:
        rows = []
        if ta_location.rating and ta_location.num_reviews:
            rows.append(
                f"<li><strong>Rating:</strong> {escape(ta_location.rating)}/5 "
                f"({escape(ta_location.num_reviews)} reviews)</li>"
            )
        elif ta_location.rating:
            rows.append(f"<li><strong>Rating:</strong> {escape(ta_location.rating)}/5</li>")
        if ta_location.ranking_data:
            ranking = ta_location.ranking_data.get("ranking_string", "")
            if ranking:
                rows.append(f"<li><strong>Ranking:</strong> {escape(ranking)}</li>")
        if ta_location.price_level:
            rows.append(f"<li><strong>Price Level:</strong> {escape(ta_location.price_level)}</li>")
        category_parts: list[str] = []
        if ta_location.category:
            cat_name = ta_location.category.get("name", "")
            if cat_name:
                category_parts.append(cat_name)
        if ta_location.subcategory:
            sub_names = [s.get("name", "") for s in ta_location.subcategory if s.get("name")]
            category_parts.extend(sub_names)
        if category_parts:
            rows.append(f"<li><strong>Categoria:</strong> {escape(' > '.join(category_parts))}</li>")
        if ta_location.web_url:
            ta_url = escape(ta_location.web_url)
            rows.append(f'<li><strong>URL:</strong> <a href="{ta_url}">{ta_url}</a></li>')
        if not rows:
            rows.append("<li>Sin datos encontrados</li>")
        parts.append(f"<h3>TripAdvisor</h3><ul>{''.join(rows)}</ul>")

    if not place and not ta_location:
        parts.append("<p>No se encontraron datos en ninguna fuente.</p>")

    return "".join(parts)
