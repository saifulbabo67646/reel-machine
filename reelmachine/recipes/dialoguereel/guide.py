"""The dialoguereel guide: what describe_recipe hands an agent so it can write a good script and its own characters."""

from __future__ import annotations

from typing import Any

from .library import builtin_names
from .svg import MAX_BYTES, OPTIONAL, REQUIRED, VIEW_BOX

# A complete, minimal character in the house style: a round mochi. It passes check_svg (a test says so).
EXAMPLE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400">
  <g id="arm-left" data-pivot="104,270">
    <ellipse cx="78" cy="286" rx="26" ry="15" transform="rotate(-30 78 286)" fill="#fff" stroke="#fff" stroke-width="28"/>
    <ellipse cx="78" cy="286" rx="26" ry="15" transform="rotate(-30 78 286)" style="fill:var(--secondary,#ffe1ec)" stroke="#3a2a3a" stroke-width="5.5"/>
  </g>
  <g id="arm-right" data-pivot="296,270">
    <ellipse cx="322" cy="286" rx="26" ry="15" transform="rotate(30 322 286)" fill="#fff" stroke="#fff" stroke-width="28"/>
    <ellipse cx="322" cy="286" rx="26" ry="15" transform="rotate(30 322 286)" style="fill:var(--secondary,#ffe1ec)" stroke="#3a2a3a" stroke-width="5.5"/>
  </g>
  <g id="body">
    <path d="M96 330 C70 250 110 120 200 120 C290 120 330 250 304 330 Q200 368 96 330 Z" fill="#fff" stroke="#fff" stroke-width="34" stroke-linejoin="round"/>
    <path d="M96 330 C70 250 110 120 200 120 C290 120 330 250 304 330 Q200 368 96 330 Z" style="fill:var(--primary,#fff4f7)" stroke="#3a2a3a" stroke-width="5.5" stroke-linejoin="round"/>
    <ellipse cx="200" cy="330" rx="96" ry="14" fill="#3a2a3a" opacity="0.08"/>
    <ellipse cx="150" cy="168" rx="18" ry="9" transform="rotate(-35 150 168)" fill="#fff" opacity="0.8"/>
    <circle cx="200" cy="112" r="14" style="fill:var(--accent,#9fe2c8)" stroke="#3a2a3a" stroke-width="5"/>
  </g>
  <g id="blush">
    <ellipse cx="136" cy="258" rx="17" ry="9" fill="#ff5f95" opacity="0.45"/>
    <ellipse cx="264" cy="258" rx="17" ry="9" fill="#ff5f95" opacity="0.45"/>
  </g>
  <g id="eyes-open">
    <circle cx="164" cy="234" r="12" fill="#3a2a3a"/><circle cx="236" cy="234" r="12" fill="#3a2a3a"/>
    <circle cx="160" cy="229" r="4" fill="#fff"/><circle cx="232" cy="229" r="4" fill="#fff"/>
  </g>
  <g id="eyes-closed" display="none" fill="none" stroke="#3a2a3a" stroke-width="5" stroke-linecap="round">
    <path d="M152 233 Q164 243 176 233"/><path d="M224 233 Q236 243 248 233"/>
  </g>
  <g id="eyes-happy" display="none" fill="none" stroke="#3a2a3a" stroke-width="5" stroke-linecap="round">
    <path d="M152 239 Q164 223 176 239"/><path d="M224 239 Q236 223 248 239"/>
  </g>
  <g id="mouth-closed" fill="none" stroke="#3a2a3a" stroke-width="5" stroke-linecap="round">
    <path d="M190 252 Q200 261 210 252"/>
  </g>
  <g id="mouth-open" display="none" stroke="#3a2a3a" stroke-width="4.5" stroke-linejoin="round">
    <path d="M187 250 Q200 247 213 250 Q212 274 200 274 Q188 274 187 250 Z" fill="#8a2f45"/>
  </g>
  <g id="mouth-smile" display="none" stroke="#3a2a3a" stroke-width="4.5" stroke-linejoin="round">
    <path d="M182 249 Q200 252 218 249 Q216 270 200 270 Q184 270 182 249 Z" fill="#8a2f45"/>
  </g>
