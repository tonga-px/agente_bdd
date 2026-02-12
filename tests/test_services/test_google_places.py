from app.services.google_places import build_search_query


def test_build_query_all_parts():
    assert build_search_query("Acme Corp", "Santiago", "Chile") == "Acme Corp, Santiago, Chile"


def test_build_query_name_only():
    assert build_search_query("Acme Corp") == "Acme Corp"


def test_build_query_skips_none():
    assert build_search_query("Acme Corp", None, "Chile") == "Acme Corp, Chile"
