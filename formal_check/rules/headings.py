"""Shared heading checks from the rectorate clarification supplied on 21.10.2026."""
import re

from .common import indent
from ..constraints import BLANK_LINE_SPACING
from ..formatting import blank_line_spacing_values, paragraph_edge_spacing_values
from ..messages import alignment, number
from ..messages import spacing as describe_spacing
from ..model import Diagnostic
from ..xmlutil import xp

SPECIAL_TITLES = {"toc_heading", "abstract_heading", "bibliography_heading", "appendices_heading",
                  "appendix_heading", "appendix_title", "appendix_meta"}


def titles(model):
    return [p for p in model.body if p.kind in SPECIAL_TITLES or
            (p.kind == "heading" and p.heading_level in (1, 2))]


def unnumbered(p):
    return p.kind in SPECIAL_TITLES or (p.kind == "heading" and p.heading_level == 1
                                       and not p.heading_number)


def title_runs(p):
    """Status rows of a combined appendix heading need not be bold."""
    omitted = set()
    if p.kind in ("appendix_heading", "appendix_meta"):
        omitted = {i for i, line in enumerate(p.text.splitlines())
                   if re.fullmatch(r"\s*\([^()]+\)\s*", line)}
    line = 0
    selected = []
    for r in p.runs:
        if r.props.get("hidden"):
            continue
        parts = r.text.split("\n")
        if any(part.strip() and line + i not in omitted for i, part in enumerate(parts)):
            selected.append(r)
        line += len(parts) - 1
    return selected


def physical_alignment(p):
    value = p.props["alignment"]
    if value in ("start", "end"):
        return "left" if (value == "start") != p.props.get("bidi", False) else "right"
    return value


def check_alignment(p, report, target, subject, prop):
    value = physical_alignment(p)
    if value != target:
        label = {"left": "По левому краю", "both": "По ширине", "center": "По центру"}[target]
        report.add(p, f"Выравнивание {subject} — {alignment(value)}.",
                   f"В Word откройте настройки абзаца и выберите выравнивание «{label}».", prop=prop,
                   diagnostic=(Diagnostic("paragraph_alignment", p, prop, value, target)
                               if report.rule == "F06" else None))


def check_zero_indent(p, report):
    # A centered title can still be shifted by a whole-paragraph side indent.
    # Check those separately from firstLine/hanging so Word's controls map to
    # distinct, actionable findings.
    for side, alias, chars, label in (
            ("left", "start", ("leftChars", "startChars"), "слева"),
            ("right", "end", ("rightChars", "endChars"), "справа")):
        prop = f"heading_{side}_indent"
        if any(key in p.props for key in chars):
            report.add(p, f"Отступ {label} у ненумерованного заголовка задан в знаках; "
                       "его размер в сантиметрах не определён.",
                       f"Проверьте настройки абзаца ненумерованного заголовка: отступ {label} должен быть 0 см.",
                       "review", prop=prop)
            continue
        size = p.props.get(alias, p.props.get(side, 0))
        if size != 0:
            cm = size * 2.54 / 1440
            display = number(cm)
            if display == "0":
                display = number(cm, precision=3)
            report.add(p, f"Отступ {label} у ненумерованного заголовка — {display} см.",
                       "В Word откройте «Абзац» → «Отступ» и установите: слева — 0 см; справа — 0 см; "
                       "особый отступ — «Нет» («отсутствует»).", prop=prop,
                       diagnostic=(Diagnostic("paragraph_side_indent", p, prop, size, 0)
                                   if report.rule == "F06" else None))
    value = indent(p)
    if value is None:
        report.add(p, "Отступ первой строки ненумерованного заголовка задан в знаках.",
                   "Проверьте настройки абзаца: у ненумерованного заголовка особый отступ "
                   "должен быть «Нет», отступ первой строки — 0 мм.",
                   "review", prop="heading_first_line_indent")
    elif value != 0:
        fact = "отступ" if value > 0 else "выступ"
        report.add(p, f"У ненумерованного заголовка задан {fact} первой строки "
                   f"{number(abs(value) * 25.4 / 1440)} мм.",
                   "В настройках абзаца ненумерованного заголовка выберите особый отступ «Нет» "
                   "(отступ первой строки — 0 мм).", prop="heading_first_line_indent",
                   diagnostic=(Diagnostic("paragraph_first_line_indent", p, "heading_first_line_indent", value, 0)
                               if report.rule == "F06" else None))


