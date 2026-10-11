"""A small ASS/SRT writer for plain and karaoke-style captions.

`subtitles.py` stays what it is: the furigana-and-vocabulary-card machinery for
nadeshiko-cut. Recipes whose captions are "a line of text, with word timings" (quranic,
doodle, wordreel) use this instead — one style per `style_ref`, word spans rendered as
ASS karaoke so a highlight follows the audio.

A caption may also name an `emphasis` in its `meta`: a word or phrase to colour inside
the line. That is how a teaching reel shows *which* word a clip is about while the
karaoke fill tracks the voice — the two compose, because the colour is applied to the
emphasised word's own karaoke run rather than to the line as a whole.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from ..core.timeline import Caption, WordSpan

#: `caption.meta` key naming the word or phrase to highlight inside the line.
EMPHASIS_KEY = "emphasis"

#: What a token is, for the purpose of matching an emphasis against word spans:
#: letters, digits and internal apostrophes, with surrounding punctuation dropped so
#: `light,` and `light` are the same word.
_BARE = re.compile(r"^[^0-9a-z']+|[^0-9a-z']+$", re.IGNORECASE)

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: {width}
PlayResY: {height}
YCbCr Matrix: TV.601

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{styles}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
{events}
"""


@dataclass(frozen=True)
class CaptionStyle:
    name: str
    font: str = "Helvetica"
    size: int = 48
    primary: str = "&H00FFFFFF"
    highlight: str = "&H0060B0FF"  # BGR: warm gold
    outline_colour: str = "&H00000000"
    back_colour: str = "&H80000000"
    outline: float = 2.0
    shadow: float = 1.0
    alignment: int = 2  # bottom-centre
    margin_lr: int = 60
    margin_v: int = 120
    bold: int = 0
    karaoke: bool = False


def ass_colour(hex_colour: str, alpha: str = "00") -> str:
    """`#rrggbb` → ASS `&HAABBGGRR`."""
    value = (hex_colour or "").strip().lstrip("#")
    if len(value) != 6:
        value = "ffffff"
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha}{blue}{green}{red}".upper()


def ass_time(ms: int) -> str:
    ms = max(0, int(ms))
    hours, remainder = divmod(ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}.{millis // 10:02d}"


def _style_line(style: CaptionStyle) -> str:
    return (
        f"Style: {style.name},{style.font},{style.size},{style.primary},{style.highlight},"
        f"{style.outline_colour},{style.back_colour},{style.bold},0,0,0,100,100,0,0,1,"
        f"{style.outline},{style.shadow},{style.alignment},{style.margin_lr},{style.margin_lr},"
        f"{style.margin_v},1"
    )


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def _bare(text: str) -> str:
    return _BARE.sub("", (text or "").lower())


def _emphasis_run(words: Sequence[WordSpan], needle: str) -> set[int]:
    """Which word spans an emphasis covers — one word, or a run forming a phrase.

    Matched against the spans rather than against the rendered text so the colour
    lands on exactly the characters the karaoke tags wrap, and never straddles a tag.
    """
    wanted = [part for part in (_bare(piece) for piece in (needle or "").split()) if part]
    if not wanted:
        return set()
    surfaces = [_bare(word.text) for word in words]
    width = len(wanted)
    covered: set[int] = set()
    for start in range(len(surfaces) - width + 1):
        if surfaces[start : start + width] == wanted:
            covered.update(range(start, start + width))
    return covered


def _decorate(text: str, emphasised: bool, style: CaptionStyle) -> str:
    """Escaped text, wrapped in the highlight colour when it carries the emphasis."""
    escaped = _escape(text)
    if not emphasised:
        return escaped
    return f"{{\\c{style.highlight}\\b1}}{escaped}{{\\c{style.primary}\\b0}}"


def _emphasis_text(text: str, needle: str, style: CaptionStyle) -> str:
    """Colour every occurrence of `needle` in a plain caption, escaping the rest."""
    target = (needle or "").lower()
    if not target:
        return _escape(text)
    lowered = (text or "").lower()
    parts: list[str] = []
    cursor = 0
    while True:
        found = lowered.find(target, cursor)
        if found < 0:
            break
        parts.append(_escape(text[cursor:found]))
        parts.append(_decorate(text[found : found + len(target)], True, style))
        cursor = found + len(target)
    parts.append(_escape(text[cursor:]))
    return "".join(parts)


def _karaoke(caption: Caption, style: CaptionStyle) -> str:
    """Word spans become ASS karaoke; the plain text is the fallback.

    An emphasis composes with karaoke instead of replacing it: the highlighted word
    keeps its own `\\k` run, so it still fills with the voice while staying a
    different colour from the rest of the line.
    """
    needle = str(caption.meta.get(EMPHASIS_KEY) or "")
    if not style.karaoke or not caption.words:
        return _emphasis_text(caption.text, needle, style)
    marked = _emphasis_run(caption.words, needle)
    parts: list[str] = []
    for index, word in enumerate(caption.words):
        centis = max(1, int(round((word.end_ms - word.start_ms) / 10)))
        parts.append(f"{{\\k{centis}}}{_decorate(word.text, index in marked, style)}")
    return " ".join(parts)


def build_ass(
    captions: Sequence[Caption],
    *,
    styles: Mapping[str, CaptionStyle],
    width: int = 1080,
    height: int = 1920,
    title: str = "reel",
) -> str:
    if not styles:
        raise ValueError("at least one caption style is required")
    style_lines = "\n".join(_style_line(style) for style in styles.values())
    events: list[str] = []
    for caption in captions:
        style = styles.get(caption.style_ref) or next(iter(styles.values()))
        text = _karaoke(caption, style)
        events.append(
            f"Dialogue: 0,{ass_time(caption.start_ms)},{ass_time(caption.end_ms)},"
            f"{style.name},{_escape(str(caption.meta.get('source', '')))},0,0,0,,{text}"
        )
    return ASS_HEADER.format(
        width=width, height=height, styles=style_lines, events="\n".join(events)
    )


def srt_time(ms: int) -> str:
    ms = max(0, int(ms))
    hours, remainder = divmod(ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def write_srt(captions: Sequence[Caption], path: Path) -> Path:
    blocks = []
    for index, caption in enumerate(captions, start=1):
        blocks.append(
            f"{index}\n{srt_time(caption.start_ms)} --> {srt_time(caption.end_ms)}\n"
            f"{caption.text}\n"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def write_ass(path: Path, captions: Sequence[Caption], **kwargs) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_ass(captions, **kwargs), encoding="utf-8")
    return path
