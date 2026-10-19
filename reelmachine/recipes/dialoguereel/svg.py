"""The SVG character contract: what a character must contain, and what it may never contain.

Characters are inlined into the page a headless browser renders, and agents write their
own, so `check_svg` is a security boundary. It refuses every `<!` (comments, CDATA, DOCTYPE,
ENTITY) and `<?` textually before parsing: stdlib ElementTree never expands an entity, and
nothing survives that the XML parser drops but the HTML parser reads differently (`<!-->`
closes a comment for HTML, not for XML). Then it walks every element: no scripting or
embedding elements, no animation (the runtime owns motion), no event-handler attributes,
no reference that leaves the document, no two attributes whose names differ only in case.
Tag and attribute names are compared case-insensitively because the HTML parser lowercases
them when the SVG is inlined, and every attribute value is checked as CSS because
presentation attributes (fill, filter, mask, clip-path, ...) are CSS values.

The page never receives the source text: `inline_svg` re-checks it, scopes the ids on the
parsed tree and serializes that tree, so every byte the HTML parser reads was written by
ElementTree (text and attribute values escaped, names from the checked tree).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from xml.etree import ElementTree
from xml.etree.ElementTree import Element

from reelmachine.core.errors import InvalidInput

REQUIRED: tuple[str, ...] = ("body", "eyes-open", "eyes-closed", "mouth-closed", "mouth-open")
OPTIONAL: tuple[str, ...] = (
    "mouth-smile", "eyes-happy", "eyes-surprised", "blush", "arm-left", "arm-right", "sweat", "sparkle",
)
MAX_BYTES = 60_000
VIEW_BOX = "0 0 400 400"

_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"
_FORBIDDEN = {
    name.lower(): name
    for name in ("script", "foreignObject", "iframe", "object", "embed", "audio", "video",
                 "animate", "animateTransform", "animateMotion", "set",
                 # An inlined <style> applies page-wide (it could restyle the other character or the
                 # page) and CSS animations would bypass the runtime; inline style attributes suffice.
                 "style")
}
# Everything else a hand-drawn character can need. An element outside this list (an HTML
# tag that breaks out of foreign content when inlined, an editor's private namespace) is
# refused rather than guessed about.
_ALLOWED = {
    "svg", "g", "defs", "symbol", "use", "title", "desc", "metadata", "switch",
    "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "text", "tspan", "textPath", "a", "image", "marker", "pattern", "clipPath", "mask",
    "linearGradient", "radialGradient", "stop", "filter",
    "feBlend", "feColorMatrix", "feComponentTransfer", "feComposite", "feConvolveMatrix",
    "feDiffuseLighting", "feDisplacementMap", "feDistantLight", "feDropShadow", "feFlood",
    "feFuncA", "feFuncB", "feFuncG", "feFuncR", "feGaussianBlur", "feMerge", "feMergeNode",
    "feMorphology", "feOffset", "fePointLight", "feSpecularLighting", "feSpotLight", "feTile",
    "feTurbulence",
}
_DATA_IMAGE = re.compile(r"^data:image/(png|jpeg);base64,[A-Za-z0-9+/=\s]*$")
_MARKUP_DECL = re.compile(r"<\s*[!?]")
_EXTERNAL_URL = re.compile(r"url\(\s*(?![\"']?\s*#)", re.IGNORECASE)
_CSS_FORBIDDEN = re.compile(r"@import|@font-face|@namespace|javascript:|image-set\(|image\(|\\", re.IGNORECASE)
_JAVASCRIPT = re.compile(r"javascript\s*:", re.IGNORECASE)
_XML_NS = "http://www.w3.org/XML/1998/namespace"
# HTML integration points: the HTML parser reads their children as HTML, so they hold text only
_TEXT_ONLY = {"title", "desc"}
_HINT = "characters are plain SVG shapes; see the SVG character contract in the guide"
_DRAWING_HINT = "drawings are plain SVG shapes: no scripts, HTML or links outside the drawing"


def _refuse(message: str, element: str, path: str, hint: str = _HINT) -> InvalidInput:
    return InvalidInput(message, hint=hint, details={"path": path, "element": element})


def _local(name: str) -> tuple[str, str]:
    """Split ElementTree's `{ns}local` into (ns, local)."""
    if name.startswith("{"):
        ns, _, local = name[1:].partition("}")
        return ns, local
    return "", name


