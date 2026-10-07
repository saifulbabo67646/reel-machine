"""Render a storyreel: many micro-clips, the narration, burned captions — one pass.

The picture is the film's own footage, cropped full-bleed to the reel's frame (or
letterboxed over a soft fill, when the caller prefers to see the whole frame). The
sound is only the voiceover (plus an optional music bed) — the film's dialogue is
never used, which keeps every section's timing exactly where the voiceover put it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ... import ffmpeg
from ...render.audio import audio_filter
from ...render.captions import CaptionStyle, ass_colour


def caption_styles(style: Any, *, aspect: str) -> dict[str, CaptionStyle]:
    """Two styles: the spoken line (karaoke, bottom) and the short on-screen overlay."""
    palette = style.palette or {}
    fonts = style.fonts or {}
    params = style.params or {}
    outline = float(params.get("outline", 2.4))
    voice = CaptionStyle(
        name="Voice",
        font=str(fonts.get("caption", "Helvetica")),
        size=int(params.get("caption_size", 54)),
        primary=ass_colour(str(palette.get("caption", "#ffffff"))),
        highlight=ass_colour(str(palette.get("highlight", "#ffd166"))),
        outline_colour="&H00000000",
        outline=outline,
        shadow=float(params.get("shadow", 1.0)),
        alignment=2,
        margin_v=int(params.get("caption_margin", 280)),
        bold=1,
        karaoke=True,
    )
    overlay = CaptionStyle(
        name="Overlay",
        font=str(fonts.get("overlay", fonts.get("caption", "Helvetica"))),
        size=int(params.get("overlay_size", 62)),
        primary=ass_colour(str(palette.get("overlay", "#ffffff"))),
        highlight=ass_colour(str(palette.get("highlight", "#ffd166"))),
        outline_colour="&H00000000",
        outline=max(1.0, outline - 1.0),
        shadow=float(params.get("shadow", 1.0)),
        alignment=8,  # top-centre: the voice line owns the bottom
        margin_v=int(params.get("overlay_margin", 260)),
        bold=1,
        karaoke=False,
    )
    return {"storyreel.voice": voice, "storyreel.overlay": overlay}


def _escape(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def concat_clips(clips: list[Path], dest: Path, *, settings: Any, cancel: Any = None) -> Path:
    """Join the normalised clips with the concat demuxer — a stream copy, no re-encode.

    Every clip comes from the same cutter with the same profile, resolution, frame
    rate and pixel format, so this is lossless and fast; it also keeps the final
    pass's filtergraph down to a single video input however many clips a 5-10 minute
    reel is built from.
    """
    listing = dest.parent / "story-video-concat.txt"
    listing.write_text(
        "".join(f"file '{clip.resolve()}'\n" for clip in clips), encoding="utf-8"
    )
    ffmpeg.run(
        [
            settings.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c",
            "copy",
            str(dest),
        ],
        cancel=cancel,
    )
    return dest


def render_storyreel(
    *,
    clips: list[Path],
    narration: Path,
    music: Path | None,
    ass_path: Path,
    dest: Path,
    width: int,
    height: int,
    fps: int = 30,
    fit: str = "crop",
    profile: str = "tiktok",
    duration_ms: int = 0,
    settings: Any = None,
    workdir: Path | None = None,
    cancel: Any = None,
) -> Path:
    """Two passes: clips joined losslessly, then cropped, captioned and mixed once.

    The film's own audio is never used — the voiceover is the only source of sound
    (plus an optional music bed), which keeps every section exactly where the
    narration put it.
    """
    if not clips:
        raise ValueError("no clips to render")
    if settings is None:
        from ...config import get_settings

        settings = get_settings()
    dest.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(workdir) if workdir is not None else dest.parent
    workdir.mkdir(parents=True, exist_ok=True)

    joined = concat_clips(clips, workdir / "story-video.mp4", settings=settings, cancel=cancel)

    cmd = [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    cmd += ["-i", str(joined)]
    cmd += ["-i", str(narration)]
    music_index = None
    if music is not None:
        music_index = 2
        cmd += ["-stream_loop", "-1", "-i", str(music)]

    parts: list[str] = []
    if fit == "band":
        parts.append(
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},gblur=sigma=24,eq=brightness=-0.08[bgz];"
            f"[fg]scale={width}:-2:flags=lanczos[fs];"
            f"[bgz][fs]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps},format=yuv420p[vcat]"
        )
    else:
        parts.append(
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase:"
            f"flags=lanczos,crop={width}:{height},setsar=1,fps={fps},format=yuv420p[vcat]"
        )
    parts.append(f"[vcat]ass='{_escape(ass_path)}'[vout]")

    parts.append(
        "[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        "asetpts=N/SR/TB[anar]"
    )
    audio_label = "[anar]"
    if music_index is not None:
        parts.append(
            f"[{music_index}:a]aformat=sample_fmts=fltp:sample_rates=48000:"
            f"channel_layouts=stereo,volume=-20dB[amus]"
        )
        parts.append(
            "[anar][amus]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[amix]"
        )
        audio_label = "[amix]"
    mastering = audio_filter(profile)
    if mastering:
        parts.append(f"{audio_label}{mastering}[aout]")
    else:
        parts.append(f"{audio_label}anull[aout]")

    cmd += ["-filter_complex", ";".join(parts)]
    cmd += ["-map", "[vout]", "-map", "[aout]"]
    cmd += [
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-crf",
        str(settings.crf),
        "-preset",
        settings.preset,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
    ]
    if duration_ms > 0:
        cmd += ["-t", f"{duration_ms / 1000:.3f}"]
    cmd += ["-y", str(dest)]
    ffmpeg.run(cmd, cancel=cancel)
    return dest
