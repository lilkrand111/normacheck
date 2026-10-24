"""Allowlisted property edits, bound to a single formal_check inspection."""
import logging
from dataclasses import replace

from formal_check import subsection_layout
from formal_check.constraints import PAGE_SETUP
from formal_check.formatting import (INDENT_KEYS, blank_line_spacing_values,
                                    paragraph_edge_spacing_values, section_indentation_values)
from formal_check.model import Paragraph
from formal_check.xmlutil import attr, child, integer, q, xp

LOG = logging.getLogger("formal_fix")
PAGE_PROPERTIES = {name: (tag, attribute) for name, tag, attribute, *_ in PAGE_SETUP}
ALLOWED = {
    ("F03", "paragraph_line_spacing", "line_spacing"),
    ("F04", "paragraph_first_line_indent", "first_line_indent"),
    ("F05", "paragraph_first_line_indent", "first_line_indent"),
    ("F05", "section_heading_indentation", "heading_indentation"),
    ("F05", "subsection_layout", "subsection_indentation"),
    ("F07", "heading_gap_spacing", "heading_gap"),
    ("F06", "paragraph_first_line_indent", "heading_first_line_indent"),
    ("F06", "paragraph_side_indent", "heading_left_indent"),
    ("F06", "paragraph_side_indent", "heading_right_indent"),
    *(('F06', 'paragraph_alignment', name) for name in (
        "text_alignment", "heading_alignment", "unnumbered_heading_alignment")),
    *(("F01", "section_property", name) for name in PAGE_PROPERTIES),
}
UNSAFE_TAGS = {q(name) for name in (
    "w:ins", "w:del", "w:moveFrom", "w:moveTo", "w:conflictIns", "w:conflictDel",
    "w:object", "w:txbxContent", "w:altChunk", "w:drawing", "w:pict", "w:sym",
    "mc:AlternateContent",
)}


def target_xml(diagnostic):
    return diagnostic.target.xml if isinstance(diagnostic.target, Paragraph) else diagnostic.target


def _gap_nodes(node):
    nodes = (node.getprevious(), node, node.getnext())
    if any(x is None or x.tag != q("w:p") for x in nodes):
        raise ValueError("Нарушена структура промежутка после заголовка.")
    return nodes


def _gap_values(model, node):
    heading, blank, following = _gap_nodes(node)
    return (paragraph_edge_spacing_values(model.resolver.paragraph(heading), "after"),
            blank_line_spacing_values(model.resolver.paragraph(blank)),
            paragraph_edge_spacing_values(model.resolver.paragraph(following), "before"))


def _unsafe(node):
    for item in node.iter():
        if item.tag in UNSAFE_TAGS:
            return True
        if isinstance(item.tag, str) and item.tag.startswith(q("w:")) and item.tag.endswith("Change"):
            return True
    return False


def _eligible(model, diagnostic):
    node = target_xml(diagnostic)
    if node.getroottree().getroot() is not model.document.element:
        return False
    if _unsafe(node):
        return False
    for ancestor in node.iterancestors():
        if ancestor.tag in UNSAFE_TAGS:
            return False
        if ancestor.tag == q("w:sdt"):
            lock = child(ancestor, "w:sdtPr/w:lock")
            if lock is not None and attr(lock) != "unlocked":
                return False
    p = diagnostic.target
    if isinstance(p, Paragraph):
        if p.uncertain or not p.num_known or p.table is not None or p.in_toc:
            return False
        if any(_unsafe(s) for s in model.resolver.p_styles(node)):
            return False
        if diagnostic.kind == "section_heading_indentation":
            props = model.resolver.paragraph(node)
            if any(key in props for key in INDENT_KEYS if key.endswith("Chars")):
                return False
        if diagnostic.kind == "heading_gap_spacing":
            if (p.kind != "blank" or p.num_id or p.props.get("page_break_before") or
                    any(r.text.strip() for r in p.runs) or
                    xp(node, ".//w:br | .//w:cr | .//w:fldChar | .//w:fldSimple | "
                       ".//w:instrText | ./w:pPr/w:sectPr")):
                return False
            nodes = _gap_nodes(node)
            members = {item.xml: item for item in model.paragraphs if item.xml in nodes}
            if len(members) != 3:
                return False
            if members[nodes[2]].props.get("page_break_before") or any(
                    xp(item, './/w:br[@w:type="page" or @w:type="column"] | ./w:pPr/w:sectPr')
                    for item in nodes):
                return False
            # A gap is one operation: if any of its three paragraphs is unsafe,
            # do not change just the blank and leave an incomplete correction.
            if any(not _eligible(model, replace(diagnostic, kind="gap_member", target=members[item]))
                   for item in nodes):
                return False
        num_id, level = model.resolver.num_info(node)
        if num_id:
            lvl, _ = model.resolver.level(num_id, level)
            if lvl is None or _unsafe(lvl):
                return False
    return True


