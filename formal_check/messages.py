"""Russian presentation of measurements and OOXML values used by rule messages."""


def number(value, precision=2):
    """Use a decimal comma, omit insignificant zeros and avoid negative zero."""
    rounded = round(value, precision)
    if rounded == 0:
        rounded = 0
    text = f"{rounded:.{precision}f}"
    if precision:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def alignment(value):
    return {
        "left": "по левому краю", "center": "по центру", "right": "по правому краю",
        "both": "по ширине", "start": "по началу строки", "end": "по концу строки",
        "distribute": "равномерно по ширине", "thaiDistribute": "равномерно по ширине",
        "numTab": "по позиции номера", "centerGroup": "по центру группы формул",
    }.get(value, "нестандартное выравнивание")


def page_number_format(value):
    return {
        "lowerRoman": "строчные римские цифры", "upperRoman": "прописные римские цифры",
        "lowerLetter": "строчные латинские буквы", "upperLetter": "прописные латинские буквы",
        "russianLower": "строчные русские буквы", "russianUpper": "прописные русские буквы",
        "decimalZero": "арабские цифры с ведущим нулём", "bullet": "маркеры",
        "none": "номера скрыты",
    }.get(value, "формат, отличный от обычных арабских цифр")


def spacing(value, mode):
    if mode == "auto":
        if value == 240:
            return "Межстрочный интервал — одинарный (1,0)."
        return f"Межстрочный интервал — {number(value / 240)}."
    name = {"exact": "Точно", "atLeast": "Минимум"}.get(mode)
    if name:
        return f"Межстрочный интервал задан в режиме «{name}»: {number(value / 20)} пт."
    return f"Межстрочный интервал задан в пунктах: {number(value / 20)} пт."
