# PyMuPDF uses RGB tuples normalized 0.0–1.0

COLORS = {
    "orange": (1.0, 0.647, 0.0),       # #FFA500
    "red": (1.0, 0.0, 0.0),            # #FF0000
    "green": (0.0, 1.0, 0.0),          # #00FF00
    "transparent": None,
}

STATUS_HIGHLIGHT_COLOR = {
    "MATCH": None,
    "NOT_FOUND": COLORS["orange"],
    "VIOLATION": COLORS["red"],
}

STATUS_INSERTION_COLOR = {
    "NOT_FOUND": COLORS["orange"],
    "VIOLATION": COLORS["green"],
}