</svg>"""

GUIDE: dict[str, Any] = {
    "workflow": [
        "1. Pick the learner's level (N5, A1, ...) and one everyday situation (helping at home, ordering food): "
        "it becomes the title, and `scene` takes one emoji for it.",
        "2. make_cast with two characters: built-ins by name (see `builtins`) or your own SVG (see `characters`). "
        "It is free and instant and returns a castId you can reuse across reels.",
        "3. Look at the preview: get_job(previewJobId), then get_artifact(previewJobId, \"cast.png\"). "
        "Fix a character that looks wrong before writing lines for it.",
        "4. Write 3 to 12 short lines (see `lines`), each spoken by one of the two cast ids, with `cast` set to the castId.",
        "5. probe the inputs: it checks the script, the lint, the voices and the renderer and spends nothing.",
        "6. start_job in `storyboard` mode (the default) and look at storyboard.jpg: one still per line.",
        "7. start_job again in `full` mode for reel.mp4, captions.srt and reel.json.",
    ],
    "builtins": builtin_names(),
    "characters": {
        "contract": [
            f"One <svg> root with viewBox=\"{VIEW_BOX}\"; the character stands in it facing the viewer, "
            "filling most of it: feet near y=330-380, top of the head near y=60-120.",
            f"At most {MAX_BYTES:,} bytes ({MAX_BYTES // 1000} kB).",
            f"Required groups (ids): {', '.join(REQUIRED)}. The runtime shows and hides them: eyes-closed for blinks, "
            "mouth-open while the character speaks (it flaps with the voice), mouth-closed otherwise.",
            f"Optional groups: {', '.join(OPTIONAL)}. arm-left/arm-right wave while speaking; give each a "
            "data-pivot=\"x,y\" (the shoulder, in viewBox units) to rotate around. mouth-smile and eyes-happy are "
            "used for cheerful beats.",
            "Every alternate group (eyes-closed, mouth-open, mouth-smile, eyes-happy, eyes-surprised, sweat, "
            "sparkle) starts hidden with display=\"none\"; eyes-open and mouth-closed start shown.",
            "Each id is used once. Ids are prefixed per character when inlined, so both characters may use the same ids.",
        ],
        "refused": [
            "Anything starting <! or <?: comments, CDATA, <!DOCTYPE>/<!ENTITY> declarations, processing "
            "instructions and the <?xml ...?> line. Strip editor comments before passing the SVG.",
            "Two attributes on one element whose names differ only in case (id and ID), or href with xlink:href.",
            "Child elements inside <title> or <desc> (text only).",
            "The elements script, foreignObject, iframe, object, embed, audio, video, animate, animateTransform, "
            "animateMotion, set, and <style>: use inline style=\"...\" attributes instead (a <style> element would "
            "restyle the whole page). Motion belongs to the runtime, so no SVG or CSS animation.",
            "Any element that is not plain SVG (no HTML, no editor namespaces such as inkscape: or sodipodi:).",
            "Any on* event attribute (onload, onclick, ...).",
            "href / xlink:href that does not start with # - except <image> with data:image/png;base64, or "
            "data:image/jpeg;base64,.",
            "In any attribute or style: @import, @font-face, javascript:, url(...) that is not url(#id), "
            "image()/image-set(), and CSS backslash escapes.",
        ],
        "house_style": [
            "Chunky, cute and flat: a round body, big dark eyes with a white highlight, a small mouth, rosy blush.",
            "Outline everything in #3a2a3a at stroke-width 5-5.5 with round joins.",
            "A white sticker border: draw the silhouette once underneath in #fff with a wide (about 30) white stroke.",
            "Colour with CSS variables and a fallback, style=\"fill:var(--primary,#ffb3d1)\" (also --secondary, "
            "--accent), so a cast can recolour the character.",
            "A soft shadow or highlight at low opacity (#3a2a3a at 0.1, #fff at 0.75) is enough shading; no gradients needed.",
            "Keep the face around the middle of the viewBox (eyes near y=200-240) and arms clear of the body edge.",
        ],
        "example": EXAMPLE_SVG,
        "use": 'Pass it as {"kind": "svg", "svg": "<svg ...>"} in a character\'s "source".',
    },
    "lines": {
        "shape": "Each line is {speaker, text, reading, meaning}: speaker is a cast id, text is what is said in the "
                 "target language, meaning is its translation.",
        "ruby": {
            "rule": "Furigana: put the reading in braces right after a kanji run; it attaches to that run only.",
            "example": "手伝{てつだ}いましょうか。",
        },
        "reading": "For a non-Latin script, give `reading` as the romanised line (Tetsudai mashou ka.) so a beginner "
                   "can follow; leave it out for Latin scripts.",
        "length": "Keep every line short: one breath, at most about 15 characters of Japanese or 8 words. "
                  "3 to 12 lines; a reel of 6-8 lines lasts about 20-30 seconds.",
        "tips": [
            "Alternate speakers so the conversation moves; a question then its answer teaches best.",
            "Use the level's grammar and vocabulary only; repeat a useful phrase rather than adding new ones.",
            "`meaning` is natural English (or meaning_language), not a word-by-word gloss.",
        ],
    },
    "voices": {
        "rule": "A deployment serves one narration provider; every character's voice must name it "
                "(fake:..., macos:..., elevenlabs:..., cartesia:...). probe's `voice` check says which one is bound.",
        "advice": "Give the two characters different voice ids of that provider, so they sound like two people "
                  "(for example macos:Kyoko and macos:Reed (Japanese (Japan)) for Japanese; `say -v '?'` lists "
                  "the macOS voices). A voice that cannot speak the reel's language mumbles or skips it.",
        "recasting": "Voices are not part of the castId (only how the characters look and are named): calling "
                     "make_cast again with the same characters but other voices keeps the same castId and replaces "
                     "the saved voices, so every later reel that passes that castId speaks with the new voices.",
    },
}
