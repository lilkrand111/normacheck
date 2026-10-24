"""A hanging indent and one tab align a subsection's title and continuation.

The 12.5 mm number position follows the agreed heading layout. The text tab is
an implementation choice, not an additional numerical requirement from STP.
It leaves room for the rendered decimal label without editing its definition.
"""
import math
import re

from .constraints import mm_to_twips
from .formatting import section_indentation_values
from .xmlutil import q, val, xp

MANUAL_PREFIX = re.compile(r"^(\d+\.\d+\.?)([ \t\u00a0]+)(?=\S)")
DECIMAL_LABEL = re.compile(r"\d+\.\d+\.?")


def visible_text(node):
    from .reader import paragraph_runs, visible_run_text
    return "".join(visible_run_text(r) for r in paragraph_runs(node))


def separator_edit(model, p, node=None, text=None):
    """Return only the whitespace range after the number and its replacement."""
    node = p.xml if node is None else node
    text = visible_text(node) if text is None else text
    num_id, level = model.resolver.num_info(node)
    if num_id:
        lvl, _ = model.resolver.level(num_id, level)
        suffix = val(lvl, "w:suff", "tab")
        if suffix not in ("tab", "space", "nothing") or not text.strip():
            return None
        leading = len(text) - len(text.lstrip(" \t\u00a0"))
        return 0, leading, "" if suffix == "tab" else "\t"
    match = MANUAL_PREFIX.match(text)
    if not match:
        return None
    return match.start(2), match.end(2), "\t"


def expected_text(model, p, node=None):
    # With no node argument this is the planned text from the original model,
    # not text read back from already-mutated XML.
    text = p.text if node is None else visible_text(node)
    edit = separator_edit(model, p, node, text=text)
    if edit is None:
        return text
    start, end, replacement = edit
    return text[:start] + replacement + text[end:]


def target(model, p):
    label = p.label if p.num_id else (MANUAL_PREFIX.match(p.text)[1]
                                     if MANUAL_PREFIX.match(p.text) else "")
    props = model.resolver.heading_paragraph(p.xml)
    if (not p.num_known or not DECIMAL_LABEL.fullmatch(label) or len(label) > 7 or
            section_indentation_values(props) is None or props.get("bidi") or
            separator_edit(model, p) is None or p.math or
            any(r.props.get("hidden") and r.text for r in p.runs) or
            xp(p.xml, ".//w:hyperlink | .//w:fldChar | .//w:fldSimple | .//w:instrText | "
               './/w:br[@w:type="page" or @w:type="column"]')):
        return None
    if p.num_id:
        lvl, _ = model.resolver.level(p.num_id, p.num_level)
        if val(lvl, "w:lvlJc", "left") not in ("left", "start"):
            return None
    runs = [p.num_props] if p.num_id else [r.props for r in p.runs if r.text.strip()]
    size = max([r.get("size", 14) for r in runs] or [14])
    times = bool(runs) and all(r.get("font_ascii", "").casefold() == "times new roman" for r in runs)
    # Times decimal digits have half-em advances; dots have quarter-em advances.
    # For other fonts use a deliberately wider reserve. The tab, rather than
    # the estimate itself, determines the final exact text column.
    em = sum(0.25 if c == "." else 0.5 for c in label) if times else len(label)
    hanging_mm = max(10, math.ceil((em * size * 25.4 / 72 + 2.5) / 2.5) * 2.5)
    if hanging_mm > 40:
        return None
    return (mm_to_twips(12.5), 0, mm_to_twips(hanging_mm), 0), True, True


def current_value(model, p, node=None):
    node = p.xml if node is None else node
    props = model.resolver.heading_paragraph(node)
    desired = target(model, p)
    if desired is None:
        return None
    left, _, hanging, _ = desired[0]
    text_column = left + hanging
    tabs = props.get("tabs", {})
    tab_ok = (tabs.get(text_column) in (("left", "none"), ("start", "none")) and
              not any(position < text_column for position in tabs))
    return section_indentation_values(props), visible_text(node) == expected_text(model, p, node), tab_ok


def apply_separator(model, p):
    """Replace whitespace only, preserving runs, bookmarks and explicit breaks."""
    from docx.oxml import OxmlElement
    from .reader import paragraph_runs
    node = p.xml
    start, end, replacement = separator_edit(model, p)
    text = visible_text(node)
    if text[start:end] == replacement:
        return
    assert not text[start:end].strip(), "Only number/title separator whitespace may change"
    tokens = []
    offset = 0
    for run in paragraph_runs(node):
        for item in run:
            if item.tag == q("w:t"):
                content = item.text or ""
            elif item.tag in (q("w:tab"), q("w:br"), q("w:cr")):
                content = "\t" if item.tag == q("w:tab") else "\n"
            else:
                continue
            tokens.append((item, offset, content))
            offset += len(content)
    inserted = False
    for item, offset, content in tokens:
        if inserted and offset >= end:
            break
        if offset + len(content) <= start or offset > end:
            continue
        a, b = max(0, start - offset), min(len(content), end - offset)
        if a > b:
            continue
        parent = item.getparent()
        index = parent.index(item)
        before, after = content[:a], content[b:]
        if item.tag == q("w:t"):
            item.text = before + after
            if not inserted and replacement:
                item.text = before
                tab = OxmlElement("w:tab")
                parent.insert(index + 1, tab)
                if after:
                    tail = OxmlElement("w:t")
                    tail.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                    tail.text = after
                    parent.insert(index + 2, tail)
        else:
            parent.remove(item)
            if not inserted and replacement:
                parent.insert(index, OxmlElement("w:tab"))
        inserted = True
    if not inserted:
        raise ValueError("Could not locate heading separator in its XML runs")
    assert visible_text(node) == text[:start] + replacement + text[end:]
