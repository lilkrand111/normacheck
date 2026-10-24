"""Read DOCX stories in document order, including SDTs and hyperlink text."""
import logging
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from docx import Document

from .formatting import Numbering, Resolver
from .model import Cell, Model, Paragraph, Run, Table
from .xmlutil import attr, child, integer, parse, q, xp

LOG = logging.getLogger("formal_check")


class DocumentReadError(ValueError):
    """The input cannot be read as a supported DOCX package."""


def visible_run_text(r):
    pieces = []
    for node in r:
        if node.tag == q("w:t"):
            pieces.append(node.text or "")
        elif node.tag == q("w:tab"):
            pieces.append("\t")
        elif node.tag in (q("w:br"), q("w:cr")) and attr(node, "type") not in ("page", "column"):
            pieces.append("\n")
        elif node.tag == q("w:noBreakHyphen"):
            pieces.append("\u2011")
        elif node.tag == q("w:softHyphen"):
            pieces.append("\u00ad")
    return "".join(pieces)


def paragraph_runs(p):
    """Do not leak nested text boxes, deleted revisions or OMML into prose."""
    for r in xp(p, ".//w:r"):
        if any(a.tag in (q("w:del"), q("w:moveFrom"), q("w:txbxContent"),
                         q("m:oMath")) for a in r.iterancestors() if a is not p):
            continue
        if next((a for a in r.iterancestors() if a.tag == q("w:p")), None) is p:
            yield r


def read_document(path):
    path = Path(path)
    if path.suffix.lower() != ".docx":
        raise DocumentReadError("Поддерживается только формат .docx.")
    try:
        with ZipFile(path) as z:
            if "word/document.xml" not in z.namelist():
                raise DocumentReadError("В архиве отсутствует word/document.xml.")
            if sum(i.file_size for i in z.infolist()) > 256 * 1024 * 1024:
                raise DocumentReadError("Распакованный DOCX превышает лимит MVP 256 МиБ.")
            parts = {}
            for name in z.namelist():
                if (name.startswith(("word/header", "word/footer", "word/footnotes",
                                     "word/endnotes", "word/theme/theme", "word/settings",
                                     "word/numbering", "word/styles")) and name.endswith(".xml")):
                    parts[name] = parse(z.read(name))
            if any(i.filename.endswith("vbaProject.bin") for i in z.infolist()):
                raise DocumentReadError("Документы с макросами не поддерживаются.")
        document = Document(str(path))
    except DocumentReadError:
        raise
    except Exception as exc:
        # Wrap library-specific ZIP/XML/OPC exceptions at the API boundary.
        raise DocumentReadError(f"Не удалось прочитать DOCX: {exc}") from exc
    resolver = Resolver(parts.get("word/styles.xml"), parts.get("word/numbering.xml"),
                        parts.get("word/theme/theme1.xml"))
    numbering = Numbering(resolver)
    paragraphs, tables = [], []
    section_index = 1
    unsupported = set()

    def make_paragraph(p, toc=False, table=None, row=None, col=None):
        nonlocal section_index
        runs = [Run(visible_run_text(r), resolver.run(p, r)) for r in paragraph_runs(p)]
        label, num_id, level, num_props, known = numbering.label(p)
        urls = []
        for link in xp(p, ".//w:hyperlink"):
            rel = document.part.rels.get(link.get(q("r:id")))
            if rel is not None and rel.is_external:
                urls.append(rel.target_ref)
        fields = " ".join(xp(p, ".//w:instrText/text()") + xp(p, ".//w:fldSimple/@w:instr"))
        para = Paragraph(
            len(paragraphs), p, "".join(r.text for r in runs if not r.props.get("hidden")), runs, resolver.paragraph(p),
            section_index=section_index, table=table, row=row, col=col,
            in_toc=toc or "TOC " in fields, label=label, num_id=num_id,
            num_level=level, num_props=num_props, num_known=known,
            math=xp(p, ".//m:oMath"), images=len(xp(p, ".//w:drawing | .//w:pict")),
            urls=urls,
        )
        paragraphs.append(para)
        if table is None and child(p, "w:pPr/w:sectPr") is not None:
            section_index += 1
        for tag in ("altChunk", "object", "txbxContent", "del", "ins", "sym"):
            if xp(p, ".//w:" + tag):
                unsupported.add(tag)
        return para

    def walk(container, toc=False, table=None, row=None, col=None):
        for node in container:
            if node.tag == q("w:p"):
                make_paragraph(node, toc, table, row, col)
            elif node.tag == q("w:tbl"):
                index = len(tables) + 1
                item = Table(index, node, [], len(paragraphs))
                tables.append(item)
                for ri, tr in enumerate(xp(node, "./w:tr"), 1):
                    ci = integer(attr(child(tr, "w:trPr/w:gridBefore")), 0) + 1
                    for tc in xp(tr, "./w:tc"):
                        span = integer(attr(child(tc, "w:tcPr/w:gridSpan")), 1)
                        merge = child(tc, "w:tcPr/w:vMerge")
                        continuation = merge is not None and attr(merge, default="continue") != "restart"
                        first = len(paragraphs)
                        walk(tc, toc, index, ri, ci)
                        item.cells.append(Cell(ri, ci, span, continuation, tc,
                                               [p for p in paragraphs[first:] if p.table == index]))
                        ci += span
            elif node.tag == q("w:sdt"):
                is_toc = toc or bool(xp(node, './/w:docPartGallery[@w:val="Table of Contents"]'))
                instructions = " ".join(xp(node, ".//w:instrText/text()"))
                is_toc = is_toc or "TOC " in instructions
                content = child(node, "w:sdtContent")
                if content is not None:
                    walk(content, is_toc, table, row, col)
            elif node.tag in (q("w:customXml"), q("w:ins"), q("w:moveTo")):
                if node.tag != q("w:customXml"):
                    unsupported.add("ins")
                walk(node, toc, table, row, col)
            elif node.tag == q("mc:AlternateContent"):
                # Select one branch only; never count both Choice and Fallback.
                choices = xp(node, "./mc:Choice")
                fallback = child(node, "mc:Fallback")
                selected = choices[0] if choices else fallback
                if selected is not None:
                    walk(selected, toc, table, row, col)
                unsupported.add("AlternateContent")
            elif node.tag == q("w:altChunk"):
                unsupported.add("altChunk")

    walk(document.element.body)
    for feature in sorted(unsupported):
        LOG.warning("READER: %s: ограниченная поддержка; вложенный неподдерживаемый текст не проверен.", feature)
    if "ins" in unsupported or "del" in unsupported:
        LOG.warning("READER: исправления прочитаны как итоговый текст (вставки включены, удаления исключены).")
    model = Model(document, paragraphs, tables, resolver, parts)
    model.opaque_objects = bool(unsupported & {"object", "altChunk", "AlternateContent", "txbxContent"})
    return model
