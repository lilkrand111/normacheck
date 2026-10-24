"""Shared values for the supported checks and their property diagnostics."""

SINGLE_LINE = (240, "auto")
# line, lineRule, before/after (twips), beforeLines/afterLines (hundredths), auto flags
BLANK_LINE_SPACING = SINGLE_LINE + (0, 0, 0, 0, False, False)
FIRST_LINE_MM = (12.5, 12.7)
SECTION_INDENT_MM = (12.5, 0, 5)  # Word dialog: left, right, hanging

# property, XML child, attribute, millimetres, minimum, fact label, action label
PAGE_SETUP = (
    ("page_width", "w:pgSz", "w", 210, False, "Ширина страницы", "ширину страницы"),
    ("page_height", "w:pgSz", "h", 297, False, "Высота страницы", "высоту страницы"),
    ("left_margin", "w:pgMar", "left", 30, True, "Левое поле", "левое поле"),
    ("right_margin", "w:pgMar", "right", 15, True, "Правое поле", "правое поле"),
    ("top_margin", "w:pgMar", "top", 20, False, "Верхнее поле", "верхнее поле"),
    ("bottom_margin", "w:pgMar", "bottom", 27, False, "Нижнее поле", "нижнее поле"),
)


def mm_to_twips(value):
    return round(value * 1440 / 25.4)
