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
