"""Save a corrected copy atomically; the caller runs the final full check."""
from io import BytesIO
import logging
import os
from pathlib import Path
import tempfile
from zipfile import ZipFile

from lxml import etree

from formal_check import CheckError, DocumentReadError, inspect_document
from formal_check import subsection_layout
from formal_check.reader import read_document
from formal_check.xmlutil import xp

from .operations import apply_plan, build_plan, current_value, target_xml

LOG = logging.getLogger("formal_fix")


class FixError(RuntimeError):
    """Correction or output validation failed; no new output was published."""


def _validate_paths(source, output):
    if source.suffix.lower() != ".docx" or output.suffix.lower() != ".docx":
        raise FixError("Входной и выходной файлы должны иметь расширение .docx.")
    if source.resolve() == output.resolve() or (
            source.exists() and output.exists() and os.path.samefile(source, output)):
        raise FixError("Пути исходного и исправленного DOCX должны различаться.")


def _write_package(source_bytes, output, document):
    """Replace only the main XML part, retaining every other ZIP entry's bytes."""
    xml = etree.tostring(document.element, encoding="UTF-8", xml_declaration=True, standalone=True)
    with ZipFile(BytesIO(source_bytes)) as original, ZipFile(output, "w") as corrected:
        corrected.comment = original.comment
        for entry in original.infolist():
            data = xml if entry.filename == "word/document.xml" else original.read(entry)
            corrected.writestr(entry, data)


def _verify_saved(path, model, plan):
    # Only read back and check edited properties; no second run of all active rules.
    saved = read_document(path)
    separator_targets = {d.target.index for d in plan if d.kind == "subsection_layout"}
    expected_text = [subsection_layout.expected_text(model, p) if p.index in separator_targets else p.text
                     for p in model.paragraphs]
    if expected_text != [p.text for p in saved.paragraphs]:
        raise FixError("При сохранении изменился текст или порядок абзацев.")
    original_numbers = [(p.label, p.num_id, p.num_level, p.num_known) for p in model.paragraphs]
    saved_numbers = [(p.label, p.num_id, p.num_level, p.num_known) for p in saved.paragraphs]
    if original_numbers != saved_numbers:
        raise FixError("При сохранении изменилась нумерация документа.")
    for d in plan:
        node = target_xml(d)
        locator = node.getroottree().getpath(node)
        matches = xp(saved.document.element, locator)
        if len(matches) != 1 or current_value(saved, d, matches[0]) != d.expected:
            raise FixError(f"Не подтверждено исправление {d.rule_id}: {d.property}.")


def fix_document(input_path, output_path):
    """Save a corrected DOCX at output_path and return None.

    F01, F03, unambiguous F04/F05 indents and subsection tabs, F06 alignment
    and zero indents of unnumbered titles, and the spacing of a single existing
    blank paragraph together with the facing edges of its neighbors (F07) are
    supported. Unsupported findings are left for the caller's subsequent
    check_document(output_path).
    If there are no eligible edits, save an exact byte copy. On failure preserve
    the input and any existing output, raising DocumentReadError, CheckError or
    FixError. The input must not be edited concurrently during this call.
    """
    temporary = None
    try:
        source, output = Path(input_path), Path(output_path)
        _validate_paths(source, output)
        try:
            source_bytes = source.read_bytes()
        except OSError as exc:
            raise DocumentReadError(f"Не удалось прочитать DOCX: {exc}") from exc
        inspection = inspect_document(source)
        # Bind inspection and package copying to exactly the same source bytes.
        if source.read_bytes() != source_bytes:
            raise FixError("Исходный DOCX изменился во время проверки. Повторите обработку.")
        with ZipFile(BytesIO(source_bytes)) as package:
            names = package.namelist()
            if len(names) != len(set(names)):
                raise FixError("В DOCX есть повторяющиеся имена частей; исправление неоднозначно.")
        plan = build_plan(inspection)
        apply_plan(inspection.model, plan)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".formal-fix-",
                                         suffix=".docx", delete=False) as stream:
            temporary = Path(stream.name)
        if plan:
            _write_package(source_bytes, temporary, inspection.model.document)
        else:
            temporary.write_bytes(source_bytes)
        _verify_saved(temporary, inspection.model, plan)
        os.replace(temporary, output)
        temporary = None
        LOG.info("Выполнено операций: %d. Сохранено: %s. Итоговый отчёт получите через formal_check.",
                 len(plan), output)
    except (DocumentReadError, CheckError, FixError):
        raise
    except Exception as exc:
        raise FixError(f"Сбой автоисправления: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                LOG.warning("Не удалось удалить временный файл %s.", temporary)
