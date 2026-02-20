from unittest.mock import patch

from app.mappers.note_builder import (
    build_calificar_lead_note,
    build_conflict_note,
    build_enrichment_note,
    build_error_note,
    build_merge_note,
)
from app.schemas.booking import BookingData
from app.schemas.google_places import DisplayName, GooglePlace
from app.schemas.instagram import InstagramData
from app.schemas.tavily import ReputationData, ScrapedListingData
from app.schemas.tripadvisor import TripAdvisorLocation, TripAdvisorPhoto
from app.schemas.website import WebScrapedData


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
    assert "+542614051900" in result
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
    assert "+542614051900" in result
    assert "info@diplomatic.com" in result
    assert "Ver en TripAdvisor" in result


def test_google_display_name_in_note():
    place = GooglePlace(
        displayName=DisplayName(text="Hotel Diplomatic"),
        formattedAddress="Av. Belgrano 1041",
    )
    result = build_enrichment_note("Test Hotel", place, None)
    assert "Google Places" in result
    assert "<strong>Nombre:</strong> Hotel Diplomatic" in result


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


def test_tripadvisor_with_photos():
    ta = TripAdvisorLocation(location_id="1", rating="4.0", num_reviews="100")
    photos = [
        TripAdvisorPhoto(id="1", images={"small": {"url": "https://img.ta/1.jpg"}}),
        TripAdvisorPhoto(id="2", images={"small": {"url": "https://img.ta/2.jpg"}}),
    ]
    result = build_enrichment_note("Test", None, ta, ta_photos=photos)
    assert "Fotos TripAdvisor" in result
    assert "<img" in result
    assert "https://img.ta/1.jpg" in result
    assert "https://img.ta/2.jpg" in result


def test_tripadvisor_photos_limit_10():
    photos = [
        TripAdvisorPhoto(id=str(i), images={"small": {"url": f"https://img.ta/{i}.jpg"}})
        for i in range(15)
    ]
    result = build_enrichment_note("Test", None, None, ta_photos=photos)
    assert result.count("<img") == 10
    assert "https://img.ta/9.jpg" in result
    assert "https://img.ta/10.jpg" not in result


def test_tripadvisor_no_small_url_skips_photo():
    photos = [
        TripAdvisorPhoto(id="1", images={"large": {"url": "https://img.ta/big.jpg"}}),
        TripAdvisorPhoto(id="2", images={"small": {"url": "https://img.ta/small.jpg"}}),
        TripAdvisorPhoto(id="3", images={}),
    ]
    result = build_enrichment_note("Test", None, None, ta_photos=photos)
    assert result.count("<img") == 1
    assert "https://img.ta/small.jpg" in result
    assert "https://img.ta/big.jpg" not in result


# --- Website section tests ---


def test_website_section_full():
    web = WebScrapedData(
        phones=["+541152630435", "+541199887766"],
        whatsapp="+5491123530759",
        emails=["reservas@hotel.com", "info@hotel.com"],
        source_url="https://hotel.com",
    )
    result = build_enrichment_note("Test Hotel", None, None, web_data=web)
    assert "Website" in result
    assert "+541152630435" in result
    assert "+5491123530759" in result
    assert "reservas@hotel.com" in result
    assert "info@hotel.com" in result
    assert "https://hotel.com" in result


def test_website_section_empty_data():
    """Empty WebScrapedData should not produce a Website section."""
    web = WebScrapedData(source_url="https://hotel.com")
    result = build_enrichment_note("Test", None, None, web_data=web)
    # source_url alone produces a section with "Fuente:"
    assert "Website" in result
    assert "Fuente:" in result


def test_website_section_none():
    """No web_data → no Website section."""
    result = build_enrichment_note("Test", None, None, web_data=None)
    assert "Website" not in result


def test_website_phones_limited_to_3():
    web = WebScrapedData(
        phones=[f"+{i}1111111" for i in range(5)],
        source_url="https://hotel.com",
    )
    result = build_enrichment_note("Test", None, None, web_data=web)
    assert "+01111111" in result
    assert "+21111111" in result
    assert "+31111111" not in result


# --- build_error_note tests ---


def test_error_note_contains_all_fields():
    result = build_error_note("Datos", "Hotel Test", "error", "Something went wrong")
    assert "Error - Agente Datos" in result
    assert "Fecha:" in result
    assert "error" in result
    assert "Hotel Test" in result
    assert "Something went wrong" in result


def test_error_note_escapes_html():
    result = build_error_note(
        "<script>x</script>",
        "<b>Evil</b>",
        "error",
        "<img src=x onerror=alert(1)>",
    )
    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    assert "&lt;b&gt;Evil&lt;/b&gt;" in result
    assert "&lt;img " in result


