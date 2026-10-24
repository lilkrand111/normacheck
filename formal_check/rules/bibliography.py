"""Conservative F33 text checks; spans always refer to the original paragraph."""
import re


SURNAME = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)*"
INITIALS = r"[А-ЯЁ]\.(?:[ \t]*[А-ЯЁ]\.)?"
AUTHOR = re.compile(
    rf"^\s*(?:\[?\d+(?:\]|[.)])?\s+)?(?P<name>{SURNAME})"
    rf"(?P<comma>,?)[ \t]+(?P<initials>{INITIALS})(?=\s|$)")
EDITION = re.compile(
    r":\s*(?P<kind>Учебное пособие|Учеб\. пособие|Справ\. пособие|"
    r"Методические указания|Метод\. указания|Курс лекций|Учебник|Монография|Справочник)\b")
PROTECTED = re.compile(
    r"(?:[a-z][a-z\d+.-]*://|www\.)[^\s<>]+|"
    r"\b(?:doi:\s*)?10\.\d{4,9}/\S+|"
    r"\b[\w.+-]+@[\w.-]+\.\w+|\b[a-z]:[\\/][^\s<>]+|"
    r"\b\d+(?::\d+)+\b|"
    r"«[^»]*»|“[^”]*”|\"[^\"]*\"|\[(?!\d+\])[^\]]*\]", re.I)


def excerpt(text, start, end):
    """Show short literal context; keep a following address or author's name whole."""
    before = re.search(r"\S+(?:\s+\S+)?\s*$", text[:start])
    after = re.match(rf"\s*(?:{INITIALS}\s+{SURNAME}|\S+)", text[end:])
    left = before.start() if before else start
    right = end + after.end() if after else end
    return left, right, text[left:right].strip()


def bibliography_issues(text):
    # Same-length masking prevents a URL replacement from shifting later locations.
    masked = PROTECTED.sub(lambda m: "#" * len(m[0]), text)
    problems = []
    seen = set()

    def add(start, end, description, instruction, replacement):
        key = (start, end, description)
        if key in seen:
            return
        seen.add(key)
        left, right, fragment = excerpt(text, start, end)
        corrected = (text[left:start] + replacement + text[end:right]).strip()
        problems.append((start, f"В «{fragment}»: {description}",
                         f"{instruction}: «{fragment}» → «{corrected}»."))

    author = AUTHOR.match(masked)
    initials_spans = set()
    if author:
        if not author["comma"]:
            start, name_end = author.span("name")
            end = author.end("initials")
            add(start, end, "после фамилии автора отсутствует запятая.",
                "Поставьте запятую после фамилии автора", text[start:name_end] + "," + text[name_end:end])
        initials_spans.add(author.span("initials"))

    # In responsibility statements, initials may precede or follow a surname.
    # They never cause a demand for a comma after that surname.
    for responsibility in re.finditer(r"(?<!/)/(?!/)(.*?)(?=//|\s+[–—]\s+|$)", masked):
        start, end = responsibility.span(1)
        for match in re.finditer(rf"(?<![\w.])(?P<initials>{INITIALS})\s+{SURNAME}", masked[start:end]):
            a, b = match.span("initials")
            initials_spans.add((start + a, start + b))
        for match in re.finditer(rf"\b{SURNAME},?\s+(?P<initials>{INITIALS})(?![\w.])", masked[start:end]):
            a, b = match.span("initials")
            initials_spans.add((start + a, start + b))
    for start, end in sorted(initials_spans):
        if re.search(r"[А-ЯЁ]\.[А-ЯЁ]\.", text[start:end]):
            add(start, end, "между инициалами отсутствует пробел.",
                "Разделите инициалы пробелом", text[start:end].replace(".", ". ", 1))

    for match in re.finditer(r"(?:^|[–—])\s*(?P<city>Мн\.)(?=\s*[:,])", masked):
        start, end = match.span("city")
        add(start, end, "название города «Минск» сокращено.",
            "Напишите название города полностью", "Минск")
    for match in EDITION.finditer(masked):
        start, end = match.span("kind")
        add(start, end, "вид издания начинается с прописной буквы.",
            "Напишите вид издания со строчной буквы", text[start:end].lower())

    # A double slash is one delimiter. Single slashes without surrounding
    # whitespace may be identifiers (C/C++), so ambiguous cases are skipped.
    for match in re.finditer(r"//|[:;–/]", masked):
        start, end = match.span()
        if start == 0 or end == len(text):
            continue
        before, after = text[start - 1], text[end]
        if match[0] == "/" and not (before.isspace() or after.isspace()):
            continue
        if match[0] == "–":
            if re.search(r"\d\s*$", masked[:start]) and re.match(r"\s*\d", masked[end:]):
                continue  # Numeric ranges remain ranges even with irregular spaces.
            if not (before.isspace() or after.isspace()):
                continue  # Word-internal punctuation is ambiguous.
        if match[0] == ":" and re.search(r"\b(?:ISBN|ISSN|УДК|ББК)\s*$", masked[:start], re.I):
            continue
        missing_before, missing_after = not before.isspace(), not after.isspace()
        if not (missing_before or missing_after):
            continue
        side = "перед разделителем" if missing_before else "после разделителя"
        description = (f"с обеих сторон разделителя «{match[0]}» отсутствуют пробелы."
                       if missing_before and missing_after else
                       f"{side} «{match[0]}» отсутствует пробел.")
        instruction = (f"Добавьте пробелы с обеих сторон «{match[0]}»"
                       if missing_before and missing_after else
                       f"Добавьте пробел {'перед' if missing_before else 'после'} «{match[0]}»")
        replacement = (" " if missing_before else "") + text[start:end] + (" " if missing_after else "")
        add(start, end, description, instruction, replacement)
    return [(actual, expected) for _, actual, expected in sorted(problems, key=lambda p: p[0])]
