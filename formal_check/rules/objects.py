import re

from . import rule
from .common import (LOG, check_indent, has_gap, numbering_check, prose)
from .figure_spacing import check_figure_gap
from ..captions import NUMBER, NUMBER_TOKEN
from ..reader import paragraph_runs, visible_run_text
from ..xmlutil import attr, child, integer, on, q, val, xp
from ..messages import alignment as alignment_name

FIGURE = re.compile(r"^\s*(Рисунок|Рис\.)\s+(" + NUMBER_TOKEN + r")(.*)$", re.I | re.S)
TABLE = re.compile(r"^\s*(Таблица|Табл\.)\s+(" + NUMBER_TOKEN + r")(.*)$", re.I | re.S)
CONTINUATION = re.compile(r"^\s*Продолжение таблицы\s+(" + NUMBER_TOKEN + r")", re.I)
FIGURE_WORD = r"(?:рисунок|рисунка|рисунку|рисунком|рисунке|рисунки|рисунков|рисункам|рисунками|рисунках|рис\.)"
NUMBER_SERIES = NUMBER_TOKEN + r"(?:\s*(?:[,;–—-]|\bи\b|\bили\b)\s*" + NUMBER_TOKEN + r")*"
FIGURE_REFERENCES = (
    re.compile(r"(?<!\w)" + FIGURE_WORD + r"\s+(" + NUMBER_SERIES + r")", re.I),
    re.compile(r"(?<![\w.])(" + NUMBER_SERIES + r")\s+" + FIGURE_WORD + r"(?!\w)", re.I),
)
FORMULA_REF = re.compile(
    r"\b(?:формул[а-я]*|уравнени[а-я]*|выражени[а-я]*|соотношени[а-я]*|равенств[а-я]*)\s+"
    r"(\(?)(" + NUMBER + r")(\)?)", re.I)


def display_formulas(model):
    for p in model.paragraphs:
        if p.math and p.region in ("main", "appendix") and p.kind != "code":
            remaining = re.sub(r"\(?\s*" + NUMBER + r"\s*\)?", "", p.text).strip(" \t\n.,;")
            if xp(p.xml, ".//m:oMathPara") or not remaining or (p.kind == "formula" and formula_number(p)):
                yield p


def formula_number(p):
    match = re.search(r"\((" + NUMBER + r")\)\s*[.,;]?\s*$", p.text)
    if match:
        return match[1]
    # Word can store the right-hand number inside the editable equation.
    # Require a clear gap so an ordinary expression such as f(1) is not a label.
    math_text = "".join(xp(p.xml, ".//m:t/text()"))
    match = re.search(r"[ \t]{2,}\((" + NUMBER + r")\)\s*[.,;]?\s*$", math_text)
    return match[1] if match else None


def numbered_formulas(model):
    """Group equations in one layout-table row under a shared number."""
    seen_rows = set()
    for p in display_formulas(model):
        key = (p.table, p.row)
        if p.table is not None and key in seen_rows:
            continue
        number = formula_number(p)
        if p.table is not None:
            seen_rows.add(key)
            peers = [x for x in model.paragraphs if x.table == p.table and x.row == p.row and x is not p]
            peer_numbers = {formula_number(x) for x in peers if formula_number(x)}
            if not number and len(peer_numbers) == 1:
                number = next(iter(peer_numbers))
        yield p, number


@rule("F15")
def formula_units_indices(model, report):
    for p in model.paragraphs:
        if not p.math or p.region not in ("main", "appendix"):
            continue
        for math in p.math:
            for sub in xp(math, ".//m:sub"):
                text = "".join(xp(sub, ".//m:t/text()"))
                for r in xp(sub, ".//m:r"):
                    letters = "".join(xp(r, "./m:t/text()"))
                    style = val(r, "m:rPr/m:sty")
                    # m:val uses the math namespace.
                    style_node = child(r, "m:rPr/m:sty")
                    style = style_node.get(q("m:val")) if style_node is not None else style
                    if re.search(r"[а-яё]{2,}", letters, re.I) and style in ("i", "bi"):
                        report.add(p, f"Русский буквенный индекс «{text}» явно задан курсивом.",
                                   "Выберите для сокращённых русских слов в индексе прямой шрифт без курсива.")
        tail = re.search(r"(?<!\w)\d+(?:[.,]\d+)?(мм|см|км|кг|Гц|Па)\b", p.text)
        if tail:
            report.add(p, f"Численный результат и единица записаны слитно: «{tail[0]}».",
                       "Отделите единицу от численного результата одним пробелом.", "review")