def _check_css(text: str, element: str, path: str, hint: str = _HINT) -> None:
    if _CSS_FORBIDDEN.search(text) or _EXTERNAL_URL.search(text) or _JAVASCRIPT.search(text):
        raise _refuse(f"{element} may only reference this document (url(#id)), without CSS escapes",
                      element, path, hint)


def _parse(svg: str, path: str, view_box: str | None = VIEW_BOX, noun: str = "character") -> tuple[Element, set[str]]:
    """The checked tree and its ids, or InvalidInput naming what broke the contract. `view_box` None takes any
    positive viewBox (a drawing rather than a character); `noun` is what the refusals call the SVG."""
    hint = _HINT if noun == "character" else _DRAWING_HINT
    if len(svg.encode("utf-8")) > MAX_BYTES:
        raise _refuse(f"the SVG is over {MAX_BYTES:,} bytes", "svg", path,
                      hint="simplify paths or drop embedded images")
    if match := _MARKUP_DECL.search(svg):
        kind = "processing instructions" if match.group(0).endswith("?") else \
            "comments, CDATA sections and DOCTYPE/ENTITY declarations"
        raise _refuse(f"{kind} are not allowed in a {noun}", "<" + match.group(0)[-1], path,
                      hint="drop editor comments and the <?xml ...?> line; " + hint)
    try:
        root = ElementTree.fromstring(svg)
    except ElementTree.ParseError as exc:
        raise _refuse(f"the SVG is not well-formed XML: {exc}", "svg", path, hint) from exc
    if _local(root.tag)[1] != "svg":
        raise _refuse("the root element must be <svg>", _local(root.tag)[1], path, hint)
    box = re.split(r"[\s,]+", (root.get("viewBox") or "").strip())
    if view_box is None:
        if not _positive_box(box):
            raise _refuse('the root <svg> needs a viewBox="min-x min-y width height" with a positive size',
                          "svg", path, hint="drawings scale to their slot by their viewBox")
    elif " ".join(box) != view_box:
        raise _refuse(f'the root <svg> needs viewBox="{view_box}"', "svg", path)

    ids: set[str] = set()
    for node in root.iter():
        ns, tag = _local(node.tag)
        if tag.lower() in _FORBIDDEN:
            raise _refuse(f"<{tag}> is not allowed in a {noun}", tag, path, hint)
        if ns not in ("", _SVG_NS) or tag not in _ALLOWED:
            raise _refuse(f"<{tag}> is not an allowed SVG element", tag, path, hint)
        if tag in _TEXT_ONLY and len(node):
            raise _refuse(f"<{tag}> may only hold text", tag, path, hint)
        seen: set[str] = set()
        for raw, value in node.attrib.items():
            attr_ns, attr = _local(raw)
            name = attr.lower()
            shown = "xlink:href" if attr_ns == _XLINK_NS and name == "href" else attr
            if name in seen:
                raise _refuse(f"<{tag}> has two {name} attributes", attr, path,
                              hint="give each attribute once, in one spelling")
            seen.add(name)
            if name.startswith("on"):
                raise _refuse(f"event handler {attr} on <{tag}> is not allowed", attr, path, hint)
            if name == "href" and not value.startswith("#"):
                if tag == "image" and _DATA_IMAGE.match(value):
                    continue
                element = "image" if tag == "image" else shown
                raise _refuse(f"<{tag}> {shown} must point inside the SVG (#id)", element, path, hint)
            _check_css(value, shown, path, hint)
            if name == "id" and not attr_ns:
                if value in ids:
                    raise _refuse(f'id "{value}" is used twice', tag, path, hint="give every id one element")
                ids.add(value)
    return root, ids


