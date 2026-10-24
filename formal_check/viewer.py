"""Build a self-contained, offline HTML viewer from one or more JSON reports."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from .report import validate_report

TEMPLATE = Path(__file__).with_name("templates") / "report.html"
PLACEHOLDER = "__FORMAL_CHECK_REPORTS__"


def render_reports(report_paths):
    """Return HTML with validated reports embedded as inert, escaped JSON."""
    reports = []
    for path in report_paths:
        path = Path(path)
        report = validate_report(json.loads(path.read_text(encoding="utf-8-sig")))
        reports.append({"name": path.name, "report": report})
    payload = json.dumps(reports, ensure_ascii=False)
    # A finding can contain arbitrary document text, including </script>.
    for char, escaped in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"),
                          ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        payload = payload.replace(char, escaped)
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count(PLACEHOLDER) != 1:
        raise ValueError("Некорректный шаблон HTML.")
    return template.replace(PLACEHOLDER, payload)


def write_html_report(report_paths, output_path):
    """Write an HTML report atomically, preserving input JSON files."""
    paths = [Path(p) for p in report_paths]
    output = Path(output_path)
    if output.suffix.lower() != ".html":
        raise ValueError("Файл страницы должен иметь расширение .html.")
    for path in paths:
        if path.resolve() == output.resolve() or (
                path.exists() and output.exists() and os.path.samefile(path, output)):
            raise ValueError("HTML и входные отчёты должны иметь разные пути.")
    html = render_reports(paths)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent,
                                         prefix=".report-viewer-", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            stream.write(html)
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary:
            os.unlink(temporary)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description="Страница для разбора замечаний формальной проверки.")
    parser.add_argument("reports", nargs="*", type=Path, help="JSON-отчёты; без них создаётся пустая страница")
    parser.add_argument("-o", "--output", required=True, type=Path, help="Самодостаточный HTML")
    args = parser.parse_args(argv)
    try:
        write_html_report(args.reports, args.output)
    except (OSError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
