import logging
import re

from ..xmlutil import xp
from ..constraints import FIRST_LINE_MM, mm_to_twips
from ..messages import number as format_number
from ..model import Diagnostic

LOG = logging.getLogger("formal_check")
APP_ALPHABET = "АБВГДЕЖИКЛМНПРСТУФХЦШЩЭЮЯ"


def ordinary(model):
    return [p for p in model.body if p.region in ("main", "appendix", "abstract") and
            p.kind == "text" and p.text.strip() and not p.uncertain]


def prose(model):
    return [p for p in model.paragraphs if p.region in ("main", "appendix") and
            p.kind in ("text", "list", "note", "explanation") and not p.uncertain]


def visible_runs(p):
    return [r for r in p.runs if r.text.strip() and not r.props.get("hidden")]


def indent(p):
    if "firstLineChars" in p.props or "hangingChars" in p.props:
        return None
    return -p.props["hanging"] if "hanging" in p.props else p.props.get("firstLine", 0)


def good_indent(value):
    return value is not None and any(abs(value - mm * 1440 / 25.4) <= 1 for mm in FIRST_LINE_MM)


def check_indent(p, report):
    actual = indent(p)
    if actual is None:
        LOG.info("%s: отступ задан в знаках; перевод в миллиметры требует раскладки.", report.rule)
    elif not good_indent(actual):
        fact = (f"Отступ первой строки — {format_number(actual * 25.4 / 1440)} мм."
                if actual >= 0 else
                f"Вместо отступа задан выступ первой строки на {format_number(-actual * 25.4 / 1440)} мм.")
        report.add(p, fact,
                   "В настройках абзаца установите отступ первой строки 12,5 или 12,7 мм "
                   "(1,25 или 1,27 см). Пробелы и табуляция не заменяют эту настройку.",
                   prop="first_line_indent",
                   diagnostic=(Diagnostic("paragraph_first_line_indent", p, "first_line_indent",
                                          actual, mm_to_twips(FIRST_LINE_MM[0]))
                               if report.rule in ("F04", "F05") else None))


def cap_errors(p, title=None):
    text = p.display_text.strip() or p.text.strip()
    if title is not None:
        text = re.sub(r"^\s*\d+(?:\.\d+)*\.?\s+", "", text)
    return bool(any(c.isalpha() for c in text) and text != text.upper())


def explicit_page_break(model, p):
    if p.props.get("page_break_before") or xp(p.xml, './w:r/w:br[@w:type="page"]'):
        return True
    previous = [x for x in model.body if x.index < p.index]
    for prev in reversed(previous):
        if xp(prev.xml, './/w:br[@w:type="page"] | ./w:pPr/w:sectPr'):
            return True
        if prev.text.strip():
            break
    return False


def has_gap(model, p, after=True):
    others = [x for x in model.body if (x.index > p.index if after else x.index < p.index)]
    if not others:
        return None
    adjacent = others[0] if after else others[-1]
    if not adjacent.text.strip() and not adjacent.images and not adjacent.math:
        return True
    key = "after" if after else "before"
    other_key = "before" if after else "after"
    if p.props.get(key + "Lines", 0) >= 100 or adjacent.props.get(other_key + "Lines", 0) >= 100:
        return True
    if p.props.get(key, 0) > 0 or adjacent.props.get(other_key, 0) > 0:
        return None  # A positive spacing may or may not equal a full blank line.
    return False


def size_range(p):
    return sorted({r.props["size"] for r in visible_runs(p) if "size" in r.props})


def numbering_check(items, report, label, allow_continuations=False, gap_status="error"):
    """Items: (paragraph, number string, continuation bool). Supports two schemes."""
    seen, counters, modes = {}, {}, {}
    plural = {"формулы": "формул", "рисунка": "рисунков", "таблицы": "таблиц"}[label]
    for p, number, continuation in items:
        if continuation and allow_continuations:
            if (p.appendix, number) not in seen:
                report.add(p, f"Для продолжения {label} {number} не найдена первая часть.",
                           "Проверьте, есть ли перед продолжением первая часть с тем же номером.", "review")
            continue
        key = (p.appendix, number)
        if key in seen:
            report.add([seen[key], p], f"Номер {label} {number} указан повторно.",
                       f"Исправьте повторяющиеся номера {plural}: у каждого элемента должен быть свой номер.")
            continue
        seen[key] = p
        parts = number.split(".")
        if p.appendix:
            if len(parts) != 2 or parts[0] != p.appendix or not parts[1].isdigit():
                report.add(p, f"Номер {label} — {number}.",
                           f"В приложении {p.appendix} указывайте букву приложения перед номером, например {p.appendix}.1.")
                continue
        elif not all(s.isdigit() for s in parts) or len(parts) not in (1, 2):
            report.add(p, f"Номер {label} «{number}» записан в неподходящем формате.",
                       "Используйте арабские цифры: например, 1 при сквозной нумерации или 2.1 при нумерации по разделам.")
            continue
        scope = tuple(parts[:-1])
        counter_key = (p.appendix, scope)
        n = int(parts[-1])
        expected = counters.get(counter_key, 0) + 1
        if n != expected:
            report.add(p, f"После предыдущих элементов указан номер {number}.",
                       (f"Проверьте, нет ли между формулами пропущенного номера. "
                        f"Если других формул нет, следующим должен быть номер {'.'.join(parts[:-1] + [str(expected)])}."
                        if gap_status == "review" else
                        f"Исправьте номер: следующим по порядку должен быть {'.'.join(parts[:-1] + [str(expected)])}."),
                       gap_status)
        counters[counter_key] = n
        modes.setdefault(p.appendix, set()).add(len(parts))
    for app, mode in modes.items():
        if len(mode) > 1:
            targets = [p for p, _, c in items if p.appendix == app and not c]
            report.add(targets, f"Одновременно используются сквозная нумерация {plural} и нумерация по разделам.",
                       f"Проверьте выбранную систему и приведите номера {plural} к одному варианту.", "review")
