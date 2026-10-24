"""Strict public result contract and deterministic finding collection."""
from dataclasses import replace

from .model import Diagnostic
from .standards import STANDARD_REFERENCES

STANDARD = "СТП 01–2017"
RULE_IDS = tuple(f"F{i:02}" for i in range(1, 37) if i not in (11, 13, 14, 17, 21))
STATUSES = {"error", "recommendation", "review"}


def validate_report(result):
    if not isinstance(result, dict) or set(result) != {"standard", "findings"}:
        raise ValueError("Некорректная структура отчёта.")
    if result["standard"] != STANDARD or not isinstance(result["findings"], list):
        raise ValueError("Некорректный стандарт или findings.")
    for finding in result["findings"]:
        if not isinstance(finding, dict) or set(finding) != {
                "rule_id", "standard_reference", "status", "locations", "actual", "expected"}:
            raise ValueError("Некорректная структура замечания.")
        if not all(isinstance(finding[k], str) and finding[k]
                   for k in ("rule_id", "standard_reference", "status", "actual", "expected")):
            raise ValueError("Значения замечания должны быть непустыми строками.")
        if finding["rule_id"] not in RULE_IDS or finding["status"] not in STATUSES:
            raise ValueError("Недопустимый rule_id или status.")
        if finding["standard_reference"] != STANDARD_REFERENCES[finding["rule_id"]]:
            raise ValueError("Основание СТП не соответствует rule_id.")
        if not isinstance(finding["locations"], list) or not finding["locations"]:
            raise ValueError("locations должен быть непустым массивом.")
        for loc in finding["locations"]:
            if not isinstance(loc, dict) or "section" not in loc or not set(loc) <= {"section", "fragment_start", "property", "context"}:
                raise ValueError("Некорректное место замечания.")
            if not all(isinstance(v, str) for v in loc.values()):
                raise ValueError("Значения места замечания должны быть строками.")
            if not any(loc.get(key, '').strip() for key in ('fragment_start', 'property', 'context')):
                raise ValueError("Нужно указать fragment_start, property или context.")
            if not any(loc.get(key, '').strip() for key in ('fragment_start', 'section', 'context')):
                raise ValueError("Место замечания не содержит ориентира в документе.")
            if any(key in loc and not loc[key].strip() for key in ('fragment_start', 'property', 'context')):
                raise ValueError("Поля места замечания должны быть непустыми.")
    return result


class Reporter:
    def __init__(self):
        self.findings = []
        self.diagnostics = []
        self.seen = set()
        self.rule = ""

    def add(self, target, actual, expected, status="error", prop=None, *, diagnostic=None):
        if diagnostic is not None:
            if not isinstance(diagnostic, Diagnostic):
                raise TypeError("Внутренняя диагностика должна иметь тип Diagnostic.")
            # Identical public excerpts can belong to distinct XML paragraphs.
            self.diagnostics.append(replace(diagnostic, rule_id=self.rule, status=status))
        if isinstance(target, list):
            locations = [p.location(prop) if hasattr(p, "location") else p for p in target]
        else:
            locations = [target.location(prop) if hasattr(target, "location") else target]
        key = (self.rule, status, tuple(tuple(sorted(x.items())) for x in locations), actual, expected)
        if key not in self.seen:
            self.seen.add(key)
            self.findings.append({"rule_id": self.rule,
                                  "standard_reference": STANDARD_REFERENCES[self.rule],
                                  "status": status, "locations": locations,
                                  "actual": actual, "expected": expected})

    def result(self):
        return validate_report({"standard": STANDARD, "findings": self.findings})
