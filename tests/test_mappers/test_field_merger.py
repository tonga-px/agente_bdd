from app.mappers.field_merger import merge_fields
from app.schemas.enrichment import ParsedAddress
from app.schemas.google_places import GooglePlace
from app.schemas.hubspot import HubSpotCompanyProperties


def test_fills_empty_fields():
    current = HubSpotCompanyProperties(name="Test Corp")
    place = GooglePlace(
        nationalPhoneNumber="+56 2 1234 5678",
        websiteUri="https://test.com",
    )
    parsed = ParsedAddress(
        address="Main St 123",
        city="Santiago",
        country="Chile",
        plaza="Provincia de Santiago",
    )

    updates, changes = merge_fields(current, place, parsed, overwrite=False)

    assert updates["address"] == "Main St 123"
    assert updates["city"] == "Santiago"
    assert updates["country"] == "Chile"
    assert updates["phone"] == "+56 2 1234 5678"
    assert updates["website"] == "https://test.com"
    assert updates["plaza"] == "Provincia de Santiago"
    assert len(changes) == 6


def test_does_not_overwrite_existing():
    current = HubSpotCompanyProperties(
        name="Test Corp",
        phone="+56 9 8765 4321",
        city="Valparaíso",
    )
    place = GooglePlace(nationalPhoneNumber="+56 2 1234 5678")
    parsed = ParsedAddress(city="Santiago", country="Chile")

    updates, changes = merge_fields(current, place, parsed, overwrite=False)

    assert "phone" not in updates
    assert "city" not in updates
    assert updates["country"] == "Chile"
    assert len(changes) == 1


def test_overwrite_mode():
    current = HubSpotCompanyProperties(name="Test Corp", city="Viña del Mar")
    place = GooglePlace()
    parsed = ParsedAddress(city="Santiago")

    updates, changes = merge_fields(current, place, parsed, overwrite=True)

    assert updates["city"] == "Santiago"
    assert len(changes) == 1


def test_no_updates_when_nothing_new():
    current = HubSpotCompanyProperties(name="Test Corp")
    place = GooglePlace()
    parsed = ParsedAddress()

    updates, changes = merge_fields(current, place, parsed)

    assert updates == {}
    assert changes == []
