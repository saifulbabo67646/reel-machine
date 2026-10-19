"""A techreel script becomes a document the DOM runtime plays, inside a HyperFrames project.

The document is plain data (times in reel ms, heading tokens, caption chunks); `runtime/main.mjs`
builds the page from it once and `runtime/pure.mjs` decides every frame from the time alone.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from html import escape
from pathlib import Path
from typing import Any

from .markup import chunks, units
from .models import HeroVisual, TechReelInputs, Visual
from .schedule import Plan, cue_ms, exit_ms

HERE = Path(__file__).resolve().parent
RUNTIME_DIR = HERE / "runtime"
RUNTIME_FILES = ("*.mjs", "*.css")

WIDTH, HEIGHT, FPS = 1080, 1920, 30
_OVERLAYS = ("stamp", "emoji")
_KEYS = ("bg", "glow1", "glow2", "glow3", "text", "muted", "accent", "accentInk", "card", "cardEdge", "red", "green")


def _look(*values: str) -> dict[str, str]:
    return dict(zip(_KEYS, values, strict=True))


# `accentInk` is the text colour on an accent fill; the glows are three soft background blooms
LOOKS: dict[str, dict[str, str]] = {
    "mint": _look("#06100f", "#0f6f66", "#0a3d4a", "#0b2447", "#e9fbf6", "#7fa7a0", "#2ee6b8", "#032019",
                  "rgba(255,255,255,0.05)", "rgba(46,230,184,0.22)", "#ff5d73", "#4ade80"),
    "ember": _look("#141110", "#7a2e12", "#4a2a1a", "#2a2321", "#f7efe9", "#a8998f", "#ff5a2a", "#1a0a04",
                   "rgba(255,255,255,0.05)", "rgba(255,90,42,0.24)", "#ff4d5e", "#5ee08a"),
    "violet": _look("#0d0a18", "#3b2a8c", "#241a5e", "#14123a", "#f1edff", "#9a93bf", "#9b7bff", "#140a2e",
                    "rgba(255,255,255,0.05)", "rgba(155,123,255,0.24)", "#ff6b8b", "#5eead4"),
    "mono": _look("#0b0b0c", "#2a2a2e", "#1c1c1f", "#141416", "#f4f4f5", "#9a9aa0", "#f4f4f5",
                  "#0b0b0c", "rgba(255,255,255,0.06)", "rgba(255,255,255,0.20)", "#ff6b6b", "#6ee7a0"),
}


def look_tokens(inputs: TechReelInputs) -> dict[str, str]:
    tokens = dict(LOOKS[inputs.look])
    if inputs.accent:
        tokens["accent"] = inputs.accent
    return tokens


def _camel(name: str) -> str:
    head, *rest = name.rstrip("_").split("_")
    return head + "".join(part.title() for part in rest)


def _visual(visual: Visual, vid: str, enter: int, leave: int, cues: list[int]) -> dict[str, Any]:
    """A stage visual as the page reads it: fields camelCased, cues resolved to reel ms."""
    fields = visual.model_dump(exclude={"at", "exit"}, exclude_none=True)
    out: dict[str, Any] = {_camel(k): v for k, v in fields.items() if k != "cursor"}
    if visual.kind == "window":
        out["cursor"] = [{"to": list(m.to), "atMs": ms} for m, ms in zip(visual.cursor, cues, strict=True)]
    out |= {"id": vid, "enterMs": enter, "exitMs": leave,
            "class": "overlay" if visual.kind in _OVERLAYS else "block"}
    return out


def reel_document(inputs: TechReelInputs, plan: Plan, times: list[list[list[tuple[int, int]]]]) -> dict[str, Any]:
    """`times[b][l]` are beat b line l's unit times, line-relative; here they move to reel time."""
    beats: list[dict[str, Any]] = []
    captions: list[dict[str, Any]] = []
    hero_states: list[dict[str, Any]] = []
    for b, (beat, slot) in enumerate(zip(inputs.beats, plan.beats, strict=True)):
        us = [units(ln.caption, inputs.language) for ln in beat.lines]
        next_start = plan.beats[b + 1].start_ms if b + 1 < len(plan.beats) else None
        stage: list[dict[str, Any]] = []
        for v, visual in enumerate(beat.visuals):
            enter, _ = cue_ms(visual.at, slot, times[b], us)
            if isinstance(visual, HeroVisual):
                hero_states.append({"atMs": enter, "state": visual.state, "text": visual.text})
                continue
            leave = exit_ms(visual.exit, slot, next_start, times[b], us)
            moves = [cue_ms(m.at, slot, times[b], us)[0] for m in getattr(visual, "cursor", [])]
            stage.append(_visual(visual, f"b{b}v{v}", enter, leave, moves))
        beats.append({
            "id": beat.id or f"b{b}", "kind": beat.kind, "startMs": slot.start_ms, "endMs": slot.end_ms,
            "transition": beat.transition,
            "heading": [{"t": u.text, "a": u.accent} for u in units(beat.heading, inputs.language)] if beat.heading else [],
            "kicker": beat.kicker or "", "visuals": stage,
        })
        for line_slot, line_us, line_times in zip(slot.lines, us, times[b], strict=True):
            groups = chunks(line_us, inputs.language)
            starts = [line_slot.start_ms + line_times[g[0]][0] for g in groups]
            for n, group in enumerate(groups):
                end = min(starts[n + 1], line_slot.end_ms) if n + 1 < len(groups) else line_slot.end_ms
                captions.append({
                    "startMs": starts[n], "endMs": max(starts[n], end),
                    "words": [{"t": line_us[i].text, "a": line_us[i].accent,
                               "startMs": line_slot.start_ms + line_times[i][0],
                               "endMs": line_slot.start_ms + line_times[i][1]} for i in group],
                })
    hero_states.sort(key=lambda s: s["atMs"])
    if inputs.hero is None:
        hero_states = []
    elif not hero_states or hero_states[0]["atMs"] > 0:
        hero_states.insert(0, {"atMs": 0, "state": "center", "text": None})
    return {
        "mode": "reel", "width": WIDTH, "height": HEIGHT, "duration": plan.duration_ms / 1000,
        "language": inputs.language, "look": look_tokens(inputs),
        "hero": {"kind": inputs.hero.kind, "text": inputs.hero.text} if inputs.hero else None,
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


def write_project(doc: dict[str, Any], dest: Path) -> Path:
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "runtime").mkdir(parents=True)
    for path in _runtime_paths():
        shutil.copy2(path, dest / "runtime" / path.name)
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


def project_key(doc: dict[str, Any]) -> str:
    """What the rendered video depends on: the document, the runtime and the template."""
    digest = hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    for path in _runtime_paths():
        digest.update(f"\0{path.name}\0".encode())
        digest.update(path.read_bytes())
    digest.update(_TEMPLATE.encode("utf-8"))
    return digest.hexdigest()