def test_error_note_none_company_name():
    result = build_error_note("Datos", None, "error", "fail")
    assert "Desconocida" in result


# --- Booking section tests ---


def test_booking_section_full():
    booking = BookingData(
        url="https://www.booking.com/hotel/ar/test.html",
        rating=8.4,
        review_count=1567,
        price_range="$$$",
        hotel_name="Hotel Test Mendoza",
    )
    result = build_enrichment_note("Test Hotel", None, None, booking_data=booking)
    assert "Booking.com" in result
    assert "8.4/10" in result
    assert "1,567 reviews" in result
    assert "$$$" in result
    assert "Hotel Test Mendoza" in result
    assert "Ver en Booking.com" in result


def test_booking_section_none():
    """No booking_data → no Booking section."""
    result = build_enrichment_note("Test", None, None, booking_data=None)
    assert "Booking.com" not in result


def test_booking_section_empty_data():
    """BookingData with no rating/name/url → no Booking section."""
    booking = BookingData()
    result = build_enrichment_note("Test", None, None, booking_data=booking)
    assert "Booking.com" not in result


def test_booking_section_rating_only():
    """BookingData with only rating → shows Booking section."""
    booking = BookingData(rating=7.5, url="https://booking.com/hotel/ar/x")
    result = build_enrichment_note("Test", None, None, booking_data=booking)
    assert "Booking.com" in result
    assert "7.5/10" in result
    assert "reviews" not in result


def test_booking_section_order():
    """Booking section appears after Website and before TripAdvisor."""
    web = WebScrapedData(phones=["+541152630435"], source_url="https://hotel.com")
    booking = BookingData(rating=8.0, url="https://booking.com/hotel/ar/x")
    ta = TripAdvisorLocation(location_id="1", rating="4.0", num_reviews="100")
    place = GooglePlace(formattedAddress="Calle 1")

    result = build_enrichment_note("Test", place, ta, web_data=web, booking_data=booking)

    website_pos = result.index("Website")
    booking_pos = result.index("Booking.com")
    ta_pos = result.index("TripAdvisor")
    google_pos = result.index("Google Places")

    assert website_pos < booking_pos < ta_pos < google_pos


# --- build_merge_note tests ---


def test_build_merge_note():
    result = build_merge_note("Hotel Sol", "99999", "Hotel Sol Viejo")
    assert "Empresa Fusionada" in result
    assert "Hotel Sol" in result
    assert "99999" in result
    assert "Hotel Sol Viejo" in result
    assert "Fecha:" in result
    assert "id_hotel" in result


def test_build_merge_note_none_names():
    result = build_merge_note(None, "99999", None)
    assert "Empresa" in result
    assert "Desconocida" in result


def test_build_merge_note_escapes_html():
    result = build_merge_note("<script>x</script>", "99999", "<b>Evil</b>")
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


# --- build_conflict_note tests ---


def test_build_conflict_note():
    result = build_conflict_note("Hotel Sol", "88888", "Hotel Luna", "ChIJ_test")
    assert "Conflicto id_hotel" in result
    assert "Hotel Sol" in result
    assert "88888" in result
    assert "Hotel Luna" in result
    assert "ChIJ_test" in result
    assert "Fecha:" in result


def test_build_conflict_note_none_names():
    result = build_conflict_note(None, "88888", None, None)
    assert "Empresa" in result
    assert "Desconocida" in result
    assert "N/A" in result


def test_build_conflict_note_escapes_html():
    result = build_conflict_note("<script>", "88888", "<b>Evil</b>", "<img>")
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


# --- Instagram section tests ---


def test_instagram_section_in_note():
    ig = InstagramData(
        username="hotelitapua",
        full_name="Hotel Itapúa",
        biography="Reservas: +595 21 123 4567",
        profile_url="https://www.instagram.com/hotelitapua/",
        follower_count=1500,
        business_email="reservas@hotel.com",
        bio_phones=["+595211234567"],
        whatsapp="+595981654321",
    )
    result = build_enrichment_note("Hotel Test", None, None, instagram_data=ig)
    assert "Instagram" in result
    assert "Hotel Itap" in result
    assert "Reservas:" in result
    assert "1,500" in result
    assert "+595211234567" in result
    assert "reservas@hotel.com" in result
    assert "+595981654321" in result
    assert "@hotelitapua" in result


def test_instagram_section_none():
    """No instagram_data → no Instagram section."""
    result = build_enrichment_note("Test", None, None, instagram_data=None)
    assert "Instagram" not in result


def test_instagram_section_empty_data():
    """InstagramData with all None → no Instagram section."""
    ig = InstagramData()
    result = build_enrichment_note("Test", None, None, instagram_data=ig)
    assert "Instagram" not in result


