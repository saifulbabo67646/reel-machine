"""A small ASS/SRT writer for plain and karaoke-style captions.

`subtitles.py` stays what it is: the furigana-and-vocabulary-card machinery for
nadeshiko-cut. Recipes whose captions are "a line of text, with word timings" (quranic,
doodle) use this instead — one style per `style_ref`, word spans rendered as ASS
karaoke so a highlight follows the audio.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from ..core.timeline import Caption

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


def _karaoke(caption: Caption, style: CaptionStyle) -> str:
    """Word spans become ASS karaoke; the plain text is the fallback."""
    if not style.karaoke or not caption.words:
        return _escape(caption.text)
    parts: list[str] = []
    for word in caption.words:
        centis = max(1, int(round((word.end_ms - word.start_ms) / 10)))
        parts.append(f"{{\\k{centis}}}{_escape(word.text)}")
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