def current_value(model, diagnostic, node=None):
    """Read the effective value, including inheritance, without cached props."""
    if node is None:
        node = target_xml(diagnostic)
    if diagnostic.kind == "section_property":
        tag, attribute = PAGE_PROPERTIES[diagnostic.property]
        return integer(attr(child(node, tag), attribute))
    if diagnostic.kind == "section_heading_indentation":
        return section_indentation_values(model.resolver.heading_paragraph(node))
    if diagnostic.kind == "subsection_layout":
        return subsection_layout.current_value(model, diagnostic.target, node)
    if diagnostic.kind == "heading_gap_spacing":
        return _gap_values(model, node)
    props = model.resolver.paragraph(node)
    if diagnostic.kind == "paragraph_side_indent":
        left = diagnostic.property == "heading_left_indent"
        side, alias = ("left", "start") if left else ("right", "end")
        if any(key in props for key in (side + "Chars", alias + "Chars")):
            return None
        return props.get(alias, props.get(side, 0))
    if diagnostic.kind == "paragraph_line_spacing":
        return props["line"], props["lineRule"]
    if diagnostic.kind == "paragraph_first_line_indent":
        if "firstLineChars" in props or "hangingChars" in props:
            return None
        return -props["hanging"] if "hanging" in props else props.get("firstLine", 0)
    if diagnostic.kind == "paragraph_alignment":
        value = props["alignment"]
        if value in ("start", "end"):
            return "left" if (value == "start") != props.get("bidi", False) else "right"
        return value
    raise ValueError("Неподдерживаемая операция исправления.")


def build_plan(inspection):
    """Deduplicate by XML identity, never by the public fragment_start."""
    selected, conflicts = {}, set()
    for d in inspection.diagnostics:
        if d.status != "error" or (d.rule_id, d.kind, d.property) not in ALLOWED:
            continue
        if not _eligible(inspection.model, d):
            LOG.info("%s: пропущено %s — неоднозначный или неподдерживаемый объект.",
                     d.rule_id, d.property)
            continue
        key = (target_xml(d), d.kind, d.property)
        if key in selected and (selected[key].actual != d.actual or selected[key].expected != d.expected):
            conflicts.add(key)
        else:
            selected[key] = d
    for key in conflicts:
        LOG.warning("Пропущены конфликтующие исправления свойства %s.", key[2])
    return [d for key, d in selected.items() if key not in conflicts]


