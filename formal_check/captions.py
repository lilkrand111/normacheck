"""Recognize caption structure without treating every figure mention as a caption."""
import re

from .xmlutil import q

NUMBER = r"(?:[А-ЯЁA-Z]\.\d+|\d+(?:\.\d+)?)"
NUMBER_TOKEN = NUMBER + r"(?!\w|\.\d)"
CAPTION = re.compile(
    r"^\s*(Рисунок|Рис\.|Таблица|Табл\.|Продолжение таблицы)\s+(" +
    NUMBER_TOKEN + r")(.*)$", re.I | re.S)
# These clause openings are evidence of narrative, even near an image/table.
# Explicit caption separators take precedence, so titles may contain these words.
NARRATIVE = re.compile(
    r"^\s*(?:созда[её]т|создают|показывает|показывают|демонстрирует|демонстрируют|"
    r"иллюстрирует|иллюстрируют|отражает|отражают|содержит|содержат|позволяет|позволяют|"
    r"свидетельствует|свидетельствуют|подтверждает|подтверждают|представляет|представляют|"
    r"является|являются|отображает|отображают|привед[её]н|приведена|представлен|представлена|"
    r"расположен|расположена|находится|относится|относятся|соответствует|имеет|имеют|"
    r"может|могут|будет|был|была|это|также)\b", re.I)


def is_caption(p, paragraphs):
    match = CAPTION.match(p.text)
    if not match:
        return False
    name, _, tail = match.groups()
    if re.match(r"\s*[-–—]", tail) or re.match(r"\s*,?\s*лист\s+\d+\b", tail, re.I):
        return True
    if NARRATIVE.match(tail):
        return False
    if name.casefold() == "продолжение таблицы":
        return True
    if re.search(r"caption|подпись|название объекта", p.props.get("style_name", ""), re.I):
        return True
    if name.casefold() in ("рисунок", "рис."):
        if p.images:
            return True
        neighbor = p.xml.getprevious()
        while neighbor is not None and neighbor.tag == q("w:p"):
            previous = paragraphs.get(neighbor)
            if previous is None:
                break
            if previous.images:
                return True
            if previous.text.strip() or previous.math:
                break
            neighbor = neighbor.getprevious()
    else:
        neighbor = p.xml.getnext()
        while neighbor is not None and neighbor.tag == q("w:p"):
            following = paragraphs.get(neighbor)
            if following is None or following.text.strip() or following.images or following.math:
                break
            neighbor = neighbor.getnext()
        if neighbor is not None and neighbor.tag == q("w:tbl"):
            return True
    # Without a separator, caption style or an adjacent object, a title and an
    # ordinary sentence cannot reliably be distinguished in the MVP.
    return False
