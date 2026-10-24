import re
from urllib.parse import urlparse

from . import rule
from .bibliography import bibliography_issues
from .common import LOG, prose

URL = re.compile(r"(?:https?://|www\.)[^\s<>]+", re.I)


def bibliography_items(model):
    items = []
    for p in model.bibliography:
        match = re.match(r"^\s*\[?(\d+)(?:\]|[.)])?\s+(\S.*)", p.numbered_text, re.S)
        if match and p.num_known:
            items.append((int(match[1]), p))
    return items


def citation_numbers(text):
    for match in re.finditer(r"\[([^\]\n]+)\]", text):
        content = re.sub(r"[,;]\s*(?:с\.|c\.|стр\.)\s*\d+(?:[–-]\d+)?\s*$", "", match[1], flags=re.I)
        # Russian decimal endpoints use commas and a semicolon separator.
        # They must never become four source numbers (e.g. [1,60; 2,00]).
        if re.fullmatch(r"\s*[+−-]?\d+,\d+\s*;\s*[+−-]?\d+(?:,\d+)?\s*", content):
            continue
        if re.fullmatch(r"\s*[+−-]?\d+\s*;\s*[+−-]?\d+,\d+\s*", content):
            continue
        if not re.fullmatch(r"\d+(?:\s*[–-]\s*\d+)?(?:\s*[,;]\s*\d+(?:\s*[–-]\s*\d+)?)*", content):
            continue
        numbers = []
        for part in re.split(r"[,;]", content):
            bounds = [int(n) for n in re.split(r"[–-]", part)]
            if len(bounds) == 1:
                numbers.append(bounds[0])
            elif 0 <= bounds[1] - bounds[0] <= 100:
                numbers.extend(range(bounds[0], bounds[1] + 1))
        if numbers:
            yield match.start(), match[0], numbers


@rule("F32")
def citations(model, report):
    items = bibliography_items(model)
    sources = {}
    for number, p in items:
        if number in sources:
            report.add([sources[number], p], f"Номер источника {number} повторяется.",
                       "Присвойте источникам уникальные арабские номера.")
        sources[number] = p
    order = []
    for p in prose(model):
        # Interval syntax in mathematical prose is not evidence of a citation.
        for start, fragment, numbers in citation_numbers(p.text):
            context = p.text[max(0, start - 40):start]
            if re.search(r"(?:отрез[а-я]*|(?:под)?интервал[а-я]*|диапазон[а-я]*|массив[а-я]*)\s*:?\s*$", context, re.I):
                continue
            for number in numbers:
                if number not in order:
                    order.append(number)
                if number not in sources:
                    report.add(p, f"Ссылка {fragment}: источник {number} не найден.",
                               "Укажите существующий номер источника в квадратных скобках.",
                               "error" if items and model.main_complete else "review")
    for number, p in items:
        if number not in order:
            report.add(p, f"Ссылка на источник {number} в распознанном тексте не найдена.",
                       "Добавьте ссылку на источник в тексте. Если это учебный материал по занятиям, "
                       "проверьте применимость исключения из пункта 2.8.4: "
                       "такой источник можно оставить без ссылки и разместить в конце списка.",
                       "review")
    listed_cited = [n for n, _ in items if n in order]
    cited_existing = [n for n in order if n in sources]
    if listed_cited != cited_existing and listed_cited:
        report.add([sources[n] for n in listed_cited],
                   "Порядок источников в списке отличается от порядка первых ссылок.",
                   "Расположите источники по первому появлению ссылок.")
    if model.bibliography and not items:
        LOG.warning("F32: номера источников не распознаны; полнота ссылок не проверена.")


@rule("F33")
def bibliography_punctuation(model, report):
    for p in model.bibliography:
        issues = bibliography_issues(p.text)
        if issues:
            def description(index):
                return "\n".join((f"{i}. " if len(issues) > 1 else "") + issue[index]
                                 for i, issue in enumerate(issues, 1))
            report.add(p, description(0), description(1))


@rule("F34")
def disallowed_sources(model, report):
    for p in model.bibliography:
        urls = p.urls + URL.findall(p.text)
        wikipedia = False
        for url in urls:
            parsed = urlparse(url if "://" in url else "https://" + url)
            hostname = (parsed.hostname or "").lower().rstrip(".")
            if hostname == "wikipedia.org" or hostname.endswith(".wikipedia.org"):
                wikipedia = True
        if wikipedia or re.search(r"\bВикипедия\b", p.text, re.I):
            report.add(p, "В списке источников указана Википедия.",
                       "Замените ссылку на Википедию подходящим первоисточником. "
                       "Википедия не допускается по пункту 2.8.6, примечанию 6.")
        elif re.search(r"\b(?:систем[аы]\s+(?:подсказок|помощи)|встроенн[а-я]+\s+справк[а-я]+)\s+[«\"]?help\b", p.text, re.I):
            report.add(p, "В списке источников указана встроенная справочная система.",
                       "Замените систему подсказок подходящим опубликованным источником. "
                       "Системы подсказок не допускаются по пункту 2.8.6, примечанию 6.")