def check_heading_gap(model, p, report):
    following = [x for x in model.paragraphs if x.index > p.index]
    blanks = []
    next_p = None
    for item in following:
        if item.table is None and item.kind == "blank":
            blanks.append(item)
        else:
            next_p = item
            break
    if next_p is None:
        return
    expected = ("Между заголовком и следующим текстом оставьте одну пробельную строку. "
                "Используйте один пустой абзац либо равный ему интервал абзаца; "
                "точный размер промежутка проверьте в Word.")
    if any(xp(x.xml, './/w:br[@w:type="page"] | ./w:pPr/w:sectPr') for x in blanks):
        report.add(p, "Между заголовком и следующим блоком есть разрыв страницы или секции.",
                   expected, "review", prop="heading_gap")
        return
    spaced = p.props.get("after", 0) > 0 or next_p.props.get("before", 0) > 0
    automatic = any(x.props.get(key) in ("1", "true", "on") for x, key in
                    ((p, "afterAutospacing"), (next_p, "beforeAutospacing")))
    lines = max(p.props.get("afterLines", 0), next_p.props.get("beforeLines", 0))
    # Limit automatic normalization to one adjacent, genuinely empty paragraph.
    # Never cross a table/content control or reinterpret fields and line breaks.
    plain_blank = (len(blanks) == 1 and
                   not blanks[0].props.get("page_break_before") and
                   not any(r.text.strip() for r in blanks[0].runs) and
                   not xp(blanks[0].xml, ".//w:br | .//w:cr | .//w:fldChar | "
                          ".//w:fldSimple | .//w:instrText"))
    # A correct blank may precede a TOC content control. Accept its settings,
    # but do not automatically edit across such structural boundaries.
    if (plain_blank and not spaced and not lines and not automatic and
            blank_line_spacing_values(blanks[0].props) == BLANK_LINE_SPACING):
        return
    simple_blank = (plain_blank and blanks[0].xml.getprevious() is p.xml and
                    next_p.xml.getprevious() is blanks[0].xml)
    gap_boundaries = any(
        xp(x.xml, './/w:br[@w:type="page" or @w:type="column"] | ./w:pPr/w:sectPr')
        for x in (p, next_p)) or next_p.props.get("page_break_before")
    if simple_blank and not gap_boundaries:
        blank = blanks[0]
        actual = (paragraph_edge_spacing_values(p.props, "after"),
                  blank_line_spacing_values(blank.props),
                  paragraph_edge_spacing_values(next_p.props, "before"))
        required = ((0, 0, False), BLANK_LINE_SPACING, (0, 0, False))
        if actual == required:
            return
        blank_values = actual[1]
        details = ["После заголовка найдено пустых абзацев: 1."]
        for values, label in ((actual[0], "после заголовка"),
                              (actual[2], "перед следующим блоком")):
            if values[0]:
                details.append(f"Интервал {label} — {number(values[0] / 20)} пт.")
            if values[1]:
                details.append(f"Интервал {label} в строках — {number(values[1] / 100)}.")
            if values[2]:
                details.append(f"Включён автоматический интервал {label}.")
        details.extend(["Пустой абзац: " + describe_spacing(blank_values[0], blank_values[1]),
                        f"Интервалы пустого абзаца: до — {number(blank_values[2] / 20)} пт; "
                        f"после — {number(blank_values[3] / 20)} пт."])
        if blank_values[4] or blank_values[5]:
            details.append(f"Интервалы пустого абзаца в строках: до — {number(blank_values[4] / 100)}; "
                           f"после — {number(blank_values[5] / 100)}.")
        if blank_values[6] or blank_values[7]:
            details.append("У пустого абзаца включён автоматический интервал.")
        report.add(p, "Промежуток после заголовка задан неверными интервалами. " + " ".join(details),
                   "Между заголовком и следующим текстом оставьте одну пробельную строку: "
                   "у существующего пустого абзаца установите одинарный межстрочный интервал (1,0), "
                   "интервалы «До» и «После» — 0 пт; после заголовка и перед следующим блоком "
                   "установите 0 пт, без дополнительных интервалов в строках и автоматических интервалов.",
                   prop="heading_gap",
                   diagnostic=Diagnostic("heading_gap_spacing", blank, "heading_gap", actual, required))
        return
    if (not blanks and lines == 100 and not spaced and not automatic and
            not p.props.get("contextual_spacing") and not next_p.props.get("contextual_spacing")):
        return
    if not blanks and not lines and not spaced and not automatic:
        report.add(p, "Между заголовком и следующим текстом нет пустой строки или заданного интервала.",
                   expected, prop="heading_gap")
    else:
        fact = (f"После заголовка найдено пустых абзацев: {len(blanks)}. " if blanks else "")
        details = []
        for item, point_key, line_key, auto_key, label in (
                (p, "after", "afterLines", "afterAutospacing", "после заголовка"),
                (next_p, "before", "beforeLines", "beforeAutospacing", "перед следующим блоком")):
            if item.props.get(point_key, 0):
                details.append(f"Интервал {label} — {number(item.props[point_key] / 20)} пт.")
            if item.props.get(line_key, 0):
                details.append(f"Интервал {label} в строках — {number(item.props[line_key] / 100)}.")
            if item.props.get(auto_key) in ("1", "true", "on"):
                details.append(f"Включён автоматический интервал {label}.")
        if len(blanks) == 1:
            blank_props = blanks[0].props
            details.append("Пустой абзац: " + describe_spacing(blank_props["line"], blank_props["lineRule"]))
            details.append(f"Интервалы пустого абзаца: до — {number(blank_props.get('before', 0) / 20)} пт; "
                           f"после — {number(blank_props.get('after', 0) / 20)} пт.")
        if details:
            fact += " ".join(details) + " "
        report.add(p, fact + "Равенство промежутка одной пробельной строке нельзя подтвердить по настройкам.",
                   expected, "review", prop="heading_gap")
