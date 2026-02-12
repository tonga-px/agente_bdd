from app.schemas.enrichment import ParsedAddress
from app.schemas.google_places import AddressComponent


def _find_component(
    components: list[AddressComponent], *types: str
) -> str | None:
    for t in types:
        for comp in components:
            if t in comp.types:
                return comp.longText
    return None


def _find_short(
    components: list[AddressComponent], *types: str
) -> str | None:
    for t in types:
        for comp in components:
            if t in comp.types:
                return comp.shortText
    return None


def parse_address_components(components: list[AddressComponent]) -> ParsedAddress:
    street_number = _find_short(components, "street_number") or ""
    route = _find_component(components, "route") or ""
    address_parts = [p for p in (route, street_number) if p]
    address = " ".join(address_parts) if address_parts else None

    return ParsedAddress(
        address=address,
        city=_find_component(components, "locality", "sublocality"),
        state=_find_component(components, "administrative_area_level_1"),
        zip=_find_short(components, "postal_code"),
        country=_find_component(components, "country"),
    )
