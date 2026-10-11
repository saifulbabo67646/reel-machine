"""Render a wordreel: the example clips joined, with the target word named.

One ffmpeg pass (`ffmpeg.build_reel_video`) concatenates the cuts, burns the ASS and
loudness-normalises the dialogue. The reel is three caption layers and nothing else:

* the **word** — the target, pinned at the top for the whole reel, so a viewer who
  joins halfway still knows what the reel is teaching;
* the **gloss** — the meaning, under the word, for the opening seconds only;
* the **line** — what is being said, karaoke-timed, with the target coloured inside it.

Keeping the word and gloss as captions rather than as a composed card is what lets a
vertical reel work at every aspect: the video uses the whole frame, and the text sits
over it, so `original` footage does not have to lose a band to a card it does not need.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ... import ffmpeg
from ...render.captions import CaptionStyle, ass_colour

#: style_ref values the recipe's captions use.
WORD_STYLE = "wordreel.word"
GLOSS_STYLE = "wordreel.gloss"
LINE_STYLE = "wordreel.line"

GEOMETRY = {
    "vertical": (1080, 1920),
    "square": (1080, 1080),
    "landscape": (1920, 1080),
}

#: Cut at this height and let the compositor fit it; matches the other recipes.
CLIP_HEIGHT = 1080
FPS = 30

#: How long the meaning stays on screen. Long enough to read, short enough that the
#: first example is not competing with a caption that is no longer about it.
GLOSS_MS = 3_500


def caption_styles(settings: Any, *, aspect: str) -> dict[str, CaptionStyle]:
    """The three layers, scaled to the frame the reel is rendered at."""
    font = str(getattr(settings, "font_en", "") or "Helvetica")
    width, height = GEOMETRY.get(aspect, GEOMETRY["vertical"])
    portrait = height >= width
    # a square or landscape frame is shorter, so the type has to come down with it
    scale = 1.0 if portrait else 0.78
    return {
        WORD_STYLE: CaptionStyle(
            name="Word",
            font=font,
            size=int(96 * scale),
            primary=ass_colour("#ffffff"),
            highlight=ass_colour("#ffd166"),
            outline=3.4,
            shadow=1.0,
            alignment=8,           # top-centre
            margin_v=int(170 * scale),
            bold=1,
        ),
        GLOSS_STYLE: CaptionStyle(
            name="Gloss",
            font=font,
            size=int(46 * scale),
            primary=ass_colour("#c9d4e0"),
            highlight=ass_colour("#ffd166"),
            outline=2.2,
            shadow=0.8,
            alignment=8,
            margin_v=int(292 * scale),
        ),
        LINE_STYLE: CaptionStyle(
            name="Line",
            font=font,
            size=int(58 * scale),
            primary=ass_colour("#ffffff"),
            highlight=ass_colour("#ffd166"),
            outline=3.0,
            shadow=1.2,
            alignment=2,           # bottom-centre
            margin_v=int(300 * scale),
            bold=1,
            karaoke=True,
        ),
    }


def render_reel(
    clips: list[Path],
    dest: Path,
    *,
    ass_path: Path,
    aspect: str,
    settings: Any,
    cancel: Any = None,
) -> Path:
    """Join the cuts and burn the captions in a single pass."""
    width, height = GEOMETRY.get(aspect, GEOMETRY["vertical"])
    return ffmpeg.build_reel_video(
        clips,
        dest,
        ass_path=ass_path,
        aspect=aspect,
        width=width,
        height=height,
        fps=FPS,
        crf=settings.crf,
        preset=settings.preset,
        cancel=cancel,
    )
