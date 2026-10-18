"""Join rendered shots — a crossfade where a new camera starts, a hard cut where the
camera continues — and mux the final audio."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from ... import ffmpeg
from ...config import Settings
from .timing import XFADE_MS


def join_shots(
    tracks: Sequence[Path],
    joins: Sequence[str],
    nominal_ms: Sequence[int],
    dest: Path,
    *,
    settings: Settings,
    fps: int = 30,
    cancel: Any = None,
) -> Path:
    if not tracks:
        raise ValueError("no shots to join")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [settings.ffmpeg, "-hide_banner", "-nostdin", "-y"]
    for track in tracks:
        cmd += ["-i", str(track)]
    lines = [
        f"[{i}:v]fps={fps},scale=1080:1920,setsar=1,format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[v{i}]"
        for i in range(len(tracks))
    ]
    current, elapsed = "[v0]", nominal_ms[0]
    for i in range(1, len(tracks)):
        label = f"[j{i}]"
        if joins[i] == "xfade":
            lines.append(
                f"{current}[v{i}]xfade=transition=fade:duration={XFADE_MS / 1000:.3f}:offset={elapsed / 1000:.3f}{label}"
            )
        else:
            lines.append(f"{current}[v{i}]concat=n=2:v=1:a=0{label}")
        current, elapsed = label, elapsed + nominal_ms[i]
    lines.append(f"{current}trim=duration={elapsed / 1000:.3f}[out]")
    cmd += ["-filter_complex", ";".join(lines), "-map", "[out]",
            "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
            "-pix_fmt", "yuv420p", "-r", str(fps), str(dest)]
    ffmpeg.run(cmd, cancel=cancel)
    return dest


def mux(video: Path, audio: Path, dest: Path, *, total_ms: int, settings: Settings, cancel: Any = None) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run([
        settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(video), "-i", str(audio),
        "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-t", f"{total_ms / 1000:.3f}", "-movflags", "+faststart", str(dest),
    ], cancel=cancel)
    return dest