def test_instagram_bio_truncated():
    long_bio = "A" * 250
    ig = InstagramData(username="test", biography=long_bio)
    result = build_enrichment_note("Test", None, None, instagram_data=ig)
    assert "A" * 200 + "..." in result
    assert "A" * 201 + "..." not in result


def test_instagram_section_order():
    """Instagram section appears between Website and Booking."""
    web = WebScrapedData(phones=["+541152630435"], source_url="https://hotel.com")
    ig = InstagramData(username="test", full_name="Hotel Test",
                       profile_url="https://www.instagram.com/test/")
    booking = BookingData(rating=8.0, url="https://booking.com/hotel/ar/x")

    result = build_enrichment_note(
        "Test", None, None, web_data=web, booking_data=booking, instagram_data=ig,
    )

    website_pos = result.index("Website")
    ig_pos = result.index("Instagram")
    booking_pos = result.index("Booking.com")

    assert website_pos < ig_pos < booking_pos


def test_instagram_section_escapes_html():
    ig = InstagramData(
        username="test",
        full_name="<script>alert('xss')</script>",
        biography="<b>Evil</b>",
        profile_url="https://www.instagram.com/test/",
    )
    result = build_enrichment_note("Test", None, None, instagram_data=ig)
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


# --- Rooms section tests ---


def test_rooms_section_in_note():
    result = build_enrichment_note(
        "Test Hotel", None, None, rooms_str="22", auto_market_fit="Conejo",
    )
    assert "Habitaciones (auto)" in result
    assert "22" in result
    assert "Conejo" in result


def test_rooms_section_without_market_fit():
    result = build_enrichment_note("Test", None, None, rooms_str="10")
    assert "Habitaciones (auto)" in result
    assert "10" in result
    assert "Market Fit" not in result


def test_rooms_section_none():
    result = build_enrichment_note("Test", None, None, rooms_str=None)
    assert "Habitaciones (auto)" not in result


# --- Reputation section tests ---


def test_reputation_section_full():
    rep = ReputationData(
        google_rating=4.3,
        google_review_count=1234,
        tripadvisor_rating=4.5,
        tripadvisor_review_count=3566,
        booking_rating=8.4,
        booking_review_count=2100,
        summary="Excelente hotel con buenas opiniones.",
    )
    result = build_enrichment_note("Test Hotel", None, None, reputation=rep)
    assert "Reputacion" in result
    assert "4.3/5" in result
    assert "1,234 reviews" in result
    assert "4.5/5" in result
    assert "3,566 reviews" in result
    assert "8.4/10" in result
    assert "2,100 reviews" in result
    assert "Excelente hotel" in result


def test_reputation_section_partial():
    rep = ReputationData(google_rating=4.0)
    result = build_enrichment_note("Test", None, None, reputation=rep)
    assert "Reputacion" in result
    assert "Google" in result
    assert "TripAdvisor" not in result
    assert "Booking" not in result


def test_reputation_section_none():
    result = build_enrichment_note("Test", None, None, reputation=None)
    assert "Reputacion" not in result


def test_reputation_section_empty_data():
    rep = ReputationData()
    result = build_enrichment_note("Test", None, None, reputation=rep)
    assert "Reputacion" not in result


def test_reputation_summary_truncated():
    rep = ReputationData(google_rating=4.0, summary="A" * 400)
    result = build_enrichment_note("Test", None, None, reputation=rep)
    assert "A" * 300 + "..." in result
    assert "A" * 301 + "..." not in result


def test_reputation_section_escapes_html():
    rep = ReputationData(
        google_rating=4.0,
        summary="<script>alert('xss')</script>",
    )
    result = build_enrichment_note("Test", None, None, reputation=rep)
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_rooms_and_reputation_section_order():
    """Rooms and reputation sections appear after Google Places."""
    place = GooglePlace(formattedAddress="Lima, Peru")
    rep = ReputationData(google_rating=4.0)
    result = build_enrichment_note(
        "Test", place, None, rooms_str="15", auto_market_fit="Conejo",
        reputation=rep,
    )
    google_pos = result.index("Google Places")
    rooms_pos = result.index("Habitaciones (auto)")
    rep_pos = result.index("Reputacion")
    assert google_pos < rooms_pos < rep_pos


# --- build_calificar_lead_note tests ---


def test_calificar_lead_note_tipo_de_empresa():
    """tipo_de_empresa appears in the note."""
    result = build_calificar_lead_note(
        "Hotel Sol", "Conejo", "20", "Es un hotel mediano.",
        tipo_de_empresa="Hotel",
    )
    assert "Tipo de Empresa" in result
    assert "Hotel" in result


def test_calificar_lead_note_lifecyclestage():
    """lifecyclestage appears in the note."""
    result = build_calificar_lead_note(
        "Hotel Sol", "No es FIT", "2", "Muy pequeño.",
        lifecyclestage="subscriber",
    )
    assert "Lifecycle Stage" in result
    assert "subscriber" in result


