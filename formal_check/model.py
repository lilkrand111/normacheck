"""Internal document model. XML elements are read-only throughout the checker."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class Run:
    text: str
    props: Dict[str, Any]


@dataclass
class Paragraph:
    index: int
    xml: Any
    text: str
    runs: List[Run]
    props: Dict[str, Any]
    section_index: int = 1
    table: Optional[int] = None
    row: Optional[int] = None
    col: Optional[int] = None
    in_toc: bool = False
    label: str = ""
    num_id: Optional[str] = None
    num_level: int = 0
    num_props: Dict[str, Any] = field(default_factory=dict)
    num_known: bool = True
    math: List[Any] = field(default_factory=list)
    images: int = 0
    urls: List[str] = field(default_factory=list)
    region: str = "front"
    kind: str = "text"
    heading_number: Tuple[int, ...] = ()
    heading_title: str = ""
    heading_level: int = 0
    section: str = ""
    appendix: str = ""
    uncertain: bool = False
    table_label: str = ""
    location_fragment: str = ""
    location_context: str = ""

    @property
    def numbered_text(self):
        return (self.label + " " + self.text).strip() if self.label else self.text

    @property
    def display_text(self):
        return "".join(r.text.upper() if r.props.get("caps") else r.text
                       for r in self.runs if not r.props.get("hidden"))

    def location(self, prop=None):
        section = self.section
        if self.table is not None:
            section = f"{section + ', ' if section else ''}Таблица {self.table_label or self.table}, строка {self.row}, столбец {self.col}"
        result = {"section": section}
        if self.math and self.location_fragment and (self.kind == "formula" or not self.text.strip()):
            result["fragment_start"] = self.location_fragment
        elif self.text.strip():
            fragment = self.text[:160]
            result["fragment_start"] = fragment if fragment.strip() else self.text.lstrip()[:160]
        if self.location_context:
            result["context"] = self.location_context
        if self.math and (self.kind == "formula" or not self.text.strip()):
            result["property"] = prop or "formula"
        elif prop or not self.text.strip():
            result["property"] = prop or "paragraph"
        if not result.get("fragment_start") and not result.get("context"):
            raise ValueError("Для объекта без текста не подготовлен контекст места замечания.")
        return result


@dataclass
class Cell:
    row: int
    col: int
    span: int
    continuation: bool
    xml: Any
    paragraphs: List[Paragraph]

    @property
    def text(self):
        return "\n".join(p.text for p in self.paragraphs).strip()


@dataclass
class Table:
    index: int
    xml: Any
    cells: List[Cell]
    before: int
    kind: str = "unknown"
    caption: Optional[Paragraph] = None


@dataclass
class Model:
    document: Any
    paragraphs: List[Paragraph]
    tables: List[Table]
    resolver: Any
    parts: Dict[str, Any]
    headings: List[Paragraph] = field(default_factory=list)
    toc_entries: List[Paragraph] = field(default_factory=list)
    bibliography: List[Paragraph] = field(default_factory=list)
    appendices: List[Paragraph] = field(default_factory=list)
    main_complete: bool = False
    opaque_objects: bool = False

    @property
    def body(self):
        return [p for p in self.paragraphs if p.table is None]


@dataclass(frozen=True)
class Diagnostic:
    """Typed property violation bound to this inspection's live XML objects.

    Not a public JSON record or an instruction received from a user/LLM.
    Rule and status are filled by Reporter, before public finding deduplication.
    """
    kind: str
    target: Any
    property: str
    actual: Any
    expected: Any
    rule_id: str = ""
    status: str = "error"


@dataclass
class Inspection:
    model: Model
    report: Dict[str, Any]
    diagnostics: List[Diagnostic]
