"""One isolated DOCX operation. Only staging files are written by this process."""
import argparse
import ctypes
import logging
import math
import os
from pathlib import Path, PurePosixPath
import signal
import sys
from zipfile import BadZipFile, ZipFile

from formal_check import CheckError, DocumentReadError, check_document
from formal_fix import FixError, fix_document

from .storage import atomic_json


def validate_package(path):
    try:
        with ZipFile(path) as package:
            entries = package.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > 2048 or len(set(names)) != len(names):
                raise DocumentReadError("Слишком много частей DOCX или найдены повторяющиеся части.")
            if not {"[Content_Types].xml", "word/document.xml", "_rels/.rels"}.issubset(names):
                raise DocumentReadError("Файл не является поддерживаемым DOCX.")
            if sum(entry.file_size for entry in entries) > 256 * 1024 * 1024:
                raise DocumentReadError("Распакованный DOCX превышает 256 МиБ.")
            for entry in entries:
                name = PurePosixPath(entry.filename)
                if name.is_absolute() or ".." in name.parts or "\\" in entry.filename or entry.flag_bits & 1:
                    raise DocumentReadError("Неподдерживаемая структура архива DOCX.")
                if entry.file_size > 64 * 1024 * 1024 or (entry.filename.endswith(".xml") and entry.file_size > 32 * 1024 * 1024):
                    raise DocumentReadError("Одна из частей DOCX превышает допустимый размер.")
                if entry.file_size > 1024 * 1024 and entry.file_size / max(entry.compress_size, 1) > 1000:
                    raise DocumentReadError("Слишком высокая степень сжатия DOCX.")
                if entry.filename.lower().endswith("vbaproject.bin"):
                    raise DocumentReadError("Документы с макросами не поддерживаются.")
    except (BadZipFile, OSError, NotImplementedError) as exc:
        raise DocumentReadError("Не удалось открыть архив DOCX.") from exc


def prepare_process(parent_pid, memory_mb, timeout):
    if sys.platform == "linux":
        # A crashed API must not leave a document worker running indefinitely.
        ctypes.CDLL(None).prctl(1, signal.SIGKILL)
        if os.getppid() != parent_pid:
            raise SystemExit(1)
        import resource
        memory = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        cpu = math.ceil(timeout) + 1
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))


def run_operation(source, work, operation):
    warnings = []

    class Capture(logging.Handler):
        def emit(self, record):
            message = record.getMessage()
            if (message.startswith(("READER:", "IMAGES:", "FORMULAS:", "TABLES:")) or record.levelno >= logging.WARNING) and message not in warnings and len(warnings) < 100:
                warnings.append(message)

    logger = logging.getLogger("formal_check")
    logger.setLevel(logging.INFO)
    capture = Capture()
    logger.addHandler(capture)
    validate_package(source)
    if operation == "fix":
        target = work / "corrected.docx"
        fix_document(source, target)
    else:
        target = source
    report = check_document(target)
    atomic_json(work / "report.json", report)
    atomic_json(work / "warnings.json", warnings)
    logger.removeHandler(capture)
    return {"ok": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("check", "fix"))
    parser.add_argument("source", type=Path)
    parser.add_argument("work", type=Path)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--memory-mb", type=int, required=True)
    parser.add_argument("--timeout", type=float, required=True)
    args = parser.parse_args()
    prepare_process(args.parent_pid, args.memory_mb, args.timeout)
    logging.basicConfig(level=logging.WARNING)
    try:
        result = run_operation(args.source, args.work, args.operation)
    except DocumentReadError:
        logging.exception("Invalid DOCX")
        result = {"ok": False, "code": "invalid_document", "message": "Не удалось прочитать DOCX. Проверьте формат файла и ограничения размера; документы с макросами не поддерживаются."}
    except (CheckError, FixError, MemoryError):
        logging.exception("Document processing failed")
        result = {"ok": False, "code": "processing_failed", "message": "Не удалось завершить обработку документа. Исходный файл сохранён."}
    except Exception:
        logging.exception("Unexpected worker failure")
        result = {"ok": False, "code": "processing_failed", "message": "Внутренняя ошибка обработки документа. Исходный файл сохранён."}
    atomic_json(args.work / "result.json", result)


if __name__ == "__main__":
    main()
