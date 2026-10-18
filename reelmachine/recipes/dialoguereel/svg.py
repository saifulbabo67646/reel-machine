"""The SVG character contract: what a character must contain, and what it may never contain.

Characters are inlined into the page a headless browser renders, and agents write their
own, so `check_svg` is a security boundary. It refuses DOCTYPE/ENTITY textually before
parsing (so stdlib ElementTree never expands an entity), then walks every element: no
scripting or embedding elements, no animation (the runtime owns motion), no event-handler
attributes, no reference that leaves the document. Tag and attribute names
are compared case-insensitively because the HTML parser lowercases them when the SVG is
inlined, and every attribute value is checked as CSS because presentation attributes
(fill, filter, mask, clip-path, ...) are CSS values.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from xml.etree import ElementTree

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
_DECL = re.compile(r"<!\s*(doctype|entity)", re.IGNORECASE)
_PI = re.compile(r"<\?(?!xml\s)", re.IGNORECASE)
_EXTERNAL_URL = re.compile(r"url\(\s*(?![\"']?\s*#)", re.IGNORECASE)
_CSS_FORBIDDEN = re.compile(r"@import|@font-face|@namespace|javascript:|image-set\(|image\(|\\", re.IGNORECASE)
_JAVASCRIPT = re.compile(r"javascript\s*:", re.IGNORECASE)
_HINT = "characters are plain SVG shapes; see the SVG character contract in the guide"


def _refuse(message: str, element: str, path: str, hint: str = _HINT) -> InvalidInput:
    return InvalidInput(message, hint=hint, details={"path": path, "element": element})


def _local(name: str) -> tuple[str, str]:
    """Split ElementTree's `{ns}local` into (ns, local)."""
    if name.startswith("{"):
        ns, _, local = name[1:].partition("}")
        return ns, local
    return "", name


def _check_css(text: str, element: str, path: str) -> None:
    if _CSS_FORBIDDEN.search(text) or _EXTERNAL_URL.search(text) or _JAVASCRIPT.search(text):
        raise _refuse(f"{element} may only reference this document (url(#id)), without CSS escapes",
                      element, path)


def check_svg(svg: str, *, path: str) -> list[str]:
    """Check a character against the contract; return the optional group ids it has."""
    if len(svg.encode("utf-8")) > MAX_BYTES:
        raise _refuse(f"the SVG is over {MAX_BYTES:,} bytes", "svg", path,
                      hint="simplify paths or drop embedded images")
    if match := _DECL.search(svg):
        raise _refuse("DOCTYPE and ENTITY declarations are not allowed", match.group(1).upper(), path)
    if _PI.search(svg):
        raise _refuse("processing instructions are not allowed", "?", path)
    try:
        root = ElementTree.fromstring(svg)
    except ElementTree.ParseError as exc:
        raise _refuse(f"the SVG is not well-formed XML: {exc}", "svg", path) from exc
    if _local(root.tag)[1] != "svg":
        raise _refuse("the root element must be <svg>", _local(root.tag)[1], path)
    if " ".join(re.split(r"[\s,]+", (root.get("viewBox") or "").strip())) != VIEW_BOX:
        raise _refuse(f'the root <svg> needs viewBox="{VIEW_BOX}"', "svg", path)

    ids: set[str] = set()
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        ns, tag = _local(node.tag)
        if tag.lower() in _FORBIDDEN:
            raise _refuse(f"<{tag}> is not allowed in a character", tag, path)
        if ns not in ("", _SVG_NS) or tag not in _ALLOWED:
            raise _refuse(f"<{tag}> is not an allowed SVG element", tag, path)
        for raw, value in node.attrib.items():
            attr_ns, attr = _local(raw)
            name = attr.lower()
            shown = "xlink:href" if attr_ns == _XLINK_NS and name == "href" else attr
            if name.startswith("on"):
                raise _refuse(f"event handler {attr} on <{tag}> is not allowed", attr, path)
            if name == "href" and not value.startswith("#"):
                if tag == "image" and _DATA_IMAGE.match(value):
                    continue
                element = "image" if tag == "image" else shown
                raise _refuse(f"<{tag}> {shown} must point inside the SVG (#id)", element, path)
            _check_css(value, shown, path)
            if name == "id" and not attr_ns:
                if value in ids:
                    raise _refuse(f'id "{value}" is used twice', tag, path, hint="give every id one element")
                ids.add(value)

    missing = [name for name in REQUIRED if name not in ids]
    if missing:
        raise _refuse(f"the SVG is missing required group(s): {', '.join(missing)}", "svg", path,
                      hint=f"a character needs groups with ids {', '.join(REQUIRED)}")
    return [name for name in OPTIONAL if name in ids]


_ID = re.compile(r"""(?<![\w:.-])(id\s*=\s*)(["'])""", re.IGNORECASE)
_URL_REF = re.compile(r"""(url\(\s*["']?\s*)#""", re.IGNORECASE)
_HREF_REF = re.compile(r"""(?<![\w:.-])((?:[\w.-]+:)?href\s*=\s*)(["'])#""", re.IGNORECASE)


def scope_ids(svg: str, prefix: str) -> str:
    """Prefix every id and every in-document reference, so two characters share one page."""
    svg = _ID.sub(lambda m: f"{m.group(1)}{m.group(2)}{prefix}-", svg)
    svg = _URL_REF.sub(lambda m: f"{m.group(1)}#{prefix}-", svg)
    return _HREF_REF.sub(lambda m: f"{m.group(1)}{m.group(2)}#{prefix}-", svg)


_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")
_ROOT_TAG = re.compile(r"""<svg\b(?:[^>"']|"[^"]*"|'[^']*')*>""")
_STYLE_ATTR = re.compile(r"""(\sstyle\s*=\s*)(["'])(.*?)\2""", re.DOTALL | re.IGNORECASE)


def recolour(svg: str, colors: Mapping[str, str]) -> str:
    """Set the --primary/--secondary/--accent CSS variables on the root element."""
    decls = ";".join(f"--{key}:{colors[key]}" for key in ("primary", "secondary", "accent")
                     if key in colors and _COLOUR.match(colors[key]))
    match = _ROOT_TAG.search(svg)
    if not decls or match is None:
        return svg
    tag = match.group(0)
    if style := _STYLE_ATTR.search(tag):
        existing = style.group(3).strip().rstrip(";")
        joined = f"{existing};{decls}" if existing else decls
        new_tag = tag[: style.start()] + f"{style.group(1)}{style.group(2)}{joined}{style.group(2)}" + tag[style.end():]
    else:
        new_tag = f'<svg style="{decls}"' + tag[4:]
    return svg[: match.start()] + new_tag + svg[match.end():]
