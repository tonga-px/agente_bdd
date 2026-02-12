from app.mappers.address_mapper import parse_address_components
from app.schemas.google_places import AddressComponent


def _comp(long: str, short: str, types: list[str]) -> AddressComponent:
    return AddressComponent(longText=long, shortText=short, types=types)


def test_parse_full_address():
    components = [
        _comp("123", "123", ["street_number"]),
        _comp("Main Street", "Main St", ["route"]),
        _comp("Springfield", "Springfield", ["locality"]),
        _comp("Illinois", "IL", ["administrative_area_level_1"]),
        _comp("62704", "62704", ["postal_code"]),
        _comp("United States", "US", ["country"]),
    ]
    parsed = parse_address_components(components)

    assert parsed.address == "Main Street 123"
    assert parsed.city == "Springfield"
    assert parsed.state == "Illinois"
    assert parsed.zip == "62704"
    assert parsed.country == "United States"


def test_parse_missing_street_number():
    components = [
        _comp("Av. Providencia", "Av. Providencia", ["route"]),
        _comp("Santiago", "Santiago", ["locality"]),
        _comp("Chile", "CL", ["country"]),
    ]
    parsed = parse_address_components(components)

    assert parsed.address == "Av. Providencia"
    assert parsed.city == "Santiago"
    assert parsed.state is None
    assert parsed.zip is None
    assert parsed.country == "Chile"


def test_parse_empty_components():
    parsed = parse_address_components([])

    assert parsed.address is None
    assert parsed.city is None
    assert parsed.state is None
    assert parsed.zip is None
    assert parsed.country is None


def test_sublocality_fallback():
    components = [
        _comp("Palermo", "Palermo", ["sublocality"]),
    ]
    parsed = parse_address_components(components)

    assert parsed.city == "Palermo"
