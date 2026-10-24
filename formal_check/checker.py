import logging

from .classify import classify
from .coverage import LIMITATIONS
from .model import Inspection
from .reader import DocumentReadError, read_document
from .report import RULE_IDS, Reporter
from .rules import RULES

LOG = logging.getLogger("formal_check")


class CheckError(RuntimeError):
    """An internal check failed. No partial report is returned."""


def check_document(docx_path):
    """Return a validated dict. Read failures and check failures raise exceptions.

    Configure the ``formal_check`` logger at INFO to receive coverage diagnostics.
    The original DOCX is never saved or modified.
    """
    return inspect_document(docx_path).report


def inspect_document(docx_path):
    """Inspect once, retaining typed diagnostics bound to the loaded model.

    Neither this function nor the rules mutate XML. The model and diagnostics
    are for same-process consumers, not for serialization or reuse on a new file.
    """
    try:
        model = read_document(docx_path)
        classify(model)
        report = Reporter()
        math_count = sum(len(p.math) for p in model.paragraphs)
        image_count = sum(p.images for p in model.paragraphs)
        LOG.info("DOCX: абзацев %d; таблиц Word %d; формул OMML %d; графических контейнеров %d.",
                 len(model.paragraphs), len(model.tables), math_count, image_count)
        if not math_count:
            LOG.info("FORMULAS: OMML не обнаружены; это не доказывает отсутствия формул в тексте, изображениях или OLE.")
        if not model.tables:
            LOG.info("TABLES: таблиц Word нет; наличие таблиц на изображениях не проверено.")
        if image_count:
            LOG.info("IMAGES: изображения не распознаются; формулы/таблицы внутри них не проверены.")
        if set(RULES) != set(RULE_IDS):
            raise ValueError("Реестр проверок не соответствует списку действующих правил.")
        for rule_id in RULE_IDS:
            report.rule = rule_id
            LOG.info("%s: %s.", rule_id, LIMITATIONS[rule_id])
            before = len(report.findings)
            RULES[rule_id](model, report)
            LOG.info("%s: обработка завершена, замечаний: %d (это не подтверждение непроверенных частей).",
                     rule_id, len(report.findings) - before)
        return Inspection(model, report.result(), report.diagnostics)
    except DocumentReadError:
        raise
    except Exception as exc:
        raise CheckError(f"Сбой формальной проверки: {exc}") from exc
