"""A techreel script becomes a document the DOM runtime plays, inside a HyperFrames project.

The document is plain data (times in reel ms, heading tokens, caption chunks); `runtime/main.mjs`
builds the page from it once and `runtime/pure.mjs` decides every frame from the time alone.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Mapping, Sequence
from html import escape
from pathlib import Path
from typing import Any

from ..dialoguereel.svg import inline_svg
from .markup import Unit, chunks, is_unspaced, merge_short, units
from .models import (
    KEYWORD_MAX,
    Beat,
    CommentVisual,
    Cue,
    FollowVisual,
    HeroVisual,
    TechReelInputs,
    TitleVisual,
    Visual,
)
from .schedule import BeatSlot, Plan, cue_ms, exit_ms

HERE = Path(__file__).resolve().parent
RUNTIME_DIR = HERE / "runtime"
RUNTIME_FILES = ("*.mjs", "*.css")
ASSETS = "assets"         # the project folder the images are copied into; their `file` is relative to the page

WIDTH, HEIGHT, FPS = 1080, 1920, 30
_OVERLAYS = ("stamp", "emoji", "error", "flash")
_KEYS = ("bg", "glow1", "glow2", "glow3", "text", "muted", "accent", "accentInk", "card", "cardEdge", "red", "green")


def _unit(u: Unit) -> dict[str, Any]:
    """A heading or caption word as the page reads it; a partly accented word carries its accented runs."""
    out: dict[str, Any] = {"t": u.text, "a": u.accent}
    if u.accent_spans:
        out["accentSpans"] = [list(span) for span in u.accent_spans]
    return out


def _look(*values: str) -> dict[str, str]:
    return dict(zip(_KEYS, values, strict=True))


# `accentInk` is the text colour on an accent fill (look_tokens recomputes it from the accent); the glows are
# three soft background blooms
LOOKS: dict[str, dict[str, str]] = {
    "mint": _look("#06100f", "#0f6f66", "#0a3d4a", "#0b2447", "#e9fbf6", "#7fa7a0", "#2ee6b8", "#000000",
                  "rgba(255,255,255,0.05)", "rgba(46,230,184,0.22)", "#ff5d73", "#4ade80"),
    "ember": _look("#141110", "#7a2e12", "#4a2a1a", "#2a2321", "#f7efe9", "#a8998f", "#ff5a2a", "#000000",
                   "rgba(255,255,255,0.05)", "rgba(255,90,42,0.24)", "#ff4d5e", "#5ee08a"),
    "violet": _look("#0d0a18", "#3b2a8c", "#241a5e", "#14123a", "#f1edff", "#9a93bf", "#9b7bff", "#000000",
                    "rgba(255,255,255,0.05)", "rgba(155,123,255,0.24)", "#ff6b8b", "#5eead4"),
    "mono": _look("#0b0b0c", "#2a2a2e", "#1c1c1f", "#141416", "#f4f4f5", "#9a9aa0", "#f4f4f5",
                  "#000000", "rgba(255,255,255,0.06)", "rgba(255,255,255,0.20)", "#ff6b6b", "#6ee7a0"),
}


_ACCENT_CONTRAST = 3.0   # the least an accent may contrast with the background (large text, WCAG)


def _rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def _luminance(colour: str) -> float:
    """WCAG relative luminance of a #rrggbb colour."""
    def linear(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (linear(c) for c in _rgb(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _toward_white(colour: str, p: float) -> str:
    return "#" + "".join(f"{round(c + (255 - c) * p):02x}" for c in _rgb(colour))


def _readable_accent(accent: str, bg: str) -> str:
    """`accent`, or the least mix of it with white that reaches 3:1 on `bg`: a dim custom accent would vanish
    into the dark background (every look's background is dark, so lightening only raises the contrast)."""
    if _contrast(accent, bg) >= _ACCENT_CONTRAST:
        return accent
    for step in range(1, 101):
        lighter = _toward_white(accent, step / 100)
        if _contrast(lighter, bg) >= _ACCENT_CONTRAST:
            return lighter
    return "#ffffff"


def look_tokens(inputs: TechReelInputs) -> dict[str, str]:
    """The look's colours, with the custom accent if there is one, made readable on the background; the ink (text
    on an accent fill) is whichever of black and white contrasts more with the accent."""
    tokens = dict(LOOKS[inputs.look])
    tokens["accent"] = _readable_accent(inputs.accent or tokens["accent"], tokens["bg"])
    tokens["accentInk"] = max(("#000000", "#ffffff"), key=lambda ink: _contrast(tokens["accent"], ink))
    return tokens


def _camel(name: str) -> str:
    head, *rest = name.rstrip("_").split("_")
    return head + "".join(part.title() for part in rest)


def _camel_keys(value: Any) -> Any:
    """Nested model fields camelCase too; aliases (`from`) were already applied by the dump."""
    if isinstance(value, dict):
        return {_camel(k): _camel_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_camel_keys(v) for v in value]
    return value


def _visual(
    visual: Visual,
    vid: str,
    enter: int,
    leave: int,
    cues: list[int],
    slot: BeatSlot,
    times: Sequence[Sequence[tuple[int, int]]],
    us: Sequence[Sequence[Unit]],
    images: Mapping[str, str],
    path: str,
) -> dict[str, Any]:
    """A stage visual as the page reads it: fields camelCased, cues resolved to reel ms; an image as its file in
    the project (never its source), a drawing re-checked with its ids scoped to this visual."""
    fields = visual.model_dump(exclude={"at", "exit", "click_at", "src"}, exclude_none=True, by_alias=True)
    out: dict[str, Any] = {_camel(k): _camel_keys(v) for k, v in fields.items() if k != "cursor"}
    if visual.kind == "image":
        out["file"] = _asset(visual.src, images, f"{path}.src")
    elif visual.kind == "svg":
        out["svg"] = inline_svg(visual.svg, vid, path=f"{path}.svg", view_box=None, noun="drawing")
    if visual.kind == "window":
        out["cursor"] = [{"to": list(m.to), "atMs": ms} for m, ms in zip(visual.cursor, cues, strict=True)]
    if getattr(visual, "click_at", None) is not None:
        out["clickAtMs"] = cue_ms(visual.click_at, slot, times, us)[0]
    out |= {"id": vid, "enterMs": enter, "exitMs": leave,
            "class": "overlay" if visual.kind in _OVERLAYS else "block"}
    return out


def _asset(src: str, images: Mapping[str, str], path: str) -> str:
    """An image's file in the project. An image the assets stage did not fetch would draw as an empty frame, so
    it is a bug upstream, not something to render."""
    if src not in images:
        raise ValueError(f"{path}: the image {src} was not fetched (no entry in `images`)")
    return f"{ASSETS}/{images[src]}"


def _creator(inputs: TechReelInputs, images: Mapping[str, str]) -> dict[str, Any] | None:
    if inputs.creator is None:
        return None
    out = inputs.creator.model_dump()
    if out["avatar"] is not None:
        out["avatar"] = _asset(out["avatar"], images, "creator.avatar")
    return out


def _hero(inputs: TechReelInputs, images: Mapping[str, str]) -> dict[str, Any] | None:
    hero = inputs.hero
    if hero is None:
        return None
    out: dict[str, Any] = {"kind": hero.kind, "text": hero.text}
    if hero.svg is not None:
        out["svg"] = inline_svg(hero.svg, "hero", path="hero.svg", view_box=None, noun="drawing")
    if hero.src is not None:
        out["file"] = _asset(hero.src, images, "hero.src")
    return out


FOLLOW_FRAC, TITLE_FRAC = 0.6, 0.8    # a line shared with an earlier generated visual: how far into it


def _cta_visuals(beat: Beat, inputs: TechReelInputs) -> list[Visual]:
    """The standard ending a `cta` beat gets when it has no stage visuals of its own (hero moves do not count):
    the comment box at the first line, the creator's follow card at the second (part way into the first when
    there is one line), the next-reel tease as a title at the last line."""
    if beat.kind != "cta" or any(not isinstance(v, HeroVisual) for v in beat.visuals):
        return []
    # the keyword falls back to the title's first word: "Pocket: local AI" asks for "POCKET"
    # (a number is no keyword: "5 AI tools" asks for "AI")
    first = next((w for w in re.findall(r"\w+", inputs.title) if not w.isdigit()), None)
    keyword = beat.keyword or (first.upper()[:KEYWORD_MAX] if first else "LINK")
    out: list[Visual] = [CommentVisual(kind="comment", keyword=keyword, reply=beat.dm or "", at=Cue(line=0))]
    last, used = len(beat.lines) - 1, {0}
    if inputs.creator is not None:
        at = Cue(line=1) if last >= 1 else Cue(line=0, frac=FOLLOW_FRAC)
        out.append(FollowVisual(kind="follow", at=at))
        used.add(at.line)
    if beat.next:
        at = Cue(line=last, frac=TITLE_FRAC) if last in used else Cue(line=last)
        out.append(TitleVisual(kind="title", text=beat.next, at=at))
    return out


def reel_document(inputs: TechReelInputs, plan: Plan, times: list[list[list[tuple[int, int]]]],
                  images: Mapping[str, str] | None = None) -> dict[str, Any]:
    """`times[b][l]` are beat b line l's unit times, line-relative; here they move to reel time. `images` maps
    each image `src` (visuals, the hero, the creator's avatar) to its file name in the assets folder; a `src`
    missing from it is a ValueError."""
    images = images or {}
    beats: list[dict[str, Any]] = []
    captions: list[dict[str, Any]] = []
    hero_states: list[dict[str, Any]] = []
    for b, (beat, slot) in enumerate(zip(inputs.beats, plan.beats, strict=True)):
        us = [units(ln.caption, inputs.language) for ln in beat.lines]
        next_start = plan.beats[b + 1].start_ms if b + 1 < len(plan.beats) else None
        stage: list[dict[str, Any]] = []
        # generated visuals follow the authored ones, so their ids continue b{beat}v{n}
        for v, visual in enumerate([*beat.visuals, *_cta_visuals(beat, inputs)]):
            enter, _ = cue_ms(visual.at, slot, times[b], us)
            if isinstance(visual, HeroVisual):
                hero_states.append({"atMs": enter, "state": visual.state, "text": visual.text,
                                    "count": visual.count, "labels": list(visual.labels)})
                continue
            # an exit cued before the enter would leave a visual with a negative life (lint `exit-before-enter`)
            leave = max(enter, exit_ms(visual.exit, slot, next_start, times[b], us))
            moves = [cue_ms(m.at, slot, times[b], us)[0] for m in getattr(visual, "cursor", [])]
            stage.append(_visual(visual, f"b{b}v{v}", enter, leave, moves, slot, times[b], us, images,
                                 f"beats[{b}].visuals[{v}]"))
        beats.append({
            "id": beat.id or f"b{b}", "kind": beat.kind, "startMs": slot.start_ms, "endMs": slot.end_ms,
            "transition": beat.transition,
            "heading": [_unit(u) for u in units(beat.heading, inputs.language)] if beat.heading else [],
            "kicker": beat.kicker or "", "keyword": beat.keyword or "", "dm": beat.dm or "",
            "next": beat.next or "", "visuals": stage,
        })
        for line_slot, line_us, line_times in zip(slot.lines, us, times[b], strict=True):
            groups = chunks(line_us, inputs.language)
            # a chunk that would flash by joins its neighbour; characters are the cap in unspaced scripts
            groups = merge_short(groups, line_times, us=line_us if is_unspaced(inputs.language) else None)
            starts = [line_slot.start_ms + line_times[g[0]][0] for g in groups]
            for n, group in enumerate(groups):
                end = min(starts[n + 1], line_slot.end_ms) if n + 1 < len(groups) else line_slot.end_ms
                captions.append({
                    "startMs": starts[n], "endMs": max(starts[n], end),
                    "words": [{**_unit(line_us[i]),
                               "startMs": line_slot.start_ms + line_times[i][0],
                               "endMs": line_slot.start_ms + line_times[i][1]} for i in group],
                })
    hero_states.sort(key=lambda s: s["atMs"])
    if inputs.hero is None:
        hero_states = []
    elif not hero_states or hero_states[0]["atMs"] > 0:
        hero_states.insert(0, {"atMs": 0, "state": "center", "text": None,
                                "count": HeroVisual.model_fields["count"].default, "labels": []})
    return {
        "mode": "reel", "width": WIDTH, "height": HEIGHT, "duration": plan.duration_ms / 1000,
        "language": inputs.language, "look": look_tokens(inputs),
        "hero": _hero(inputs, images),
        "creator": _creator(inputs, images),
        "heroStates": hero_states, "beats": beats, "captions": captions, "sfx": inputs.sfx,
    }


_TEMPLATE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width={w}, height={h}" />
<style>
{css}
</style>
<script>window.__hf = window.__hf || {{}}; window.__reelReady = new Promise((r) => {{ window.__reelResolve = r; }}); window.__hf.buildReady = {{ techreel: window.__reelReady }};</script>
</head>
<body>
<div id="root" data-composition-id="techreel" data-start="0" data-width="{w}" data-height="{h}" data-duration="{duration}" data-no-timeline>
</div>
<script id="reel-data" type="application/json">{data}</script>
<script type="module" src="./runtime/main.mjs"></script>
</body>
</html>
"""


def _runtime_paths() -> list[Path]:
    return sorted(p for pattern in RUNTIME_FILES for p in RUNTIME_DIR.glob(pattern))


def asset_files(doc: dict[str, Any]) -> list[str]:
    """The file names the document's images point at (in the assets folder), sorted, each once."""
    files = [v["file"] for beat in doc.get("beats", []) for v in beat.get("visuals", []) if "file" in v]
    if (doc.get("hero") or {}).get("file"):
        files.append(doc["hero"]["file"])
    if (doc.get("creator") or {}).get("avatar"):
        files.append(doc["creator"]["avatar"])
    return sorted({f.removeprefix(f"{ASSETS}/") for f in files})


def _asset_paths(doc: dict[str, Any], assets: Path | None) -> list[Path]:
    names = asset_files(doc)
    if names and assets is None:
        raise FileNotFoundError("the document has images but no assets folder was given")
    paths = [Path(assets) / name for name in names] if names else []
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"the image {path.name} is missing from {path.parent}")
    return paths


def write_project(doc: dict[str, Any], dest: Path, assets: Path | None = None) -> Path:
    """The HyperFrames project: index.html with the document, the runtime, and the images the document uses
    (copied from `assets`)."""
    dest = Path(dest)
    images = _asset_paths(doc, assets)
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "runtime").mkdir(parents=True)
    for path in _runtime_paths():
        shutil.copy2(path, dest / "runtime" / path.name)
    if images:
        (dest / ASSETS).mkdir()
        for path in images:
            shutil.copy2(path, dest / ASSETS / path.name)
    # every "<" escaped, not only "</": text like "<!--<script>" would otherwise put the HTML parser into its
    # "script data double escaped" state, the real </script> would not close the data script, and the
    # runtime's module script would be swallowed (buildReady never settles and the render hangs)
    data = json.dumps(doc, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")
    # the look goes inline: HyperFrames' render compiler fetches the fonts the page itself names (snapshot bundles
    # linked sheets first, render does not), so a linked reel.css left the display font to a regular fallback
    sheet = dest / "runtime" / "reel.css"
    if not sheet.is_file():
        raise FileNotFoundError(f"the techreel runtime has no reel.css ({RUNTIME_DIR})")
    css = sheet.read_text(encoding="utf-8").replace("</", "<\\/")
    html = _TEMPLATE.format(w=WIDTH, h=HEIGHT, lang=escape(str(doc.get("language") or "en")), css=css,
                            duration=f"{doc['duration']:.3f}", data=data)
    (dest / "index.html").write_text(html, encoding="utf-8")
    return dest


def project_key(doc: dict[str, Any], assets: Path | None = None) -> str:
    """What the rendered video depends on: the document, the runtime, the template and the images' bytes."""
    digest = hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    for path in _runtime_paths():
        digest.update(f"\0{path.name}\0".encode())
        digest.update(path.read_bytes())
    for path in _asset_paths(doc, assets):
        digest.update(f"\0{ASSETS}/{path.name}\0".encode())
        digest.update(path.read_bytes())
    digest.update(_TEMPLATE.encode("utf-8"))
    return digest.hexdigest()
