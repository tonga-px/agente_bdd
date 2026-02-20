"""Tests for compute_market_fit_with_type in market_fit.py."""

from app.mappers.market_fit import compute_market_fit, compute_market_fit_with_type


# --- compute_market_fit_with_type tests ---


def test_no_booking_always_no_fit():
    """No booking_url → always 'No es FIT' regardless of rooms."""
    assert compute_market_fit_with_type(50, "Hotel", has_booking=False) == "No es FIT"
    assert compute_market_fit_with_type(3, "Hostel", has_booking=False) == "No es FIT"
    assert compute_market_fit_with_type(None, None, has_booking=False) == "No es FIT"


def test_hostel_under_5_is_hormiga():
    """Hostel with <5 rooms + booking → Hormiga (exception)."""
    assert compute_market_fit_with_type(3, "Hostel", has_booking=True) == "Hormiga"
    assert compute_market_fit_with_type(1, "Hostel", has_booking=True) == "Hormiga"
    assert compute_market_fit_with_type(4, "Hostel", has_booking=True) == "Hormiga"


def test_bb_under_5_is_hormiga():
    """Bed and breakfasts with <5 rooms + booking → Hormiga (exception)."""
    assert compute_market_fit_with_type(4, "Bed and breakfasts", has_booking=True) == "Hormiga"
    assert compute_market_fit_with_type(2, "Bed and breakfasts", has_booking=True) == "Hormiga"


def test_hostel_no_booking_still_no_fit():
    """Hostel without booking → No es FIT (booking rule wins)."""
    assert compute_market_fit_with_type(3, "Hostel", has_booking=False) == "No es FIT"
    assert compute_market_fit_with_type(4, "Bed and breakfasts", has_booking=False) == "No es FIT"


def test_normal_ranges_preserved():
    """Standard ranges with booking and non-exception type are preserved."""
    assert compute_market_fit_with_type(3, "Hotel", has_booking=True) == "No es FIT"
    assert compute_market_fit_with_type(5, "Hotel", has_booking=True) == "Hormiga"
    assert compute_market_fit_with_type(13, "Hotel", has_booking=True) == "Hormiga"
    assert compute_market_fit_with_type(14, "Resort", has_booking=True) == "Conejo"
    assert compute_market_fit_with_type(27, "Resort", has_booking=True) == "Conejo"
    assert compute_market_fit_with_type(28, "Hotel", has_booking=True) == "Elefante"
    assert compute_market_fit_with_type(100, "Hotel", has_booking=True) == "Elefante"


def test_hostel_with_5_or_more_uses_standard_range():
    """Hostel/B&B exception only applies when <5 rooms."""
    assert compute_market_fit_with_type(5, "Hostel", has_booking=True) == "Hormiga"
    assert compute_market_fit_with_type(14, "Hostel", has_booking=True) == "Conejo"
    assert compute_market_fit_with_type(28, "Bed and breakfasts", has_booking=True) == "Elefante"


def test_none_rooms_with_booking_is_no_fit():
    """No room count with booking → No es FIT."""
    assert compute_market_fit_with_type(None, "Hotel", has_booking=True) == "No es FIT"


def test_original_compute_unchanged():
    """Original compute_market_fit function is unchanged."""
    assert compute_market_fit(1) == "No es FIT"
    assert compute_market_fit(4) == "No es FIT"
    assert compute_market_fit(5) == "Hormiga"
    assert compute_market_fit(13) == "Hormiga"
    assert compute_market_fit(14) == "Conejo"
    assert compute_market_fit(27) == "Conejo"
    assert compute_market_fit(28) == "Elefante"
    assert compute_market_fit(100) == "Elefante"
