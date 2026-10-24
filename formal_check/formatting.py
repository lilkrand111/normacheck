"""Resolve the supported OOXML formatting cascade without modifying the file."""
import logging
import re

from .xmlutil import attr, child, integer, on, q, val, xp

LOG = logging.getLogger("formal_check")
TOGGLES = {"b": "bold", "i": "italic", "caps": "caps", "smallCaps": "small_caps",
           "strike": "strike", "vanish": "hidden"}
INDENT_KEYS = ("left", "right", "start", "end", "firstLine", "hanging",
               "leftChars", "rightChars", "startChars", "endChars",
               "firstLineChars", "hangingChars")
NUMBERING_INDENT_KEYS = ("left", "start", "firstLine", "hanging",
                         "leftChars", "startChars", "firstLineChars", "hangingChars")


def blank_line_spacing_values(props):
    """Resolved vertical spacing of an existing blank paragraph."""
    return (props["line"], props["lineRule"],
            *(props.get(key, 0) for key in ("before", "after", "beforeLines", "afterLines")),
            *(props.get(key) in ("1", "true", "on")
              for key in ("beforeAutospacing", "afterAutospacing")))


def paragraph_edge_spacing_values(props, edge):
    """One side of a paragraph; the other side and line spacing stay independent."""
    return (props.get(edge, 0), props.get(edge + "Lines", 0),
            props.get(edge + "Autospacing") in ("1", "true", "on"))


def section_indentation_values(props):
    """Word for Mac dialog coordinates, retaining the special-indent mode.

    The dialog's left field is the first-line position: OOXML left minus
    hanging (or plus firstLine). This also applies when the number is typed
    into the heading text, not only when it comes from automatic numbering.
    """
    if any(key in props for key in INDENT_KEYS if key.endswith("Chars")):
        return None
    left = props.get("start", props.get("left", 0))
    right = props.get("end", props.get("right", 0))
    hanging = props.get("hanging", 0)
    first_line = props.get("firstLine", 0)
    special = -hanging if "hanging" in props else first_line
    return left + special, right, hanging, first_line


