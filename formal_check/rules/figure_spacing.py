"""F22: the customer-requested blank line between a figure and its caption."""
from .common import LOG
from ..reader import paragraph_runs
from ..xmlutil import attr, q, xp


EXPECTED = (
    "Между рисунком и подписью «Рисунок … – Наименование» оставьте одну пробельную строку. "
    "Используйте один пустой абзац либо эквивалентный интервал в одну строку. "
    "Пустая строка перед рисунком не заменяет строку перед подписью."
)


def pagination_marker(node):
    return (node.tag == q("w:lastRenderedPageBreak") or
            (node.tag == q("w:br") and attr(node, "type") in ("page", "column")))


def break_before_caption_text(model, caption):
    """Markers after the caption has started do not describe its leading gap."""
    for run in paragraph_runs(caption.xml):
        if model.resolver.run(caption.xml, run).get("hidden"):
            continue
        for node in run:
            if pagination_marker(node):
                return True
            if node.tag == q("w:t") and (node.text or "").strip():
                return False
    return False


def after_picture(model, p):
    """Visible run content after the last picture, preserving explicit breaks."""
    tail = None
    before = ""
    gap_break = False
    for run in paragraph_runs(p.xml):
        if model.resolver.run(p.xml, run).get("hidden"):
            continue
        for node in run:
            if node.tag in (q("w:drawing"), q("w:pict")):
                before += tail or ""
                tail = ""
                # A marker before the last picture concerns the picture's
                # preceding page, not the gap following that picture.
                gap_break = False
            elif pagination_marker(node):
                if tail is not None and not tail.strip():
                    gap_break = True
            elif node.tag in (q("w:t"), q("w:tab"), q("w:br"), q("w:cr")):
                if node.tag == q("w:t"):
                    text = node.text or ""
                elif node.tag == q("w:tab"):
                    text = "\t"
                else:
                    text = "" if attr(node, "type") in ("page", "column") else "\n"
                if tail is None:
                    before += text
                else:
                    tail += text
    return tail, before, gap_break


def check_figure_gap(model, caption, report):
    blanks = []
    image = caption if caption.images else None
    if image is None:
        for p in reversed([p for p in model.paragraphs if p.index < caption.index]):
            if (p.table is not None or p.region != caption.region or p.section != caption.section or
                    p.section_index != caption.section_index or p.appendix != caption.appendix):
                break
            if p.kind == "blank":
                blanks.append(p)
                continue
            if p.images:
                image = p
            break
    if image is None:
        LOG.info("F22: перед подписью «%s» не удалось связать рисунок с подписью; промежуток не оценён.",
                 caption.text.strip()[:80])
        return

    def review(actual):
        report.add(caption, actual, "Проверьте промежуток в Word. " + EXPECTED,
                   "review", prop="figure_caption_gap")

    if (xp(image.xml, ".//wp:anchor | .//w:pict") or
            not xp(image.xml, ".//wp:inline")):
        review("Рисунок плавающий либо его способ размещения не распознан; "
               "расстояние до подписи нельзя определить по порядку абзацев.")
        return
    tail, before, gap_break = after_picture(model, image)
    # A break before the image or after the caption is outside the interval
    # being checked. An image and caption sharing a paragraph also share its
    # pageBreakBefore/sectPr, which do not separate their inline contents.
    if image is not caption:
        gap_break = gap_break or caption.props.get("page_break_before") or break_before_caption_text(model, caption)
        gap_break = gap_break or bool(xp(image.xml, "./w:pPr/w:sectPr")) or any(
            p.props.get("page_break_before") or
            xp(p.xml, './/w:br[@w:type="page" or @w:type="column"] | ./w:pPr/w:sectPr | '
               './/w:lastRenderedPageBreak') for p in blanks)
    if gap_break:
        review("Между рисунком и началом подписи обнаружен разрыв страницы, колонки или секции.")
        return
    if tail is None or before.strip() or (image is not caption and tail.strip()):
        review("В абзаце рисунка есть другой текст либо связь рисунка с подписью неоднозначна.")
        return

    if image is caption:
        # Two line breaks after a picture leave one blank line before the text.
        blank_lines = max(0, tail[:len(tail) - len(tail.lstrip())].count("\n") - 1)
        spaced = automatic = False  # Paragraph spacing does not separate its own contents.
        lines = 0
        line_sources = [image]
    else:
        leading = caption.text[:len(caption.text) - len(caption.text.lstrip())]
        blank_lines = sum(1 + p.text.count("\n") for p in blanks) + tail.count("\n") + leading.count("\n")
        spaced = image.props.get("after", 0) != 0 or caption.props.get("before", 0) != 0
        lines = max(image.props.get("afterLines", 0), caption.props.get("beforeLines", 0))
        automatic = any(p.props.get(key) in ("1", "true", "on") for p, key in (
            (image, "afterAutospacing"), (caption, "beforeAutospacing")))
        line_sources = blanks + ([image] if "\n" in tail else []) + ([caption] if "\n" in leading else [])

    extra = any(p.props.get(key, 0) != 0 for p in blanks
                for key in ("before", "after", "beforeLines", "afterLines"))
    extra = extra or any(p.props.get(key) in ("1", "true", "on") for p in blanks
                         for key in ("beforeAutospacing", "afterAutospacing"))
    line_units_known = all(p.props["lineRule"] == "auto" and p.props["line"] > 0
                           for p in line_sources)
    if blank_lines == 1 and line_units_known and not (spaced or lines or automatic or extra):
        return
    if (blank_lines == 0 and lines == 100 and not (spaced or automatic) and
            not image.props.get("contextual_spacing") and not caption.props.get("contextual_spacing")):
        return
    if not blank_lines and not (spaced or lines or automatic):
        report.add(caption, "Между рисунком и подписью нет пробельной строки или заданного интервала.",
                   EXPECTED, prop="figure_caption_gap")
    else:
        review(f"Между рисунком и подписью распознано пустых строк: {blank_lines}. "
               "Равенство промежутка одной пробельной строке нельзя подтвердить по настройкам.")