def _positive_box(box: list[str]) -> bool:
    try:
        numbers = [float(n) for n in box]
    except ValueError:
        return False
    return len(numbers) == 4 and all(math.isfinite(n) for n in numbers) and numbers[2] > 0 and numbers[3] > 0


def check_drawing(svg: str, *, path: str) -> None:
    """Check an agent drawing against the safety rules alone: any viewBox, no character groups needed."""
    _parse(svg, path, None, "drawing")


def check_svg(svg: str, *, path: str) -> list[str]:
    """Check a character against the contract; return the optional group ids it has."""
    _, ids = _parse(svg, path)
    missing = [name for name in REQUIRED if name not in ids]
    if missing:
        raise _refuse(f"the SVG is missing required group(s): {', '.join(missing)}", "svg", path,
                      hint=f"a character needs groups with ids {', '.join(REQUIRED)}")
    return [name for name in OPTIONAL if name in ids]


def _clean(node: Element) -> Element:
    """A copy without namespaces: SVG tags by local name, xlink:href as href, xml:space/xml:lang kept,
    attributes of any other namespace (editor metadata) dropped."""
    out = Element(_local(node.tag)[1])
    for raw, value in node.attrib.items():
        ns, attr = _local(raw)
        if ns == _XLINK_NS and attr.lower() == "href":
            out.set("href", value)
        elif ns == _XML_NS and attr in ("space", "lang"):
            out.set(f"xml:{attr}", value)
        elif not ns:
            out.set(attr, value)
    out.text, out.tail = node.text, node.tail
    out.extend(_clean(child) for child in node)
    return out


def _serialize(root: Element) -> str:
    root = _clean(root)
    root.attrib = {"xmlns": _SVG_NS, **{k: v for k, v in root.attrib.items() if k != "xmlns"}}
    return ElementTree.tostring(root, encoding="unicode", short_empty_elements=True)


def part_id(char_id: str, name: str) -> str:
    """A character's element id on the page. Character ids have no '.', so the first '.' ends the
    character id and two characters (or a character and the page's own ids) never share one."""
    return f"c_{char_id}.{name}"


_URL_REF = re.compile(r"""(url\(\s*["']?\s*)#""", re.IGNORECASE)


def inline_svg(svg: str, char_id: str, *, path: str = "svg", view_box: str | None = VIEW_BOX,
               noun: str = "character") -> str:
    """The character (or, `view_box` None, any drawing) as the page receives it: re-checked, every id and
    in-document reference scoped with `part_id`, and serialized from the tree."""
    root, _ = _parse(svg, path, view_box, noun)
    prefix = part_id(char_id, "")
    for node in root.iter():
        for raw, value in list(node.attrib.items()):
            name = _local(raw)[1].lower()
            if name == "id" and not _local(raw)[0]:
                value = prefix + value
            elif name == "href" and value.startswith("#"):
                value = "#" + prefix + value[1:]
            node.set(raw, _URL_REF.sub(lambda m: f"{m.group(1)}#{prefix}", value))
    return _serialize(root)


_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


def recolour(svg: str, colors: Mapping[str, str]) -> str:
    """Set the --primary/--secondary/--accent CSS variables on the root element (on the parsed tree)."""
    decls = ";".join(f"--{key}:{colors[key]}" for key in ("primary", "secondary", "accent")
                     if key in colors and _COLOUR.match(colors[key]))
    if not decls:
        return svg
    root, _ = _parse(svg, "svg")
    key = next((k for k in root.attrib if _local(k) == ("", k) and k.lower() == "style"), "style")
    existing = (root.get(key) or "").strip().rstrip(";")
    root.set(key, f"{existing};{decls}" if existing else decls)
    return _serialize(root)