@rule("F16")
def formula_glyphs(model, report):
    LOG.info("F16: отложено для MVP — высота и геометрия знаков требуют рендеринга; кегль не равен высоте буквы.")


@rule("F18")
def formula_breaks(model, report):
    for p in model.paragraphs:
        if p.math and xp(p.xml, ".//m:brk"):
            report.add(p, "В редактируемой формуле задан явный разрыв.",
                       "Проверьте повтор знака операции на границе строк, × при умножении и допустимость места переноса.",
                       "review")


@rule("F19")
def formula_numbers(model, report):
    items = []
    for p, number in numbered_formulas(model):
        if not number:
            report.add(p, "Номер распознанной отдельнострочной формулы не обнаружен.",
                       "Проверьте, не относится ли формула к группе с общим номером. "
                       "Если общего номера нет, добавьте номер арабскими цифрами в круглых скобках.", "review")
        else:
            items.append((p, number, False))
    numbering_check(items, report, "формулы", gap_status="review")
    main = [(p, n) for p, n, _ in items if not p.appendix]
    if any("." not in n for _, n in main) and len(main) > 10:
        report.add(main[0][0], f"Найдено {len(main)} пронумерованных формул, среди которых есть номера без номера раздела.",
                   "Проверьте выбранную систему нумерации формул. При количестве не более десяти "
                   "СТП прямо разрешает сквозные номера, например (1), (2). "
                   "Для большего количества уточните применение нумерации по разделам, например (2.1).", "review")


@rule("F20")
def formula_references(model, report):
    numbers = {number for _, number in numbered_formulas(model)}
    numbers.discard(None)
    for p in prose(model):
        for match in FORMULA_REF.finditer(p.text):
            # "Уравнение 4x ..." describes an expression, not formula number 4.
            if not match[1] and not match[3] and re.match(r"[\w]", p.text[match.end():]):
                continue
            if match[1] != "(" or match[3] != ")":
                report.add(p, f"Ссылка «{match[0]}» не содержит парных круглых скобок.",
                           "Заключите номер формулы в круглые скобки.")
            if match[2] not in numbers:
                report.add(p, f"Формула {match[2]} из ссылки не найдена среди распознанных формул.",
                           "Проверьте номер и наличие формулы, включая изображения и текстовые записи.", "review")


def caption_style(p, match, report, name):
    if match[1] != name:
        report.add(p, f"Подпись начинается с «{match[1]}».", f"Используйте полное слово «{name}».")
    tail = match[3]
    continuation = bool(re.search(r"\bлист\s+\d+", tail, re.I))
    if continuation:
        return
    title_match = re.match(r"\s+[-–—]\s+(\S.*)", tail, re.S)
    if not title_match:
        report.add(p, "После номера не распознаны разделитель с пробелами и наименование.",
                   f"Оформите подпись по образцу «{name} 1 – Наименование», подставив нужный номер и название.")
        return
    title = title_match[1].strip()
    if title.endswith("."):
        report.add(p, "Наименование заканчивается точкой.", "Уберите точку в конце наименования.")
    first = next((c for c in title if c.isalpha()), "")
    if first and first.islower():
        report.add(p, "Наименование начинается со строчной буквы.", "Начните наименование с прописной буквы.")


