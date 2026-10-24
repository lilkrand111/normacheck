import argparse
import logging
from pathlib import Path
import sys

from formal_check import CheckError, DocumentReadError
from . import FixError, fix_document


def main(argv=None):
    parser = argparse.ArgumentParser(description="Автоисправление оформления DOCX по СТП 01–2017 (MVP).")
    parser.add_argument("docx_path", type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path,
                        help="Путь исправленной копии DOCX; исходник не перезаписывается")
    args = parser.parse_args(argv)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    loggers = [logging.getLogger(name) for name in ("formal_check", "formal_fix")]
    levels = [logger.level for logger in loggers]
    try:
        for logger in loggers:
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        fix_document(args.docx_path, args.output)
        return 0
    except (DocumentReadError, CheckError, FixError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    finally:
        for logger, level in zip(loggers, levels):
            logger.removeHandler(handler)
            logger.setLevel(level)
        handler.close()


if __name__ == "__main__":
    sys.exit(main())
