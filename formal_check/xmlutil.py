"""Small OOXML helpers, also usable with stdlib-independent lxml elements."""
from lxml import etree

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}


def q(name):
    prefix, local = name.split(":")
    return "{%s}%s" % (NS[prefix], local)


def xp(node, path):
    return etree.XPath(path, namespaces=NS)(node) if node is not None else []


def attr(node, name="val", default=None):
    return node.get(q("w:" + name), default) if node is not None else default


def child(node, path):
    return node.find(path, NS) if node is not None else None


def val(node, path, default=None):
    return attr(child(node, path), default=default)


def on(node):
    return attr(node, default="true") not in ("0", "false", "off")


def integer(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse(data):
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))