def figure_reference_numbers(text):
    """Explicit lists/ranges and both word orders, without expanding unbounded ranges."""
    for pattern in FIGURE_REFERENCES:
        for reference in pattern.finditer(text):
            for item in re.finditer(r"(" + NUMBER_TOKEN + r")(?:\s*[–—-]\s*(" + NUMBER_TOKEN + r"))?", reference[1], re.I):
                first, last = item[1].upper(), item[2].upper() if item[2] else None
                yield first
                if last is None:
                    continue
                prefix, _, start = first.rpartition('.')
                end_prefix, _, end = last.rpartition('.')
                if prefix == end_prefix and start.isdigit() and end.isdigit() and 0 <= int(end) - int(start) <= 100:
                    for n in range(int(start) + 1, int(end) + 1):
                        yield (prefix + '.' if prefix else '') + str(n)
                else:
                    LOG.info("F22: диапазон %s–%s не раскрыт; проверены только явно указанные границы.", first, last)
                    yield last


@rule("F22")
def figures(model, report):
    items = []
    for p in model.body:
        if p.kind != "caption" or p.region not in ("main", "appendix"):
            continue
        match = FIGURE.match(p.text)
        if match:
            caption_style(p, match, report, "Рисунок")
            check_figure_gap(model, p, report)
            continuation = bool(re.search(r"\bлист\s+\d+", match[3], re.I))
            items.append((p, match[2].upper(), continuation))
    numbering_check(items, report, "рисунка", True)
    targets = {n: p for p, n, _ in items}
    referred = set()
    for p in prose(model):
        for number in figure_reference_numbers(p.text):
            referred.add(number)
            if number not in targets:
                report.add(p, f"Рисунок {number} из ссылки не найден среди подписей.",
                           "Проверьте номер и наличие подписи рисунка.", "review")
    for number, p in targets.items():
        if number not in referred:
            report.add(p, f"Ссылка на рисунок {number} в распознанном тексте не найдена.",
                       "Добавьте ссылку на рисунок.", "error" if model.main_complete else "review")


@rule("F23")
def tables(model, report):
    items = []
    for table in model.tables:
        if table.kind in ("service", "formula"):
            continue
        if not table.caption:
            target = next((p for c in table.cells for p in c.paragraphs if p.text.strip()), None)
            if target:
                report.add(target, "У таблицы не обнаружена подпись; назначение таблицы не подтверждено.",
                           "Проверьте назначение таблицы. Если она содержит данные работы, добавьте над ней "
                           "подпись по образцу «Таблица 1 – Заголовок». Таблицам для подписей и реквизитов "
                           "такой заголовок не требуется.", "review")
            continue
        p = table.caption
        match = TABLE.match(p.text)
        continuation = CONTINUATION.match(p.text)
        if match:
            caption_style(p, match, report, "Таблица")
            items.append((p, match[2], False))
        elif continuation:
            items.append((p, continuation[1], True))
        else:
            report.add(p, "В подписи таблицы не найден номер.",
                       "Оформите подпись по образцу «Таблица 1 – Заголовок», подставив нужный номер и название.")
    numbering_check(items, report, "таблицы", True)


@rule("F24")
def table_layout(model, report):
    for table in model.tables:
        p = table.caption
        if table.kind != "data" or p is None:
            continue
        if p.props["alignment"] not in ("left", "start"):
            report.add(p, f"Заголовок таблицы выровнен {alignment_name(p.props['alignment'])}.",
                       "Выровняйте заголовок таблицы по левому краю.")
        if any(x.index > p.index and x.index < table.before and x.table is None and not x.text.strip()
               for x in model.paragraphs):
            report.add(p, "Между подписью и таблицей есть пустой абзац.",
                       "Уберите пустой абзац между заголовком и таблицей.")
        if p.props.get("after", 0) or p.props.get("afterLines", 0):
            LOG.info("F24: у подписи таблицы %s есть интервал после; фактический зазор требует просмотра.", table.index)
        if has_gap(model, p, False) is False:
            report.add(p, "Перед заголовком таблицы нет явно заданного промежутка.",
                       "Проверьте промежуток перед заголовком таблицы. Он должен быть равен одной пустой строке; "
                       "задайте его интервалом абзаца или пустым абзацем.", "review")


