from app.schemas.google_places import GooglePlace
from app.schemas.hubspot import HubSpotCompanyProperties
from app.schemas.enrichment import ParsedAddress
from app.schemas.responses import FieldChange


def _is_empty(value: str | None) -> bool:
    return value is None or value.strip() == ""


def merge_fields(
    current: HubSpotCompanyProperties,
    place: GooglePlace,
    parsed: ParsedAddress,
    overwrite: bool = False,
) -> tuple[dict[str, str], list[FieldChange]]:
    """Merge Google Places data into HubSpot fields.

    Returns (properties_to_update, list_of_changes).
    """
    candidates: dict[str, tuple[str | None, str | None]] = {
        "address": (current.address, parsed.address),
        "city": (current.city, parsed.city),
        "state": (current.state, parsed.state),
        "zip": (current.zip, parsed.zip),
        "country": (current.country, parsed.country),
        "phone": (
            current.phone,
            place.internationalPhoneNumber or place.nationalPhoneNumber,
        ),
        "website": (current.website, place.websiteUri),
        "plaza": (current.plaza, parsed.plaza),
    }

    updates: dict[str, str] = {}
    changes: list[FieldChange] = []

    for field, (old, new) in candidates.items():
        if new is None or _is_empty(new):
            continue
        if not overwrite and not _is_empty(old):
            continue
        if old == new:
            continue

        updates[field] = new
        changes.append(FieldChange(field=field, old_value=old, new_value=new))

    return updates, changes
