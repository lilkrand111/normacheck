"""Human-readable anchors for objects that have no searchable paragraph text."""
from collections import defaultdict

from .xmlutil import q, xp


def _scope(p):
    # Do not borrow context from another section, cell or content-control story.
    return (p.region, p.section, p.section_index, p.table, p.row, p.col, p.xml.getparent())


def _anchor_text(model, p):
    if p.kind == 'formula' or not any(c.isalpha() for c in p.text):
        return ''
    if not (p.math or p.images):
        return p.text.strip()[:160]
    # The ordinary-text reader omits inline equations. Never join words across
    # such an omission and present that artificial sentence as a Word search key.
    segments = ['']
    excluded = {q(name) for name in ('m:oMath', 'w:drawing', 'w:pict', 'w:object',
                                   'w:del', 'w:moveFrom', 'w:txbxContent')}

    def visit(node):
        if node.tag in excluded or (node is not p.xml and node.tag == q('w:p')):
            segments.append('')
            return
        if node.tag == q('w:r') and model.resolver.run(p.xml, node).get('hidden'):
            segments.append('')
            return
        if node.tag == q('w:t'):
            segments[-1] += node.text or ''
        elif node.tag == q('w:tab'):
            segments[-1] += '\t'
        elif node.tag in (q('w:br'), q('w:cr')):
            segments.append('')
        else:
            for child in node:
                visit(child)

    visit(p.xml)
    usable = [text.strip() for text in segments if any(c.isalpha() for c in text)]
    return max(usable, key=len)[:160] if usable else ''


def _math_text(model, p):
    pieces = []
    for node in xp(p.xml, './/m:t'):
        ancestors = list(node.iterancestors())
        if next((a for a in ancestors if a.tag == q('w:p')), None) is not p.xml:
            continue
        if any(a.tag in {q('w:del'), q('w:moveFrom'), q('w:txbxContent')} for a in ancestors):
            continue
        run = next((a for a in ancestors if a.tag == q('m:r')), None)
        if run is not None and not model.resolver.run(p.xml, run).get('hidden'):
            pieces.append(node.text or '')
    # Literal text-node order, not a reconstruction of the equation's layout.
    return ''.join(pieces).strip()[:160]


def prepare_locations(model):
    """Keep search anchors separate from the target's literal source fragment."""
    following, nearest = {}, {}
    anchors = {p.index: _anchor_text(model, p) for p in model.paragraphs}
    for p in reversed(model.paragraphs):
        key = _scope(p)
        following[p.index] = nearest.get(key)
        if anchors[p.index]:
            nearest[key] = p
    previous, positions = {}, defaultdict(int)
    for p in model.paragraphs:
        key = _scope(p)
        before, after = previous.get(key), following[p.index]
        p.location_fragment = _math_text(model, p) if p.math else ''
        p.location_context = ''
        if p.math or not any(c.isalpha() for c in p.text):
            label = ('Формула' if p.math else 'Изображение' if p.images else
                     'Пустой абзац' if not p.text.strip() else 'Абзац')
            group = key, label, before.index if before is not None else None
            positions[group] += 1
            hint = f'{label}: позиция {positions[group]} '
            if before is not None:
                hint += f'после фрагмента «{anchors[before.index]}».'
                if after is not None:
                    hint += f' Далее следует «{anchors[after.index]}».'
            elif after is not None:
                hint += f'перед фрагментом «{anchors[after.index]}».'
            else:
                hint += 'в ячейке.' if p.table is not None else (
                    'в разделе.' if p.section else 'в документе.')
            p.location_context = hint
        if anchors[p.index]:
            previous[key] = p
