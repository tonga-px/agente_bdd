from unittest.mock import patch

from app.mappers.note_builder import build_enrichment_note
from app.schemas.google_places import GooglePlace
from app.schemas.tripadvisor import TripAdvisorLocation


def _mock_now():
    """Patch datetime to return a fixed time for deterministic tests."""
    from datetime import datetime, timezone
    return datetime(2026, 2, 13, 15, 30, tzinfo=timezone.utc)


def test_full_google_and_tripadvisor():
    place = GooglePlace(
        formattedAddress="Av. Belgrano 1041, Mendoza",
        nationalPhoneNumber="0261 405-1900",
        websiteUri="https://diplomatichotel.com.ar",
        rating=4.3,
        userRatingCount=1234,
        googleMapsUri="https://maps.google.com/?cid=123",
        priceLevel="PRICE_LEVEL_EXPENSIVE",
        businessStatus="OPERATIONAL",
    )
    ta = TripAdvisorLocation(
        location_id="123",
        rating="4.5",
        num_reviews="3566",
        ranking_data={"ranking_string": "#10 de 134 hoteles en Mendoza"},
        price_level="$$$",
        category={"name": "Hotel"},
        subcategory=[{"name": "Boutique"}],
        web_url="https://www.tripadvisor.com/Hotel_Review-123",
        description="Un hermoso hotel en el centro de Mendoza.",
        awards=[{"display_name": "Travellers' Choice 2024"}],
        amenities=["WiFi", "Pool", "Spa", "Restaurant", "Bar"],
        trip_types=[
            {"name": "Parejas", "value": "45"},
            {"name": "Familias", "value": "30"},
        ],
        review_rating_count={"5": 800, "4": 300, "3": 50, "2": 10, "1": 5},
        phone="+54 261 405 1900",
        email="info@diplomatic.com",
    )

    with patch("app.mappers.note_builder.datetime") as mock_dt:
        mock_dt.now.return_value = _mock_now()
        mock_dt.side_effect = lambda *a, **kw: _mock_now()
        result = build_enrichment_note("Diplomatic Hotel", place, ta)

    assert "Enrichment Summary - Diplomatic Hotel" in result
    assert "Fecha:" in result
    # Google section
    assert "Google Places" in result
    assert "4.3/5" in result
    assert "1,234 reviews" in result
    assert "Operativo" in result
    assert "Av. Belgrano 1041, Mendoza" in result
    assert "0261 405-1900" in result
    assert "diplomatichotel.com.ar" in result
    assert "Ver en Google Maps" in result
    # TripAdvisor section
    assert "TripAdvisor" in result
    assert "4.5/5" in result
    assert "3566 reviews" in result
    assert "#10 de 134 hoteles en Mendoza" in result
    assert "$$$" in result
    assert "Hotel &gt; Boutique" in result
    assert "Travellers&#x27; Choice 2024" in result
    assert "WiFi, Pool, Spa, Restaurant, Bar" in result
    assert "Parejas 45%" in result
    assert "Familias 30%" in result
    assert "800" in result
    assert "Un hermoso hotel en el centro de Mendoza." in result
    assert "+54 261 405 1900" in result
    assert "info@diplomatic.com" in result
    assert "Ver en TripAdvisor" in result


def test_google_only():
    place = GooglePlace(
        formattedAddress="Av. Belgrano 1041",
        nationalPhoneNumber="0261 405-1900",
    )

    result = build_enrichment_note("Test Hotel", place, None)

    assert "Google Places" in result
    assert "TripAdvisor" not in result
    assert "Av. Belgrano 1041" in result


def test_tripadvisor_only():
    ta = TripAdvisorLocation(
        location_id="123",
        rating="4.0",
        num_reviews="500",
        web_url="https://tripadvisor.com/Hotel-123",
    )

    result = build_enrichment_note("Test Hotel", None, ta)

    assert "Google Places" not in result
    assert "TripAdvisor" in result
    assert "4.0/5" in result


def test_no_data():
    result = build_enrichment_note("Test Hotel", None, None)

    assert "No se encontraron datos en ninguna fuente." in result


def test_empty_place_no_section():
    """A GooglePlace with all None fields should not produce a section."""
    place = GooglePlace()
    result = build_enrichment_note("Test", place, None)
    assert "Google Places" not in result


def test_empty_tripadvisor_no_section():
    """A TripAdvisorLocation with all None/empty fields should not produce a section."""
    ta = TripAdvisorLocation()
    result = build_enrichment_note("Test", None, ta)
    assert "TripAdvisor" not in result


def test_closed_temporarily():
    place = GooglePlace(businessStatus="CLOSED_TEMPORARILY")
    result = build_enrichment_note("Test", place, None)
    assert "Cerrado temporalmente" in result


def test_closed_permanently():
    place = GooglePlace(businessStatus="CLOSED_PERMANENTLY")
    result = build_enrichment_note("Test", place, None)
    assert "Cerrado permanentemente" in result


def test_price_level_icons():
    place = GooglePlace(priceLevel="PRICE_LEVEL_MODERATE")
    result = build_enrichment_note("Test", place, None)
    assert "\U0001f4b0\U0001f4b0" in result


def test_description_truncated():
    long_desc = "A" * 250
    ta = TripAdvisorLocation(
        location_id="1",
        description=long_desc,
    )
    result = build_enrichment_note("Test", None, ta)
    assert "A" * 200 + "..." in result
    assert "A" * 201 not in result


def test_amenities_limited_to_10():
    amenities = [f"Amenity{i}" for i in range(15)]
    ta = TripAdvisorLocation(location_id="1", amenities=amenities)
    result = build_enrichment_note("Test", None, ta)
    assert "Amenity9" in result
    assert "Amenity10" not in result


def test_html_escaping():
    place = GooglePlace(formattedAddress="<script>alert('xss')</script>")
    result = build_enrichment_note("<b>Evil</b>", place, None)
    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    assert "&lt;b&gt;Evil&lt;/b&gt;" in result