class Resolver:
    def __init__(self, styles, numbering=None, theme=None):
        self.styles = {attr(s, "styleId"): s for s in xp(styles, "./w:style")}
        self.default_style = next((attr(s, "styleId") for s in self.styles.values()
                                   if attr(s, "type") == "paragraph" and attr(s, "default") == "1"), None)
        self.default_p = child(styles, "w:docDefaults/w:pPrDefault/w:pPr")
        self.default_r = child(styles, "w:docDefaults/w:rPrDefault/w:rPr")
        self.nums = {attr(n, "numId"): n for n in xp(numbering, "./w:num")}
        self.abstract = {attr(n, "abstractNumId"): n for n in xp(numbering, "./w:abstractNum")}
        self.theme = {}
        for family in ("major", "minor"):
            for script in ("latin", "ea", "cs"):
                fonts = xp(theme, f".//a:{family}Font/a:{script}")
                if fonts:
                    self.theme[(family, script)] = fonts[0].get("typeface") or None
        self._warnings = set()

    def warn(self, message):
        if message not in self._warnings:
            LOG.warning(message)
            self._warnings.add(message)

    def chain(self, style_id):
        result, seen = [], set()
        while style_id and style_id in self.styles:
            if style_id in seen:
                self.warn("FORMAT: цикл наследования стилей; зависимые свойства не гарантированы.")
                break
            seen.add(style_id)
            style = self.styles[style_id]
            result.append(style)
            style_id = val(style, "w:basedOn")
        return list(reversed(result))

    def p_styles(self, p):
        style_id = val(p, "w:pPr/w:pStyle", self.default_style)
        return self.chain(style_id)

    def p_sources(self, p):
        return [self.default_p] + [child(s, "w:pPr") for s in self.p_styles(p)]

    def num_info(self, p):
        num_id, level = None, 0
        for source in self.p_sources(p) + [child(p, "w:pPr")]:
            num = child(source, "w:numPr")
            if num is not None:
                num_id = val(num, "w:numId", num_id)
                level = integer(val(num, "w:ilvl"), level)
        if num_id and num_id != "0" and child(p, "w:pPr/w:numPr/w:ilvl") is None:
            # Outline numbering may associate a paragraph style with a level
            # even when numPr on the style has no explicit ilvl.
            style_ids = [attr(s, "styleId") for s in reversed(self.p_styles(p))]
            for style_id in style_ids:
                associated = [i for i in range(9)
                              if val(self.level(num_id, i)[0], "w:pStyle") == style_id]
                if associated:
                    level = associated[0]
                    break
        return (None, 0) if num_id == "0" else (num_id, level)

    def level(self, num_id, level, seen=None):
        seen = set() if seen is None else seen
        if num_id in seen:
            return None, None
        seen.add(num_id)
        num = self.nums.get(num_id)
        abstract = self.abstract.get(val(num, "w:abstractNumId"))
        # A numbering style can point to a different num definition.
        link = val(abstract, "w:numStyleLink")
        if link:
            linked_id = None
            for style in self.chain(link):
                linked_id = val(style, "w:pPr/w:numPr/w:numId", linked_id)
            if linked_id:
                return self.level(linked_id, level, seen)
        override = next((n for n in xp(num, "./w:lvlOverride")
                         if integer(attr(n, "ilvl")) == level), None)
        lvl = child(override, "w:lvl")
        if lvl is None:
            lvl = next((n for n in xp(abstract, "./w:lvl")
                        if integer(attr(n, "ilvl")) == level), None)
        start = integer(val(override, "w:startOverride"))
        return lvl, start

    def apply_p(self, result, source):
        if source is None:
            return
        for tag, key in (("jc", "alignment"), ("outlineLvl", "outline"),
                         ("textAlignment", "text_alignment")):
            value = val(source, "w:" + tag)
            if value is not None:
                result[key] = integer(value) if key == "outline" else value
        for tag, key in (("pageBreakBefore", "page_break_before"), ("keepNext", "keep_next"),
                         ("contextualSpacing", "contextual_spacing"), ("bidi", "bidi")):
            node = child(source, "w:" + tag)
            if node is not None:
                result[key] = on(node)
        ind = child(source, "w:ind")
        if ind is not None:
            for name in INDENT_KEYS:
                value = integer(attr(ind, name))
                if value is not None:
                    result[name] = value
            if attr(ind, "firstLine") is not None and attr(ind, "hanging") is None:
                result.pop("hanging", None)
            if attr(ind, "hanging") is not None:
                result.pop("firstLine", None)
        spacing = child(source, "w:spacing")
        if spacing is not None:
            for name in ("line", "before", "after", "beforeLines", "afterLines"):
                value = integer(attr(spacing, name))
                if value is not None:
                    result[name] = value
            for name in ("lineRule", "beforeAutospacing", "afterAutospacing"):
                value = attr(spacing, name)
                if value is not None:
                    result[name] = value
        tabs = result.setdefault("tabs", {})
        for tab in xp(source, "./w:tabs/w:tab"):
            position = integer(attr(tab, "pos"))
            if attr(tab) == "clear":
                tabs.pop(position, None)
            else:
                tabs[position] = (attr(tab), attr(tab, "leader", "none"))

    def paragraph(self, p, *, style_numbering_indents=False):
        # OOXML defaults, not python-docx's None values.
        result = {"alignment": "left", "firstLine": 0, "left": 0, "right": 0,
                  "line": 240, "lineRule": "auto", "before": 0, "after": 0,
                  "page_break_before": False, "tabs": {}}
        self.apply_p(result, self.default_p)
        sources = self.p_sources(p) + [child(p, "w:pPr")]
        cancelled_at = None
        for i, source in enumerate(sources):
            value = val(source, "w:numPr/w:numId")
            if value is not None:
                cancelled_at = i if value == "0" else None
        num_id, level = self.num_info(p)
        if num_id:
            lvl, _ = self.level(num_id, level)
            self.apply_p(result, child(lvl, "w:pPr"))
        # ISO 29500 cascade: defaults -> numbering -> paragraph style -> direct.
        for i, source in enumerate(sources[1:], 1):
            if i == cancelled_at:
                # Word's numId=0 also suppresses inherited paragraph indents.
                # Apply the cancelling source's own formatting AFTER the reset:
                # an explicit left indent/firstLine/hanging still takes effect.
                for key in INDENT_KEYS:
                    result.pop(key, None)
                result.update(left=0, right=0, firstLine=0)
            preserve_style_indents = style_numbering_indents and i == len(sources) - 1
            inherited_indents = ({key: result[key] for key in NUMBERING_INDENT_KEYS if key in result}
                                 if preserve_style_indents else None)
            self.apply_p(result, source)
            if inherited_indents is not None:
                for key in NUMBERING_INDENT_KEYS:
                    result.pop(key, None)
                result.update(inherited_indents)
        result["style_id"] = val(p, "w:pPr/w:pStyle", self.default_style)
        result["style_name"] = val(self.styles.get(result["style_id"]), "w:name", "")
        return result

    def heading_uses_style_indents(self, p):
        """Whether Word's style-linked heading compatibility applies to p."""
        num_id, level = self.num_info(p)
        lvl, _ = self.level(num_id, level) if num_id else (None, None)
        style_ids = {attr(style, "styleId") for style in self.p_styles(p)}
        return (num_id is not None and val(lvl, "w:pStyle") in style_ids and
                child(p, "w:pPr/w:numPr") is None)

    def heading_paragraph(self, p):
        """Word's paragraph-dialog indents for style-linked outline numbering.

        Verified against Word for Mac on the customer's document: when a level
        is linked back to the paragraph style and numPr is inherited, Word
        presents that style's left/special indents even if the paragraph carries
        another ind element. Explicit numbering and right indents stay direct.
        Keep this compatibility case local to F05, not the general cascade.
        """
        return self.paragraph(p, style_numbering_indents=self.heading_uses_style_indents(p))

    def apply_r(self, result, source, style=False):
        if source is None:
            return
        for tag, key in TOGGLES.items():
            node = child(source, "w:" + tag)
            if node is not None:
                enabled = on(node)
                if style:
                    if enabled:
                        result[key] = not result.get(key, False)
                else:
                    result[key] = enabled
        for tag, key in (("sz", "size"), ("szCs", "size_cs")):
            size = integer(val(source, "w:" + tag))
            if size is not None:
                result[key] = size / 2
        underline = child(source, "w:u")
        if underline is not None:
            result["underline"] = attr(underline, default="single") not in ("none", "0", "false")
        vert = val(source, "w:vertAlign")
        if vert is not None:
            result["vertical"] = vert
        fonts = child(source, "w:rFonts")
        if fonts is not None:
            for script, theme_attr in (("ascii", "asciiTheme"), ("hAnsi", "hAnsiTheme"),
                                       ("eastAsia", "eastAsiaTheme"), ("cs", "cstheme")):
                literal, theme_value = attr(fonts, script), attr(fonts, theme_attr)
                if literal is not None or theme_value is not None:
                    if theme_value is not None:
                        family = "major" if theme_value.startswith("major") else "minor"
                        theme_script = "ea" if script == "eastAsia" else "cs" if script == "cs" else "latin"
                        result["font_" + script] = self.theme.get((family, theme_script))
                    else:
                        result["font_" + script] = literal
        for flag in ("cs", "rtl"):
            node = child(source, "w:" + flag)
            if node is not None:
                result[flag] = on(node)

    def run(self, p, r=None, number_level=None):
        result = {"bold": False, "italic": False, "caps": False, "underline": False}
        self.apply_r(result, self.default_r)
        for style in self.p_styles(p):
            self.apply_r(result, child(style, "w:rPr"), style=True)
        if r is not None:
            for style in self.chain(val(r, "w:rPr/w:rStyle")):
                self.apply_r(result, child(style, "w:rPr"), style=True)
            self.apply_r(result, child(r, "w:rPr"))
        if number_level is not None:
            self.apply_r(result, child(p, "w:pPr/w:rPr"))
            self.apply_r(result, child(number_level, "w:rPr"))
        return result