def test_calificar_lead_note_resumen_interacciones():
    """resumen_interacciones bullets appear in the note."""
    resumen = "- Llamada el 2024-01-15\n- Email enviado el 2024-01-20"
    result = build_calificar_lead_note(
        "Hotel Sol", "Conejo", "20", "ok",
        resumen_interacciones=resumen,
    )
    assert "Historial de Interacciones" in result
    assert "Llamada el 2024-01-15" in result
    assert "Email enviado el 2024-01-20" in result
    # Should be wrapped in <li> tags
    assert "<li>Llamada el 2024-01-15</li>" in result


def test_calificar_lead_note_resumen_escapes_html():
    """HTML in resumen_interacciones is escaped."""
    resumen = "- <script>alert('xss')</script>"
    result = build_calificar_lead_note(
        "Hotel", "Conejo", "20", "ok",
        resumen_interacciones=resumen,
    )
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_calificar_lead_note_no_resumen():
    """No resumen_interacciones → no Historial section."""
    result = build_calificar_lead_note("Hotel", "Conejo", "20", "ok")
    assert "Historial de Interacciones" not in result


def test_calificar_lead_note_empty_resumen():
    """Empty resumen_interacciones → no Historial section."""
    result = build_calificar_lead_note("Hotel", "Conejo", "20", "ok", resumen_interacciones="")
    assert "Historial de Interacciones" not in result


# --- Scraped listings section tests ---


def test_scraped_listings_in_note():
    """Scraped listing data appears in enrichment note."""
    listings = [
        ScrapedListingData(
            source="Booking.com",
            url="https://www.booking.com/hotel/pe/sol.html",
            room_types=["Suite Deluxe", "Habitación Doble", "Habitación Familiar"],
            nightly_rate_usd="US$85",
            review_count=1234,
        ),
    ]
    result = build_enrichment_note("Test Hotel", None, None, scraped_listings=listings)
    assert "Datos de OTAs" in result
    assert "Booking.com" in result
    assert "Tipos (3)" in result
    assert "Suite Deluxe" in result
    assert "US$85" in result
    assert "1,234" in result


def test_scraped_listings_multiple_sources():
    """Multiple scraped sources appear."""
    listings = [
        ScrapedListingData(
            source="Booking.com",
            url="https://booking.com/hotel/test",
            room_types=["Habitación Standard"],
            review_count=500,
        ),
        ScrapedListingData(
            source="Hoteles.com",
            url="https://hoteles.com/ho123/test/",
            nightly_rate_usd="US$65",
            review_count=200,
        ),
    ]
    result = build_enrichment_note("Test", None, None, scraped_listings=listings)
    assert "Booking.com" in result
    assert "Hoteles.com" in result
    assert "Habitación Standard" in result
    assert "US$65" in result


def test_scraped_listings_none():
    """No scraped_listings → no OTAs section."""
    result = build_enrichment_note("Test", None, None, scraped_listings=None)
    assert "Datos de OTAs" not in result


def test_scraped_listings_empty_list():
    """Empty scraped_listings → no OTAs section."""
    result = build_enrichment_note("Test", None, None, scraped_listings=[])
    assert "Datos de OTAs" not in result


def test_scraped_listings_no_data_items():
    """Listing with all None fields → no OTAs section."""
    listings = [ScrapedListingData(source="Booking.com")]
    result = build_enrichment_note("Test", None, None, scraped_listings=listings)
    assert "Datos de OTAs" not in result


def test_scraped_listings_url_as_link():
    """Source name should be a link when URL is provided."""
    listings = [
        ScrapedListingData(
            source="Booking.com",
            url="https://booking.com/test",
            room_types=["Suite"],
        ),
    ]
    result = build_enrichment_note("Test", None, None, scraped_listings=listings)
    assert 'href="https://booking.com/test"' in result


def test_scraped_listings_escapes_html():
    """HTML in nightly_rate_usd is escaped."""
    listings = [
        ScrapedListingData(
            source="Test",
            nightly_rate_usd="<script>alert(1)</script>",
        ),
    ]
    result = build_enrichment_note("Test", None, None, scraped_listings=listings)
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_scraped_listings_section_order():
    """Scraped listings section appears after Reputation."""
    place = GooglePlace(formattedAddress="Lima, Peru")
    rep = ReputationData(google_rating=4.0)
    listings = [
        ScrapedListingData(source="Booking.com", room_types=["Suite"]),
    ]
    result = build_enrichment_note(
        "Test", place, None,
        reputation=rep,
        scraped_listings=listings,
    )
    rep_pos = result.index("Reputacion")
    ota_pos = result.index("Datos de OTAs")
    assert rep_pos < ota_pos
