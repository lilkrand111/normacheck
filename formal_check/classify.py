"""Conservative structural recognition; ambiguity is retained in the model."""
import logging
import re

from .xmlutil import child, xp
from .captions import is_caption
from .locations import prepare_locations

LOG = logging.getLogger("formal_check")
HEADING = re.compile(r"^\s*(\d+(?:\.\d+){0,3})(\.?)\s+(\S.*)$", re.S)
APPENDIX = re.compile(r"^\s*ПРИЛОЖЕНИЕ\s+([А-ЯЁA-Z])\s*$", re.I)
CODE = re.compile(
    r"^\s*(?:#\s*(?:include|define|ifdef|endif|pragma)|//|/\*|\*/|"
    r"(?:int|void|double|float|char|bool|size_t)\s+[A-Za-z_]|"
    r"(?:def|class|import|from|using namespace)\s+\w|"
    r"(?:if|for|while|switch)\s*\(|(?:return|printf|scanf|cout|cin|std::)\b|[{}]\s*;?\s*$)")


def normalized(text):
    return re.sub(r"\s+", " ", text).strip().casefold()


def heading_parts(text):
    match = HEADING.match(text)
    return (tuple(map(int, match[1].split("."))), match[3].strip()) if match else ((), text.strip())


def classify(model):
    region, section, appendix = "front", "", ""
    code_appendix = False
    toc_seen, main_seen = False, False
    body = model.body
    paragraphs = {p.xml: p for p in body}
    front_matter = any(re.search(r"(?:министерство|задание|пояснительная записка)", p.text, re.I)
                       for p in body[:30])
    for pos, p in enumerate(body):
        text = p.text.strip()
        display = p.display_text.strip() or text
        norm = normalized(text)
        style = p.props.get("style_name", "").lower()
        outline = p.props.get("outline", 9)
        app = APPENDIX.fullmatch(text.splitlines()[0]) if text else None
        visible_number = bool(p.label and not p.num_props.get("hidden"))
        if not text and not p.math and not p.images and not visible_number:
            p.region, p.section, p.appendix = region, section, appendix
            p.kind = "blank"
            continue
        if norm == "содержание":
            p.kind = "toc_heading"
            region, section, toc_seen = "toc", "Содержание", True
        elif norm == "реферат" and not p.in_toc and not re.match(r"^(?:toc|оглавление)\s*\d", style):
            p.kind = "abstract_heading"
            region, section = "abstract", "Реферат"
        elif norm == "приложения" and not p.in_toc and not re.match(r"^(?:toc|оглавление)\s*\d", style):
            p.kind = "appendices_heading"
            region, section, appendix = "appendix", "Приложения", ""
            code_appendix = False
        elif p.in_toc or re.match(r"^(?:toc|оглавление)\s*\d", style):
            p.kind = "toc_entry"
            if norm != "содержание":
                model.toc_entries.append(p)
            region, section, toc_seen = "toc", "Содержание", True
        elif re.fullmatch(r"список\s+использованных\s+источников\.?", norm):
            p.kind = "bibliography_heading"
            region, section = "bibliography", "Список использованных источников"
        elif app and (main_seen or region in ("bibliography", "appendix") or outline < 9):
            p.kind = "appendix_heading"
            appendix = app[1].upper()
            region, section, code_appendix = "appendix", "Приложение " + appendix, False
            model.appendices.append(p)
        else:
            numbered = p.numbered_text
            number, title = heading_parts(numbered)
            heading_style = outline < 9 or bool(re.search(r"(?:heading|заголовок)\s*[1-4]", style))
            number_only = re.fullmatch(r"\d+(?:\.\d+){0,3}\.?", numbered.strip())
            if heading_style and number_only:
                number = tuple(map(int, numbered.strip().rstrip(".").split(".")))
                title = ""
            displayed_title = re.sub(r"^\s*\d+(?:\.\d+)*\.?\s+", "", display)
            uppercase_title = any(c.isalpha() for c in displayed_title) and displayed_title.upper() == displayed_title
            if p.label and number:
                uppercase_title = any(c.isalpha() for c in display) and display.upper() == display
            special = norm in ("введение", "заключение")
            # For a manually authored TOC, a final tab/dot leader plus page is evidence.
            manual_toc = region == "toc" and bool(
                re.search(r"(?:\t|\.{2,})\s*\d+\s*$", text))
            if manual_toc:
                p.kind = "toc_entry"
                model.toc_entries.append(p)
            elif (heading_style or special or
                  (number and (toc_seen or main_seen or not front_matter) and
                   (uppercase_title or len(number) > 1) and len(title) < 220)):
                if region == "bibliography" and (special or (heading_style and number)):
                    region = "main"
                region = "main" if region not in ("appendix", "bibliography") else region
                if region != "bibliography":
                    p.kind = "heading"
                    p.heading_number, p.heading_title = number, title
                    p.heading_level = len(number) or (outline + 1 if outline < 9 else 1)
                    section = numbered.strip()
                    model.headings.append(p)
                    main_seen = main_seen or region == "main"
            if p.kind == "text":
                if region == "toc" and text:
                    # An unrecognised boundary must not silently turn TOC text into prose.
                    p.uncertain = True
                if is_caption(p, paragraphs):
                    p.kind = "caption"
                elif CODE.match(text) or re.search(r"\b(code|код|listing|листинг)\b", style):
                    p.kind = "code"
                elif xp(p.xml, ".//m:oMathPara") or (
                        (p.math or re.search(r"[=∫∑√]", text)) and
                        len(re.findall(r"[а-яё]{2,}", text, re.I)) < 4):
                    p.kind = "formula"
                elif re.match(r"^\s*(?:где\b|Здесь\b|В формуле обозначено\b)", text, re.I):
                    p.kind = "explanation"
                elif re.match(r"^\s*Примечани[ея]\b", text, re.I):
                    p.kind = "note"
                elif p.num_id or re.match(r"^\s*(?:[-–—]|[а-яa-z]\)|\d+[.)])\s+\S", text):
                    p.kind = "list"
                elif p.images and not text:
                    p.kind = "image"
        p.region, p.section, p.appendix = region, section, appendix
        if region == "bibliography" and p.kind != "bibliography_heading" and text:
            p.kind = "bibliography_entry"
            model.bibliography.append(p)
        if region == "appendix":
            if re.search(r"(?:исходн\w*\s+код|листинг\s+программ|текст\s+программ)", text, re.I):
                code_appendix = True
            if code_appendix and p.kind not in ("appendix_heading", "caption") and (
                    CODE.match(text) or p.kind == "code"):
                p.kind = "code"
    # Fill main-only documents without front matter or a TOC conservatively.
    if not main_seen and not toc_seen and not model.bibliography and not model.appendices:
        if not front_matter:
            for p in body:
                p.region = "main"
            main_seen = True
    model.main_complete = main_seen and not model.opaque_objects
    if not main_seen:
        LOG.warning("STRUCTURE: граница основного текста не определена; проверки обычного текста ограничены.")
    # Classify code blocks, retaining blank lines and non-keyword lines inside braces.
    in_code, depth = False, 0
    for p in body:
        if p.kind in ("heading", "appendix_heading", "bibliography_heading", "caption"):
            in_code, depth = False, 0
        if p.kind == "code":
            in_code = True
        if in_code:
            if p.kind in ("text", "formula", "list", "code") and (
                    depth > 0 or p.kind == "code" or not p.text.strip()):
                p.kind = "code"
                depth = max(0, depth + p.text.count("{") - p.text.count("}"))
            elif p.text.strip():
                in_code = False
    # Recognise status/title rows immediately following an appendix designation.
    for app in model.appendices:
        if len([line for line in app.text.splitlines() if line.strip()]) >= 3:
            continue
        following = [p for p in body if p.index > app.index and p.text.strip()][:2]
        for i, p in enumerate(following):
            if p.kind in ("appendix_heading", "heading", "caption", "code"):
                break
            if i == 0 and re.fullmatch(r"\([^()]+\)", p.text.strip().splitlines()[0]):
                p.kind = "appendix_status"
                if len(p.text.strip().splitlines()) > 1:
                    p.kind = "appendix_meta"
                    break
            else:
                p.kind = "appendix_title"
                break
    in_notes = False
    for p in body:
        if re.fullmatch(r"\s*Примечания\s*", p.text, re.I):
            in_notes = True
        elif in_notes and p.kind == "list" and re.match(r"^\s*\d+[.)]?\s+", p.numbered_text):
            p.kind = "note_item"
        elif p.text.strip():
            in_notes = False
    # Tables retain their XML grid locations, including merged-cell continuations.
    for table in model.tables:
        previous = [p for p in body if p.index < table.before and p.text.strip()]
        anchor = previous[-1] if previous else None
        contents = " ".join(c.text for c in table.cells)
        if anchor and anchor.kind == "caption" and re.match(
                r"^\s*(?:Таблица|Табл\.|Продолжение таблицы)\s+", anchor.text, re.I):
            table.kind, table.caption = "data", anchor
        elif any(p.math for c in table.cells for p in c.paragraphs):
            table.kind = "formula"
        elif (anchor and anchor.region == "front") or re.search(r"подпись\s+(?:студента|руководителя)", contents, re.I):
            table.kind = "service"
        elif any(child(c.xml, "w:tcPr") is not None for c in table.cells) and len(table.cells) >= 4:
            # A rectangular grid alone is insufficient to assert that it contains data.
            table.kind = "unknown"
        for cell in table.cells:
            for p in cell.paragraphs:
                p.region = anchor.region if anchor else "front"
                p.section = anchor.section if anchor else ""
                p.appendix = anchor.appendix if anchor else ""
                p.kind = "table" if table.kind == "data" else "table_" + table.kind
                if table.caption:
                    match = re.match(r"^\s*(?:Таблица|Табл\.|Продолжение таблицы)\s+(\S+)", table.caption.text, re.I)
                    p.table_label = match[1] if match else ""
                if CODE.match(p.text):
                    p.kind = "code"
    prepare_locations(model)
    return model
