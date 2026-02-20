_HOSTEL_BB_TYPES = {"Hostel", "Bed and breakfasts"}


def compute_market_fit(rooms: int) -> str:
    """Classify a hotel by room count into a market_fit category.

    Categories:
      - "No es FIT": < 5 rooms
      - "Hormiga":   5-13 rooms
      - "Conejo":    14-27 rooms
      - "Elefante":  28+ rooms
    """
    if rooms < 5:
        return "No es FIT"
    if rooms <= 13:
        return "Hormiga"
    if rooms <= 27:
        return "Conejo"
    return "Elefante"


def compute_market_fit_with_type(
    rooms: int | None,
    tipo_de_empresa: str | None,
    has_booking: bool,
) -> str:
    """Market fit with booking validation and Hostel/B&B exception.

    Priority rules:
      1. No booking_url → always "No es FIT"
      2. Hostel/B&B with <5 rooms → "Hormiga" (exception)
      3. Standard ranges via compute_market_fit()
    """
    if not has_booking:
        return "No es FIT"

    if rooms is not None:
        if rooms < 5 and tipo_de_empresa in _HOSTEL_BB_TYPES:
            return "Hormiga"
        return compute_market_fit(rooms)

    return "No es FIT"
