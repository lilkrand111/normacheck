import argparse
from itertools import combinations
import json
import logging
import os
from pathlib import Path
import sys
import tempfile

from . import CheckError, DocumentReadError, check_document


def main(argv=None):
    parser = argparse.ArgumentParser(description="Формальная проверка DOCX по СТП 01–2017 (MVP).")
    parser.add_argument("docx_path", type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path, help="Путь результата JSON в UTF-8")
    parser.add_argument("--log", type=Path, help="Диагностика в UTF-8; по умолчанию stderr")
    args = parser.parse_args(argv)
    logger = logging.getLogger("formal_check")
    handler = None
    temporary = None
    old_level = logger.level
    try:
        paths = [args.docx_path.resolve(), args.output.resolve()]
        if args.log:
            paths.append(args.log.resolve())
        if len(set(paths)) != len(paths) or any(
                a.exists() and b.exists() and os.path.samefile(a, b) for a, b in combinations(paths, 2)):
            raise ValueError("Пути входного DOCX, результата и журнала должны различаться.")
        if args.output.suffix.lower() != ".json":
            raise ValueError("Файл результата должен иметь расширение .json.")
        if args.log:
            args.log.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(args.log, mode="w", encoding="utf-8")
        else:
            handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        result = check_document(args.docx_path)
        payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=args.output.parent,
                                         prefix=".formal-check-", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            stream.write(payload)
        os.replace(temporary, args.output)
        temporary = None
        return 0
    except (DocumentReadError, CheckError, OSError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        if handler:
            logger.removeHandler(handler)
            handler.close()
        logger.setLevel(old_level)


if __name__ == "__main__":
    sys.exit(main())
