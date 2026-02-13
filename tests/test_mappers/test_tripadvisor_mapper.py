from app.mappers.tripadvisor_mapper import map_tripadvisor_to_hubspot
from app.schemas.tripadvisor import TripAdvisorLocation


def test_full_mapping():
    loc = TripAdvisorLocation(
        location_id="123",
        name="Hotel Test",
        rating="4.5",
        num_reviews="1234",
        ranking_data={"ranking_string": "#3 of 245 hotels in Santiago"},
        price_level="$$",
        category={"name": "Hotel"},
        subcategory=[{"name": "Boutique"}],
        web_url="https://www.tripadvisor.com/Hotel_Review-123",
    )

    result = map_tripadvisor_to_hubspot(loc)

    assert result["id_tripadvisor"] == "123"
    assert result["ta_rating"] == "4.5"
    assert result["ta_reviews_count"] == "1234"
    assert result["ta_ranking"] == "#3 of 245 hotels in Santiago"
    assert result["ta_price_level"] == "$$"
    assert result["ta_category"] == "Hotel"
    assert result["ta_subcategory"] == "Boutique"
    assert result["ta_url"] == "https://www.tripadvisor.com/Hotel_Review-123"


def test_minimal_mapping():
    loc = TripAdvisorLocation(location_id="456")

    result = map_tripadvisor_to_hubspot(loc)

    assert result == {"id_tripadvisor": "456"}


def test_empty_ranking_data():
    loc = TripAdvisorLocation(
        location_id="789",
        ranking_data={"ranking_string": ""},
    )

    result = map_tripadvisor_to_hubspot(loc)

    assert result == {"id_tripadvisor": "789"}
    assert "ta_ranking" not in result


def test_multiple_subcategories():
    loc = TripAdvisorLocation(
        location_id="100",
        subcategory=[{"name": "Boutique"}, {"name": "Luxury"}],
    )

    result = map_tripadvisor_to_hubspot(loc)

    assert result["ta_subcategory"] == "Boutique, Luxury"


def test_empty_location_id():
    loc = TripAdvisorLocation(location_id="")

    result = map_tripadvisor_to_hubspot(loc)

    assert result == {}