class Numbering:
    def __init__(self, resolver):
        self.resolver = resolver
        self.counters = {}

    def label(self, p):
        num_id, level = self.resolver.num_info(p)
        if not num_id:
            return "", None, 0, {}, True
        lvl, override = self.resolver.level(num_id, level)
        if lvl is None:
            self.resolver.warn("NUMBERING: не удалось разрешить определение автоматической нумерации.")
            return "", num_id, level, {}, False
        counters = self.counters.setdefault(num_id, {})
        start = override if override is not None else integer(val(lvl, "w:start"), 1)
        counters[level] = counters.get(level, start - 1) + 1
        for deeper in list(counters):
            if deeper > level:
                deeper_lvl, _ = self.resolver.level(num_id, deeper)
                restart = integer(val(deeper_lvl, "w:lvlRestart"), deeper)
                if restart and level <= restart - 1:
                    del counters[deeper]
        known = True

        def replace(match):
            nonlocal known
            i = int(match.group(1)) - 1
            definition, initial = self.resolver.level(num_id, i)
            n = counters.get(i, initial if initial is not None else integer(val(definition, "w:start"), 1))
            fmt = val(definition, "w:numFmt", "decimal")
            if child(lvl, "w:isLgl") is not None and on(child(lvl, "w:isLgl")):
                fmt = "decimal"
            if fmt in ("decimal", "decimalZero"):
                return str(n).zfill(2) if fmt == "decimalZero" else str(n)
            if fmt in ("lowerLetter", "upperLetter", "russianLower", "russianUpper"):
                alphabet = "абвгдежзийклмнопрстуфхцчшщъыьэюя" if fmt.startswith("russian") else "abcdefghijklmnopqrstuvwxyz"
                if not 1 <= n <= len(alphabet):
                    known = False
                    return "?"
                letter = alphabet[n - 1]
                return letter.upper() if fmt in ("upperLetter", "russianUpper") else letter
            known = False
            return "?"

        template = val(lvl, "w:lvlText", "")
        label = re.sub(r"%([1-9])", replace, template)
        if not known:
            self.resolver.warn("NUMBERING: неподдерживаемый формат номера; связанные проверки ограничены.")
        return label, num_id, level, self.resolver.run(p, number_level=lvl), known
