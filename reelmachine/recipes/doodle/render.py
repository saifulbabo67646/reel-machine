"""Compose a doodle reel: scene tracks + narration + burned captions, one pass."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from ... import ffmpeg
from ...config import Settings
from ...render.audio import audio_filter
from ...render.captions import CaptionStyle, ass_colour

GEOMETRY = {"vertical": (1080, 1920), "landscape": (1920, 1080)}


def caption_styles(style: Any, *, aspect: str) -> dict[str, CaptionStyle]:
    palette = getattr(style, "palette", {}) or {}
    fonts = getattr(style, "fonts", {}) or {}
    params = getattr(style, "params", {}) or {}
    vertical = aspect == "vertical"
    caption = CaptionStyle(
        name="Caption",
        font=str(fonts.get("caption", "Helvetica")),
        size=int(params.get("caption_size", 44)),
        primary=ass_colour(str(palette.get("ink", "#1b1b1b"))),
        outline_colour=ass_colour(str(palette.get("paper", "#ffffff"))),
        back_colour="&H00000000",
        outline=3.0,
        shadow=0.0,
        alignment=2,
        margin_v=160 if vertical else 90,
    )
    keyword = CaptionStyle(
        name="Keyword",
        font=str(fonts.get("title", "Helvetica")),
        size=int(params.get("keyword_size", 56)),
        primary=ass_colour(str(palette.get("accent", "#d94f30"))),
        outline_colour=ass_colour(str(palette.get("paper", "#ffffff"))),
        outline=3.0,
        shadow=0.0,
        alignment=8 if vertical else 9,  # top-centre / top-left
        margin_v=120,
    )
    return {"doodle.caption": caption, "doodle.keyword": keyword}


def normalise_track(
    source: Path,
    dest: Path,
    *,
    width: int,
    height: int,
    fps: int,
    settings: Settings,
    duration_ms: int | None = None,
    cancel: Any = None,
) -> Path:
    """Force one scene track to the reel's exact geometry, frame rate and duration.

    The narration window is the contract: a renderer that runs long (the stroke runtime
    deliberately holds the finished picture for half a second) is trimmed to it here.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        settings.ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-i", str(source),
        "-vf", (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=white,fps={fps}"
        ),
        "-an",
    ]
    if duration_ms:
        cmd += ["-t", f"{duration_ms / 1000.0:.3f}"]
    cmd += [
        "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
        "-pix_fmt", "yuv420p", str(dest),
    ]
    ffmpeg.run(cmd, cancel=cancel)
    return dest


def compose_doodle(
    tracks: Sequence[Path],
    narration: Path,
    ass_path: Path,
    dest: Path,
    *,
    settings: Settings,
    profile: str,
    width: int,
    height: int,
    fps: int = 30,
    cancel: Any = None,
) -> Path:
    if not tracks:
        raise ValueError("no scene tracks to compose")
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [settings.ffmpeg, "-hide_banner", "-nostdin", "-y"]
    for track in tracks:
        cmd += ["-i", str(track)]
    cmd += ["-i", str(narration)]

    lines = []
    for index in range(len(tracks)):
        lines.append(f"[{index}:v]fps={fps},format=yuv420p[v{index}]")
    concat_inputs = "".join(f"[v{index}]" for index in range(len(tracks)))
    lines.append(f"{concat_inputs}concat=n={len(tracks)}:v=1:a=0[vcat]")
    escaped = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    lines.append(f"[vcat]ass='{escaped}'[v]")
    audio_line = f"[{len(tracks)}:a]"
    filter_name = audio_filter(profile)
    if filter_name:
        lines.append(f"{audio_line}{filter_name}[a]")
    else:
        lines.append(f"{audio_line}anull[a]")

    cmd += ["-filter_complex", ";".join(lines), "-map", "[v]", "-map", "[a]"]
    cmd += [
        "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-shortest", "-movflags", "+faststart", str(dest),
    ]
    ffmpeg.run(cmd, cancel=cancel)
    return dest
