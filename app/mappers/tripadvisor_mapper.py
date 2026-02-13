from app.schemas.tripadvisor import TripAdvisorLocation


def map_tripadvisor_to_hubspot(location: TripAdvisorLocation) -> dict[str, str]:
    """Map TripAdvisor location details to HubSpot property names."""
    updates: dict[str, str] = {}

    if location.location_id:
        updates["id_tripadvisor"] = location.location_id

    if location.rating:
        updates["ta_rating"] = location.rating

    if location.num_reviews:
        updates["ta_reviews_count"] = location.num_reviews

    if location.ranking_data:
        ranking = location.ranking_data.get("ranking_string", "")
        if ranking:
            updates["ta_ranking"] = ranking

    if location.price_level:
        updates["ta_price_level"] = location.price_level

    if location.category:
        cat_name = location.category.get("name", "")
        if cat_name:
            updates["ta_category"] = cat_name

    if location.subcategory:
        sub_names = [s.get("name", "") for s in location.subcategory if s.get("name")]
        if sub_names:
            updates["ta_subcategory"] = ", ".join(sub_names)

    if location.web_url:
        updates["ta_url"] = location.web_url

    return updates