@rule("F25")
def table_continuations(model, report):
    first = {}
    for table in model.tables:
        if table.caption is None:
            continue
        start, continuation = TABLE.match(table.caption.text), CONTINUATION.match(table.caption.text)
        if start:
            first[start[2]] = table
        elif continuation and continuation[1] in first:
            original = first[continuation[1]]
            original_head = [c.text for c in original.cells if c.row == 1 and not c.continuation]
            current_head = [c.text for c in table.cells if c.row == 1 and not c.continuation]
            numeric = current_head and all(re.fullmatch(r"\d+", t) for t in current_head)
            if current_head != original_head:
                first_rows = [[c.text for c in original.cells if c.row == row and not c.continuation]
                              for row in (1, 2, 3)]
                if not numeric or current_head not in first_rows:
                    report.add(table.caption, "В продолжении таблицы не найдены такие же заголовки столбцов "
                               "или номера столбцов, как в первой части.",
                               "Повторите заголовки столбцов из первой части таблицы. Если вместо заголовков используются "
                               "номера столбцов, они должны совпадать в первой части и в продолжении.", "review")
    LOG.info("F25: автоматические переносы таблиц, части рядом и линии на границе страниц не проверены без рендеринга.")


@rule("F26")
def table_headers(model, report):
    for table in model.tables:
        if table.kind != "data":
            continue
        for cell in table.cells:
            if cell.row != 1 or cell.continuation or not cell.text:
                continue
            p = next((p for p in cell.paragraphs if p.text.strip()), cell.paragraphs[0])
            if re.fullmatch(r"(?:номер\s+по\s+порядку|№\s*п\s*/\s*п)", cell.text, re.I):
                report.add(p, "Есть отдельная графа «Номер по порядку».",
                           "Уберите отдельную графу; номера показателей допустимы перед названиями в первой графе.")
                continue
            if cell.text.endswith("."):
                report.add(p, "Заголовок графы заканчивается точкой.",
                           "Проверьте, не является ли точка частью допустимого сокращения. "
                           "В остальных случаях уберите точку в конце заголовка.", "review")
            first = next((c for c in cell.text if c.isalpha()), "")
            if first.islower():
                report.add(p, "Заголовок графы начинается со строчной.",
                           "Проверьте, является ли ячейка самостоятельным заголовком. "
                           "Самостоятельный заголовок начните с прописной буквы. "
                           "У подзаголовка, который продолжает вышестоящий заголовок, строчная буква допустима.", "review")
            diagonals = xp(cell.xml, "./w:tcPr/w:tcBorders/w:tl2br | ./w:tcPr/w:tcBorders/w:tr2bl")
            if any(attr(d) not in ("nil", "none") for d in diagonals):
                report.add(p, "У ячейки заголовка задана диагональная граница.",
                           "Проверьте ячейку в Word. Если диагональ разделяет два заголовка, уберите её "
                           "и разместите заголовки в отдельных ячейках.", "review")


@rule("F27")
def table_units_notes(model, report):
    units = r"(?:мм|см|км|кг|м|с|Па|Гц|В|А|%)"
    for table in model.tables:
        if table.kind != "data":
            continue
        for cell in table.cells:
            if cell.continuation or not cell.text or not cell.paragraphs:
                continue
            if cell.row == 1 and re.search(r"\w\s+\(" + units + r"\)\s*$", cell.text):
                report.add(cell.paragraphs[0], "В заголовке графы единица помещена в скобки.",
                           "Проверьте запись единицы после названия показателя через запятую.", "review")
        notes = [c for c in table.cells if re.match(r"^\s*Примечани[ея]\b", c.text)]
        last_row = max((c.row for c in table.cells), default=0)
        for cell in notes:
            if cell.row < last_row:
                report.add(cell.paragraphs[0], "После табличного примечания есть другие строки таблицы.",
                           "Разместите примечание к строкам/графам в конце таблицы над нижней линией.", "review")


