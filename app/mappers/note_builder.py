from app.schemas.google_places import GooglePlace
from app.schemas.tripadvisor import TripAdvisorLocation


def build_enrichment_note(
    company_name: str | None,
    place: GooglePlace | None,
    ta_location: TripAdvisorLocation | None,
) -> str:
    """Build a plain-text enrichment summary for a HubSpot note."""
    lines: list[str] = []
    title = company_name or "Empresa"
    lines.append(f"Enrichment Summary - {title}")
    lines.append("")

    if place:
        lines.append("Google Places:")
        if place.formattedAddress:
            lines.append(f"- Direccion: {place.formattedAddress}")
        phone = place.nationalPhoneNumber or place.internationalPhoneNumber
        if phone:
            lines.append(f"- Telefono: {phone}")
        if place.websiteUri:
            lines.append(f"- Website: {place.websiteUri}")
        if not any(
            v
            for v in [
                place.formattedAddress,
                place.nationalPhoneNumber,
                place.internationalPhoneNumber,
                place.websiteUri,
            ]
        ):
            lines.append("- Sin datos encontrados")
        lines.append("")

    if ta_location:
        lines.append("TripAdvisor:")
        if ta_location.rating and ta_location.num_reviews:
            lines.append(
                f"- Rating: {ta_location.rating}/5 ({ta_location.num_reviews} reviews)"
            )
        elif ta_location.rating:
            lines.append(f"- Rating: {ta_location.rating}/5")
        if ta_location.ranking_data:
            ranking = ta_location.ranking_data.get("ranking_string", "")
            if ranking:
                lines.append(f"- Ranking: {ranking}")
        if ta_location.price_level:
            lines.append(f"- Price Level: {ta_location.price_level}")
        category_parts: list[str] = []
        if ta_location.category:
            cat_name = ta_location.category.get("name", "")
            if cat_name:
                category_parts.append(cat_name)
        if ta_location.subcategory:
            sub_names = [
                s.get("name", "") for s in ta_location.subcategory if s.get("name")
            ]
            category_parts.extend(sub_names)
        if category_parts:
            lines.append(f"- Categoria: {' > '.join(category_parts)}")
        if ta_location.web_url:
            lines.append(f"- URL: {ta_location.web_url}")
        lines.append("")

    if not place and not ta_location:
        lines.append("No se encontraron datos en ninguna fuente.")
        lines.append("")

    return "\n".join(lines).rstrip()
