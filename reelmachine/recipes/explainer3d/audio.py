"""Sound for a reel: synthetic SFX (no licences to track), padded speech, and the mix.

SFX are made by ffmpeg from seeded noise and tones, so they are identical on every run.
Music, when given, sits under the voice and ducks while someone speaks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from ... import ffmpeg
from ...config import Settings
from ...render.audio import audio_filter

_SFX = {
    "whoosh": "anoisesrc=d=0.45:c=pink:a=0.5:s=7,highpass=f=400,lowpass=f=3000,afade=t=in:d=0.2,afade=t=out:st=0.2:d=0.25",
    "thud": "sine=f=70:d=0.35,afade=t=out:st=0.02:d=0.33,volume=1.5",
    "pop": "sine=f=880:d=0.08,afade=t=out:st=0:d=0.08",
    "pour": "anoisesrc=d=1.2:c=brown:a=0.4:s=11,lowpass=f=1200,afade=t=in:d=0.2,afade=t=out:st=0.9:d=0.3",
    "sprinkle": "anoisesrc=d=0.8:c=white:a=0.15:s=13,highpass=f=4000,afade=t=in:d=0.1,afade=t=out:st=0.5:d=0.3",
    "sizzle": "anoisesrc=d=1.0:c=white:a=0.12:s=17,bandpass=f=6000,afade=t=in:d=0.2,afade=t=out:st=0.7:d=0.3",
    "ding": "sine=f=1320:d=0.6,afade=t=out:st=0.05:d=0.55",
}
SFX_NAMES = tuple(_SFX)
_RATE = ["-ar", "48000", "-ac", "2"]


def synth_sfx(name: str, dest: Path, *, settings: Settings) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i", _SFX[name],
                *_RATE, str(dest)])
    return dest


def pad_speech(src: Path, dest: Path, *, lead_ms: int, total_ms: int, settings: Settings, cancel: Any = None) -> Path:
    """Place a line `lead_ms` into a window of exactly `total_ms`."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    seconds = f"{total_ms / 1000:.3f}"
    ffmpeg.run([
        settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(src),
        "-af", f"aresample=48000,adelay={lead_ms}:all=1,apad=whole_dur={seconds},atrim=0:{seconds}",
        *_RATE, str(dest),
    ], cancel=cancel)
    return dest


def mix(
    narration: Path,
    cues: Sequence[tuple[int, Path]],
    music: Path | None,
    dest: Path,
    *,
    total_ms: int,
    settings: Settings,
    profile: str,
    cancel: Any = None,
) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = f"{total_ms / 1000:.3f}"
    cmd = [settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(narration)]
    for _, path in cues:
        cmd += ["-i", str(path)]
    if music is not None:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
    fmt = "aformat=sample_rates=48000:channel_layouts=stereo"
    lines = [f"[0:a]{fmt},asplit=2[voice][key]"]
    parts = ["[voice]"]
    for index, (at_ms, _) in enumerate(cues, start=1):
        lines.append(f"[{index}:a]{fmt},adelay={at_ms}:all=1,volume=0.55[s{index}]")
        parts.append(f"[s{index}]")
    if music is not None:
        m = len(cues) + 1
        lines.append(f"[{m}:a]{fmt},atrim=0:{total},volume=0.35[bed]")
        lines.append("[bed][key]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck]")
        parts.append("[duck]")
    else:
        lines.append("[key]anullsink")
    lines.append(f"{''.join(parts)}amix=inputs={len(parts)}:normalize=0:duration=first[mixed]")
    profile_filter = audio_filter(profile)
    lines.append(f"[mixed]{profile_filter + ',' if profile_filter else ''}apad=whole_dur={total},atrim=0:{total}[out]")
    cmd += ["-filter_complex", ";".join(lines), "-map", "[out]", *_RATE, str(dest)]
    ffmpeg.run(cmd, cancel=cancel)
    return dest