@rule("F28")
def table_values(model, report):
    for table in model.tables:
        if table.kind != "data":
            continue
        for cell in table.cells:
            if cell.row <= 1 or cell.continuation or cell.span > 1:
                continue
            if child(cell.xml, "w:tcPr/w:vMerge") is not None:
                continue
            if not cell.text and cell.paragraphs and not xp(cell.xml, ".//w:drawing | .//m:oMath | .//w:pict | .//w:object"):
                report.add(cell.paragraphs[0], "Необъединённая ячейка данных пуста.",
                           "Проверьте назначение ячейки. Если она предназначена для данных, но данных нет, поставьте тире. "
                           "Ячейку, которая служит только для оформления таблицы, менять не нужно.", "review", prop="cell_value")
            if re.match(r"^\s*(?:От|Св\.?)\s+\d", cell.text, re.I) and "до" in cell.text and "включ." not in cell.text:
                report.add(cell.paragraphs[0], "В записи интервала не найдено «включ.».",
                           "Проверьте последовательные интервалы «От… до… включ.», «Св… до… включ.».", "review")


@rule("F35")
def footnotes(model, report):
    root = model.parts.get("word/footnotes.xml")
    notes = {attr(n, "id"): n for n in xp(root, "./w:footnote")
             if attr(n, "type", "normal") == "normal" and integer(attr(n, "id"), -1) >= 0}
    references = [(p, ref) for p in model.paragraphs for ref in xp(p.xml, ".//w:footnoteReference")]
    for p, ref in references:
        note_id = attr(ref, "id")
        note = notes.get(note_id)
        if note is None:
            report.add(p, f"У ссылки на сноску {note_id} отсутствует текст сноски.",
                       "Восстановите соответствующую сноску.")
            continue
        run = ref.getparent()
        props = model.resolver.run(p.xml, run)
        if props.get("vertical") != "superscript":
            report.add(p, "Для знака сноски не задано надстрочное начертание.",
                       "Проверьте знак сноски в Word: он должен находиться выше строки справа от поясняемого фрагмента. "
                       "При необходимости включите для знака верхний индекс.", "review")
        for np in xp(note, "./w:p"):
            text = "".join(visible_run_text(r) for r in paragraph_runs(np))
            props = model.resolver.paragraph(np)
            location = {"section": f"Сноска {note_id}", "fragment_start": text[:160]} if text.strip() else {
                "section": f"Сноска {note_id}", "property": "footnote_text"}
            first_line = props.get("firstLine", 0) - props.get("hanging", 0)
            if first_line <= 0:
                report.add(location, "Текст сноски не имеет положительного абзацного отступа.",
                           "Добавьте отступ первой строки в настройках абзаца пояснения сноски.")
        if attr(ref, "customMarkFollows") in ("1", "true"):
            report.add(p, "Сноска использует пользовательский знак.",
                       "Проверьте арабскую цифру со скобкой и совпадение знака перед пояснением.", "review")
        elif not re.search(r"\)", "".join(visible_run_text(r) for r in paragraph_runs(p.xml))):
            report.add(p, "У автоматической ссылки на сноску не распознана скобка.",
                       "Проверьте знак сноски: арабская цифра со скобкой и такой же знак перед пояснением.", "review")
    if not references:
        LOG.info("F35: пользовательские сноски отсутствуют; условная проверка пропущена.")


@rule("F36")
def notes(model, report):
    previous = None
    for p in model.paragraphs:
        if p.kind == "code" or p.region not in ("main", "appendix"):
            continue
        if p.kind == "note_item":
            match = re.match(r"^\s*(\d+)[.)]?\s+", p.numbered_text)
            if match:
                number = int(match[1])
                if previous is not None and number != previous + 1:
                    report.add(p, f"Номер примечания {number} нарушает последовательность.",
                               f"Исправьте номер примечания: следующим должен быть {previous + 1}.")
                previous = number
            continue
        if not re.match(r"^\s*Примечани[ея]\b", p.text, re.I):
            previous = None
            continue
        if re.match(r"^\s*примечани[ея]", p.text):
            report.add(p, "Обозначение примечания начинается со строчной.", "Напишите «Примечание» с прописной.")
        if re.match(r"^\s*Примечания\s*$", p.text, re.I):
            previous = 0
        elif re.match(r"^\s*Примечание\s*[-–—]\s*\S", p.text, re.I):
            if p.table is None:
                check_indent(p, report)
        elif not re.match(r"^\s*Примечани[ея]\s+\d+", p.text, re.I):
            report.add(p, "Не распознан формат одиночного примечания.",
                       "Используйте «Примечание – Текст» либо нумерацию нескольких примечаний.")
