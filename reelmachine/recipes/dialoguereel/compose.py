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
from .keys import match_keys
from .levels import is_japanese, jlpt_level, primary_language
from .models import DialogueReelInputs, KeyWord, Line
from .ruby import parse_ruby, plain_text
from .schedule import QuizWindow, Schedule
from .strings import strings_for
from .svg import inline_svg

HERE = Path(__file__).resolve().parent
RUNTIME_DIR = HERE / "runtime"
RUNTIME_FILES = ("*.mjs", "*.css")

WIDTH, HEIGHT, FPS = 1080, 1920, 30
CAST_SECONDS = 2
RECAP_MAX = 6
# the hook's "can you understand this?" line, in the viewer's language (meaning_language's primary subtag);
# any other language has no prompt and the runtime shows no pill
PROMPTS: dict[str, str] = {
    "en": "Can you understand this?", "ja": "わかるかな？", "ko": "이해할 수 있나요?", "es": "¿Lo entiendes?",
    "zh": "你能听懂吗？",
}
LOOKS: dict[str, dict[str, str]] = {
    "pop": {"bg": "#4b2bff", "text": "#ffffff", "reading": "#7fd3ff", "meaning": "#9fe0ff", "accent": "#ffe066",
            "bubble": "rgba(255,255,255,0.10)", "ink": "#241466", "shade": "20,0,90"},
    "pastel": {"bg": "#fff1e0", "text": "#3b2a4d", "reading": "#c2577f", "meaning": "#5b55c8", "accent": "#ffb0c8",
               "bubble": "rgba(255,255,255,0.78)", "ink": "#3b2a4d", "shade": "150,105,80"},
    "night": {"bg": "#0c1230", "text": "#f4f6ff", "reading": "#5ef2ff", "meaning": "#b9a9ff", "accent": "#ff4fd8",
              "bubble": "rgba(255,255,255,0.08)", "ink": "#0c1230", "shade": "0,0,0"},
}


def prompt_for(meaning_language: str) -> str | None:
    return PROMPTS.get(primary_language(meaning_language))


def _key_word(key: KeyWord, japanese: bool) -> dict[str, Any]:
    """A key as the page shows it: its plain text, its ruby runs (one plain run when it has no ruby), reading,
    meaning, and a JLPT level (Japanese only) looked up from the plain text."""
    runs, _ = parse_ruby(key.text)
    text = plain_text(runs)
    return {"text": text, "runs": [{"t": run.text, "r": run.ruby} for run in runs], "reading": key.reading,
            "meaning": key.meaning, "level": jlpt_level(text, key.reading) if japanese else None}


def _keys(line: Line, language: str) -> list[dict[str, Any]]:
    """The line's key words that were found in it, with their place in its plain text (for the underline)."""
    japanese = is_japanese(language)
    return [{**_key_word(line.keys[span.index], japanese), "start": span.start, "end": span.end}
            for span in match_keys(line.text, line.keys) if span is not None]


def recap_card(key: KeyWord, language: str) -> dict[str, Any]:
    """A recap card: the key's plain text, ruby runs, reading, meaning and level."""
    return _key_word(key, is_japanese(language))


def _recap(inputs: DialogueReelInputs) -> list[dict[str, Any]]:
    """Every key in the reel, found in its line or not, in reel order, each word (its plain text) once, at most six."""
    cards: list[dict[str, Any]] = []
    for line in inputs.lines:
        for key in line.keys:
            card = recap_card(key, inputs.language)
            if len(cards) < RECAP_MAX and all(c["text"] != card["text"] for c in cards):
                cards.append(card)
    return cards


def _quiz(quiz: QuizWindow | None) -> dict[str, int] | None:
    if quiz is None:
        return None
    return {"line": quiz.line, "startMs": quiz.start_ms, "revealMs": quiz.reveal_ms, "endMs": quiz.end_ms}


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
            "keys": _keys(line, inputs.language), "emotion": line.emotion,
            "slowStartMs": window.slow_start_ms,
            "slowEndMs": window.slow_start_ms + window.slow_ms if window.slow_start_ms is not None else None,
        })
    return {
        "mode": "reel", "width": WIDTH, "height": HEIGHT, "duration": plan.duration_ms / 1000,
        "look": dict(LOOKS[inputs.look]), "title": inputs.title, "level": inputs.level, "scene": inputs.scene or "",
        "language": inputs.language, "prompt": prompt_for(inputs.meaning_language), "hookMs": plan.hook_ms, "characters": _characters(cast), "lines": lines,
        "strings": strings_for(inputs.meaning_language, inputs.level),
        "listen": [{"startMs": w.start_ms, "endMs": w.end_ms} for w in plan.listen],
        "bridgeMs": plan.bridge_ms, "recapMs": plan.recap_ms,
        "recap": _recap(inputs) if plan.recap_ms is not None else [], "quiz": _quiz(plan.quiz),
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
    """Each character re-checked and serialized from its parsed tree: the page never gets the source text."""
    return "\n".join(
        f'<div class="char {escape(c.side)}" id="char-{escape(c.id)}">'
        f'{inline_svg(c.svg, c.id, path=f"cast.characters[{i}].source.svg")}</div>'
        for i, c in enumerate(cast.characters)
    )


def write_project(doc: dict[str, Any], cast: ResolvedCast, dest: Path) -> Path:
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
    html = _TEMPLATE.format(w=WIDTH, h=HEIGHT, lang=escape(str(doc.get("language") or "en")),
                            duration=f"{doc['duration']:.3f}", characters=_character_html(cast), data=data)
    (dest / "index.html").write_text(html, encoding="utf-8")
    return dest


def project_key(doc: dict[str, Any], cast: ResolvedCast) -> str:
    """What the rendered video depends on: the document, the characters' SVGs, the runtime and the template."""
    digest = hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    for char in cast.characters:
        digest.update(f"\0{char.id}\0{char.side}\0".encode())
        digest.update(inline_svg(char.svg, char.id).encode("utf-8"))
    for path in _runtime_paths():
        digest.update(f"\0{path.name}\0".encode())
        digest.update(path.read_bytes())
    digest.update(_TEMPLATE.encode("utf-8"))
    return digest.hexdigest()