def apply_plan(model, plan):
    # Validate every precondition before mutating any target.
    for d in plan:
        if current_value(model, d) != d.actual:
            raise ValueError(f"{d.rule_id}: исходное значение {d.property} изменилось.")
    for d in plan:
        node = target_xml(d)
        if d.kind == "section_property":
            tag, attribute = PAGE_PROPERTIES[d.property]
            child(node, tag).set(q("w:" + attribute), str(d.expected))
            if d.property in ("page_width", "page_height"):
                child(node, "w:pgSz").set(q("w:orient"), "portrait")
        elif d.kind == "heading_gap_spacing":
            nodes = _gap_nodes(node)
            current = _gap_values(model, node)
            names = (("after", "afterLines", "afterAutospacing"),
                     ("line", "lineRule", "before", "after", "beforeLines", "afterLines",
                      "beforeAutospacing", "afterAutospacing"),
                     ("before", "beforeLines", "beforeAutospacing"))
            for member, keys, actual, expected in zip(nodes, names, current, d.expected):
                if actual == expected:
                    continue
                spacing = member.get_or_add_pPr().get_or_add_spacing()
                for name, value in zip(keys, expected):
                    spacing.set(q("w:" + name), str(int(value)) if isinstance(value, bool) else str(value))
        else:
            pp = node.get_or_add_pPr()
            if d.kind == "paragraph_line_spacing":
                spacing = pp.get_or_add_spacing()
                spacing.set(q("w:line"), str(d.expected[0]))
                spacing.set(q("w:lineRule"), d.expected[1])
            elif d.kind == "paragraph_first_line_indent":
                ind = pp.get_or_add_ind()
                ind.attrib.pop(q("w:hanging"), None)
                ind.set(q("w:firstLine"), str(d.expected))
            elif d.kind == "paragraph_side_indent":
                props = model.resolver.paragraph(node)
                side, alias = (("left", "start") if d.property == "heading_left_indent"
                               else ("right", "end"))
                ind = pp.get_or_add_ind()
                ind.set(q("w:" + side), str(d.expected))
                if alias in props:
                    ind.set(q("w:" + alias), str(d.expected))
            elif d.kind == "paragraph_alignment":
                pp.get_or_add_jc().set(q("w:val"), d.expected)
            elif d.kind == "section_heading_indentation":
                # Keep numbering identity and level; make the existing assignment
                # explicit only where Word otherwise ignores local indents.
                num_id, level = model.resolver.num_info(node)
                props = model.resolver.paragraph(node)
                if model.resolver.heading_uses_style_indents(node):
                    num = pp.get_or_add_numPr()
                    num.get_or_add_ilvl().val = level
                    num.get_or_add_numId().val = int(num_id)
                left, right, hanging, _ = d.expected
                # Word for Mac displays left - hanging for both automatic
                # numbering and headings whose number is typed as text.
                xml_left = left + hanging
                ind = pp.get_or_add_ind()
                ind.attrib.pop(q("w:firstLine"), None)
                for name, value in (("left", xml_left), ("right", right), ("hanging", hanging)):
                    ind.set(q("w:" + name), str(value))
                # Logical attributes override their physical aliases, including
                # when inherited from a shared style or numbering definition.
                for name, value in (("start", xml_left), ("end", right)):
                    if name in props:
                        ind.set(q("w:" + name), str(value))
            elif d.kind == "subsection_layout":
                from docx.oxml import OxmlElement
                num_id, level = model.resolver.num_info(node)
                props = model.resolver.paragraph(node)
                if model.resolver.heading_uses_style_indents(node):
                    num = pp.get_or_add_numPr()
                    num.get_or_add_ilvl().val = level
                    num.get_or_add_numId().val = int(num_id)
                left, right, hanging, _ = d.expected[0]
                column = left + hanging
                ind = pp.get_or_add_ind()
                ind.attrib.pop(q("w:firstLine"), None)
                for name, value in (("left", column), ("right", right), ("hanging", hanging)):
                    ind.set(q("w:" + name), str(value))
                for name, value in (("start", column), ("end", right)):
                    if name in props:
                        ind.set(q("w:" + name), str(value))
                tabs = pp.get_or_add_tabs()
                # Clear earlier inherited stops, otherwise the separator tab
                # can land before the continuation column.
                positions = {position for position in props.get("tabs", {}) if position <= column}
                positions.add(column)
                for tab in list(tabs):
                    if integer(attr(tab, "pos")) in positions:
                        tabs.remove(tab)
                for position in sorted(positions):
                    tab = OxmlElement("w:tab")
                    tab.set(q("w:pos"), str(position))
                    tab.set(q("w:val"), "left" if position == column else "clear")
                    tabs.append(tab)
                subsection_layout.apply_separator(model, d.target)
        LOG.info("%s: %s: %s → %s.", d.rule_id, d.property, d.actual, d.expected)
