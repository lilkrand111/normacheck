"""Conservative sentence-continuation hints used only by F04."""
import re

from .reader import paragraph_runs, visible_run_text
from .xmlutil import q


def paragraph_text_with_math(p):
    """Read punctuation in document order, including inside editable equations."""
    if not p.math:
        return p.text
    visible = {node for node, run in zip(paragraph_runs(p.xml), p.runs)
               if not run.props.get("hidden")}
    pieces = []
    for node in p.xml.iter():
        if node in visible:
            pieces.append(visible_run_text(node))
        elif node.tag == q("m:t") and not any(
                a.tag in (q("w:del"), q("w:moveFrom"), q("w:txbxContent"))
                for a in node.iterancestors()):
            pieces.append(node.text or "")
    return "".join(pieces)


def sentence_ended(text):
    return text.rstrip().rstrip('»”\"’\')]}').rstrip().endswith((".", "!", "?", "…"))


def formula_continuations(model):
    """Map following prose indices to 'continuation' or 'review'.

    No classifications are changed: F03, F06 and the remaining rules keep
    checking this prose. Only genuinely empty body paragraphs are skipped.
    """
    result = {}
    previous = lead = None
    table_boundaries = {table.before for table in model.tables}
    for p in model.paragraphs:
        if p.index in table_boundaries:
            previous = lead = None
        if p.table is None and p.kind == "blank":
            continue
        if (p.table is None and p.kind == "text" and previous is not None and
                previous.table is None and previous.kind == "formula" and
                not previous.uncertain and not p.uncertain and
                p.region in ("main", "appendix") and
                (p.region, p.section, p.appendix) ==
                (previous.region, previous.section, previous.appendix)):
            text = paragraph_text_with_math(previous).strip()
            # A right-hand number such as (1.6) is not sentence punctuation.
            # Preserve a comma/period placed after the number as well.
            text = re.sub(r"\s*\((?:[А-ЯЁA-Z]\.\d+|\d+(?:\.\d+)?)\)\s*([.,;:!?…]?)\s*$",
                          r"\1", text)
            first = (p.display_text or p.text).lstrip().lstrip('«“„\"([{').lstrip()[:1]
            if not (sentence_ended(text) and first.isupper()):
                has_lead = (lead is not None and lead.table is None and lead.kind == "text" and
                            not lead.uncertain and (lead.region, lead.section, lead.appendix) ==
                            (p.region, p.section, p.appendix))
                confident = (previous.math and not previous.images and first.islower() and
                             not sentence_ended(text) and has_lead and
                             not sentence_ended(paragraph_text_with_math(lead)))
                result[p.index] = "continuation" if confident else "review"
        lead, previous = previous, p
    return result
