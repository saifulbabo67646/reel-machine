"""Render a quranic timeline: background, recitation, burned captions.

One ffmpeg pass. The audio mastering profile is a filter, so a deployment can change the
loudness target without touching the picture.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ... import ffmpeg
from ...config import Settings
from ...core.timeline import Caption
from ...render.audio import audio_filter
from ...render.captions import CaptionStyle, ass_colour


def caption_styles(style: Any, *, aspect: str) -> dict[str, CaptionStyle]:
    """Two styles: the Arabic line (karaoke) and the translation under it."""
    palette = style.palette or {}
    fonts = style.fonts or {}
    params = style.params or {}
    arabic_size = int(params.get("arabic_size", 64))
    translation_size = int(params.get("translation_size", 32))
    outline = float(params.get("outline", 2))
    vertical = aspect in ("vertical", "square")
    arabic = CaptionStyle(
        name="Quranic",
        font=str(fonts.get("arabic", "Amiri Quran")),
        size=arabic_size,
        primary=ass_colour(str(palette.get("arabic", "#f4ead8"))),
        highlight=ass_colour(str(palette.get("highlight", "#d8b25c"))),
        outline_colour="&H00000000",
        outline=outline,
        shadow=float(params.get("shadow", 1)),
        alignment=5 if vertical else 2,
        margin_v=520 if vertical else 160,
        karaoke=True,
    )
    translation = CaptionStyle(
        name="Translation",
        font=str(fonts.get("translation", "Helvetica")),
        size=translation_size,
        primary=ass_colour(str(palette.get("translation", "#cfd6dc"))),
        highlight=ass_colour(str(palette.get("highlight", "#d8b25c"))),
        outline_colour="&H00000000",
        outline=max(1.0, outline - 1),
        shadow=float(params.get("shadow", 1)),
        alignment=2,
        margin_v=180,
        karaoke=False,
    )
    return {"quranic.arabic": arabic, "quranic.translation": translation}


def _escape_ass_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render_quranic(
    *,
    background: Path,
    audio: Path,
    ass_path: Path,
    dest: Path,
    profile: str,
    settings: Settings,
    cancel: Any = None,
) -> Path:
    """Mux background + recitation + burned captions into `dest`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        settings.ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(background),
        "-i",
        str(audio),
    ]
    filters = [f"ass='{_escape_ass_path(ass_path)}'"]
    cmd += ["-vf", ",".join(filters)]
    mastering = audio_filter(profile)
    if mastering:
        cmd += ["-af", mastering]
    cmd += [
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        settings.preset,
        "-crf",
        str(settings.crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-shortest",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    ffmpeg.run(cmd, cancel=cancel)
    return dest


def captions_and_words(captions: list[Caption]) -> tuple[int, int]:
    """`(captions, words)` — used by the verifier."""
    return len(captions), sum(len(caption.words) for caption in captions)
