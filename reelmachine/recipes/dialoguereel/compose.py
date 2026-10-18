"""A dialogue becomes a document the DOM runtime plays, inside a HyperFrames project.

The document is plain data (times in reel ms, ruby runs, word spans); `runtime/main.mjs`
builds the page from it once and `runtime/pure.mjs` decides every frame from the time alone.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from html import escape
from pathlib import Path
from typing import Any

from reelmachine.core.timeline import WordSpan

from .cast import ResolvedCast
from .models import DialogueReelInputs
from .ruby import parse_ruby
from .schedule import Schedule
from .svg import scope_ids

HERE = Path(__file__).resolve().parent
RUNTIME_DIR = HERE / "runtime"
RUNTIME_FILES = ("*.mjs", "*.css")

WIDTH, HEIGHT, FPS = 1080, 1920, 30
CAST_SECONDS = 2
LOOKS: dict[str, dict[str, str]] = {
    "pop": {"bg": "#4b2bff", "text": "#ffffff", "reading": "#7fd3ff", "meaning": "#9fe0ff", "accent": "#ffe066",
            "bubble": "rgba(255,255,255,0.10)"},
}


def _characters(cast: ResolvedCast) -> list[dict[str, Any]]:
    return [{"id": c.id, "side": c.side, "parts": list(c.parts)} for c in cast.characters]


def reel_document(inputs: DialogueReelInputs, cast: ResolvedCast, plan: Schedule,
                  words: list[list[WordSpan]]) -> dict[str, Any]:
    """`words[i]` are line i's spoken spans, line-relative (from TTS); here they move to reel time."""
    lines = []
    for index, (line, window) in enumerate(zip(inputs.lines, plan.lines, strict=True)):
        runs, _ = parse_ruby(line.text)
        spans = words[index] if index < len(words) else []
        lines.append({
            "speaker": line.speaker,
            "runs": [{"t": run.text, "r": run.ruby} for run in runs],
            "reading": line.reading or "",
            "meaning": line.meaning,
            "startMs": window.start_ms, "endMs": window.end_ms, "meaningMs": window.meaning_ms,
            "words": [{"text": w.text, "startMs": window.start_ms + w.start_ms, "endMs": window.start_ms + w.end_ms}
                      for w in spans],
        })
    return {
        "mode": "reel", "width": WIDTH, "height": HEIGHT, "duration": plan.duration_ms / 1000,
        "look": dict(LOOKS[inputs.look]), "title": inputs.title, "level": inputs.level, "scene": inputs.scene or "",
        "language": inputs.language, "hookMs": plan.hook_ms, "characters": _characters(cast), "lines": lines,
    }


def cast_document(cast: ResolvedCast, look: str = "pop") -> dict[str, Any]:
    """Two seconds of the characters alone: they drop in, bob and blink."""
    return {"mode": "cast", "width": WIDTH, "height": HEIGHT, "duration": CAST_SECONDS, "look": dict(LOOKS[look]),
            "characters": _characters(cast), "lines": []}


_TEMPLATE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width={w}, height={h}" />
<link rel="stylesheet" href="./runtime/reel.css" />
<script>window.__hf = window.__hf || {{}}; window.__reelReady = new Promise((r) => {{ window.__reelResolve = r; }}); window.__hf.buildReady = {{ dialoguereel: window.__reelReady }};</script>
</head>
<body>
<div id="root" data-composition-id="dialogue" data-start="0" data-width="{w}" data-height="{h}" data-duration="{duration}" data-no-timeline>
{characters}
</div>
<script id="reel-data" type="application/json">{data}</script>
<script type="module" src="./runtime/main.mjs"></script>
</body>
</html>
"""


def _runtime_paths() -> list[Path]:
    return sorted(p for pattern in RUNTIME_FILES for p in RUNTIME_DIR.glob(pattern))


def _character_html(cast: ResolvedCast) -> str:
    return "\n".join(
        f'<div class="char {escape(c.side)}" id="char-{escape(c.id)}">{scope_ids(c.svg, c.id)}</div>'
        for c in cast.characters
    )


def write_project(doc: dict[str, Any], cast: ResolvedCast, dest: Path) -> Path:
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "runtime").mkdir(parents=True)
    for path in _runtime_paths():
        shutil.copy2(path, dest / "runtime" / path.name)
    data = json.dumps(doc, ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    html = _TEMPLATE.format(w=WIDTH, h=HEIGHT, lang=escape(str(doc.get("language") or "en")),
                            duration=f"{doc['duration']:.3f}", characters=_character_html(cast), data=data)
    (dest / "index.html").write_text(html, encoding="utf-8")
    return dest


def project_key(doc: dict[str, Any], cast: ResolvedCast) -> str:
    """What the rendered video depends on: the document, the characters' SVGs, the runtime and the template."""
    digest = hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    for char in cast.characters:
        digest.update(f"\0{char.id}\0{char.side}\0".encode())
        digest.update(char.svg.encode("utf-8"))
    for path in _runtime_paths():
        digest.update(f"\0{path.name}\0".encode())
        digest.update(path.read_bytes())
    digest.update(_TEMPLATE.encode("utf-8"))
    return digest.hexdigest()
