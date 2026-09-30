"""Thin, dependency-free wrappers around ffmpeg / ffprobe.

Everything the pipeline needs to read audio, probe a stream, cut a clip and
join clips goes through here so the rest of the code never builds a command
line by hand.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

from .config import get_settings


class FFmpegError(RuntimeError):
    def __init__(self, cmd: list[str], returncode: int, stderr: str):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        tail = "\n".join(stderr.strip().splitlines()[-12:])
        super().__init__(f"command failed ({returncode}): {shlex.join(cmd)}\n{tail}")


def run(cmd: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise FFmpegError(cmd, proc.returncode, proc.stderr or "")
    return proc


def is_remote(src: str | Path) -> bool:
    return isinstance(src, str) and src.split("://", 1)[0] in {"http", "https"}


def is_hls(src: str | Path) -> bool:
    """True for an m3u8 URL, with or without a query string."""
    if not isinstance(src, str):
        return False
    path = src.split("://", 1)[-1].split("?", 1)[0]
    return path.lower().endswith(".m3u8")


@lru_cache(maxsize=1)
def hls_demuxer_options() -> frozenset[str]:
    """Which hls-demuxer private options this build actually has.

    `extension_picky` only appeared in ffmpeg 6.0, and passing an unknown
    private option is a hard error, so ask the binary once and cache it.
    """
    settings = get_settings()
    try:
        proc = subprocess.run(
            [settings.ffmpeg, "-hide_banner", "-h", "demuxer=hls"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    except OSError:
        return frozenset()
    output = proc.stdout or ""
    return frozenset(
        name
        for name in ("extension_picky", "allowed_extensions", "allowed_segment_extensions")
        if re.search(rf"^\s+-{name}\b", output, re.MULTILINE)
    )


def hls_input_args(src: str | Path) -> list[str]:
    """Extra demuxer options for HLS sources.

    Streaming CDNs routinely serve fMP4/CMAF segments with a fake `.jpg` (or
    `.png`) extension to defeat hotlink filters.  ffmpeg's HLS demuxer rejects
    those outright — "URL ... is not in allowed_segment_extensions" — unless the
    extension whitelist is widened *and* `extension_picky` is switched off,
    because a disguised extension can never match the demuxer's real one.
    """
    settings = get_settings()
    if not is_hls(src) or settings.hls_extension_picky:
        return []
    available = hls_demuxer_options()
    args: list[str] = []
    if "allowed_extensions" in available:
        args += ["-allowed_extensions", "ALL"]
    if "allowed_segment_extensions" in available:
        args += ["-allowed_segment_extensions", "ALL"]
    if "extension_picky" in available:
        args += ["-extension_picky", "false"]
    return args


def input_args(
    src: str | Path,
    *,
    headers: dict[str, str] | None = None,
    seek_s: float | None = None,
    duration_s: float | None = None,
    extra: list[str] | None = None,
    quiet: bool = True,
) -> list[str]:
    """Input flags, including the reconnect/header handling remote HLS needs.

    `quiet=False` keeps ffmpeg's info-level output, which is what the
    no-ffprobe probe fallback has to parse.
    """
    args: list[str] = ["-hide_banner", "-nostdin"]
    if quiet:
        args += ["-loglevel", "error"]
    if is_remote(src):
        args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        if headers:
            blob = "".join(f"{k}: {v}\r\n" for k, v in headers.items() if v)
            if blob:
                args += ["-headers", blob]
    args += hls_input_args(src)
    if seek_s is not None:
        args += ["-ss", f"{max(0.0, seek_s):.3f}"]
    if duration_s is not None:
        args += ["-t", f"{max(0.0, duration_s):.3f}"]
    args += list(extra or [])
    args += ["-i", str(src)]
    return args


# --------------------------------------------------------------------------- probe


@dataclass(slots=True)
class MediaInfo:
    duration_s: float
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_video: bool = False
    has_audio: bool = False
    vcodec: str = ""
    acodec: str = ""
    size_bytes: int = 0
    audio_tracks: int = 0
    audio_langs: list[str] = field(default_factory=list)  # language tag per audio stream

    @property
    def aspect(self) -> float:
        return (self.width / self.height) if self.height else 0.0


def _ratio(value: str | None) -> float:
    if not value or "/" not in value:
        try:
            return float(value or 0)
        except ValueError:
            return 0.0
    num, _, den = value.partition("/")
    try:
        den_f = float(den)
        return float(num) / den_f if den_f else 0.0
    except ValueError:
        return 0.0


def have_ffprobe() -> bool:
    import shutil

    return shutil.which(get_settings().ffprobe) is not None


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_RESOLUTION_RE = re.compile(r"(?<![\d.])(\d{2,5})x(\d{2,5})(?![\d.])")
_FPS_RE = re.compile(r"([\d.]+)\s*fps")
_HAS_VIDEO_RE = re.compile(r"Stream #\d+:\d+.*?:\s*Video:\s*(\w+)")
_HAS_AUDIO_RE = re.compile(r"Stream #\d+:\d+.*?:\s*Audio:\s*(\w+)")
_AUDIO_LANG_RE = re.compile(r"Stream #\d+:\d+(?:\((\w{3})\))?.*?:\s*Audio:")


def _probe_via_ffmpeg(src: str | Path, *, headers: dict[str, str] | None = None) -> MediaInfo:
    """Parse `ffmpeg -i` output.

    Plenty of static ffmpeg builds ship without a matching ffprobe; the decoder
    already prints everything needed, so fall back to reading it rather than
    making ffprobe a hard requirement.
    """
    settings = get_settings()
    cmd = [settings.ffmpeg]
    cmd += input_args(src, headers=headers, quiet=False)
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    output = proc.stderr or ""
    if "Invalid data found" in output or "No such file" in output or "Server returned" in output:
        raise FFmpegError(cmd, proc.returncode, output)

    info = MediaInfo(duration_s=0.0)
    if match := _DURATION_RE.search(output):
        hours, minutes, seconds = match.groups()
        info.duration_s = int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    for line in output.splitlines():
        if "Stream #" not in line or "attached pic" in line:
            continue
        if not info.has_video and (video := _HAS_VIDEO_RE.search(line)):
            info.has_video = True
            info.vcodec = video.group(1)
            if resolution := _RESOLUTION_RE.search(line):
                info.width, info.height = int(resolution.group(1)), int(resolution.group(2))
            if fps := _FPS_RE.search(line):
                info.fps = float(fps.group(1))
        elif audio := _HAS_AUDIO_RE.search(line):
            info.audio_tracks += 1
            lang = _AUDIO_LANG_RE.search(line)
            info.audio_langs.append((lang.group(1) or "").lower() if lang else "")
            if not info.has_audio:
                info.has_audio = True
                info.acodec = audio.group(1)

    if not info.has_video and not info.has_audio:
        raise FFmpegError(cmd, proc.returncode, output)
    return info


JAPANESE_TAGS = {"jpn", "ja", "jp", "japanese"}


def pick_japanese_track(info: MediaInfo) -> int:
    """Index of the Japanese audio track, or 0 when nothing is tagged.

    Dual-audio anime releases often put the dub first, so assuming track 0 would
    align a Japanese clip against English dialogue and cut the wrong audio.
    """
    for index, lang in enumerate(info.audio_langs):
        if lang in JAPANESE_TAGS:
            return index
    return 0


def probe(src: str | Path, *, headers: dict[str, str] | None = None) -> MediaInfo:
    if not have_ffprobe():
        return _probe_via_ffmpeg(src, headers=headers)

    settings = get_settings()
    cmd = [settings.ffprobe, "-hide_banner", "-loglevel", "error"]
    cmd += input_args(src, headers=headers, extra=["-show_format", "-show_streams"])
    cmd += ["-of", "json"]
    proc = run(cmd)
    payload = json.loads(proc.stdout or "{}")
    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []

    info = MediaInfo(duration_s=float(fmt.get("duration") or 0.0))
    try:
        info.size_bytes = int(fmt.get("size") or 0)
    except ValueError:
        info.size_bytes = 0

    for stream in streams:
        kind = stream.get("codec_type")
        if kind == "video" and not info.has_video and stream.get("disposition", {}).get("attached_pic") != 1:
            info.has_video = True
            info.width = int(stream.get("width") or 0)
            info.height = int(stream.get("height") or 0)
            info.vcodec = stream.get("codec_name") or ""
            info.fps = _ratio(stream.get("avg_frame_rate")) or _ratio(stream.get("r_frame_rate"))
            if not info.duration_s and stream.get("duration"):
                info.duration_s = float(stream["duration"])
        elif kind == "audio":
            info.audio_tracks += 1
            info.audio_langs.append(str((stream.get("tags") or {}).get("language") or "").lower())
            if not info.has_audio:
                info.has_audio = True
                info.acodec = stream.get("codec_name") or ""
    return info


def duration_s(src: str | Path, *, headers: dict[str, str] | None = None) -> float:
    return probe(src, headers=headers).duration_s


# ------------------------------------------------------------------ audio -> numpy


def read_pcm(
    src: str | Path,
    *,
    sample_rate: int = 16000,
    start_s: float | None = None,
    duration_s: float | None = None,
    headers: dict[str, str] | None = None,
    audio_track: int = 0,
) -> np.ndarray:
    """Decode any input to a mono float32 numpy array (no temp files)."""
    settings = get_settings()
    cmd = [settings.ffmpeg]
    cmd += input_args(src, headers=headers, seek_s=start_s, duration_s=duration_s)
    cmd += ["-vn", "-map", f"0:a:{audio_track}", "-ac", "1", "-ar", str(sample_rate)]
    cmd += ["-f", "f32le", "-acodec", "pcm_f32le", "-"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        raise FFmpegError(cmd, proc.returncode, proc.stderr.decode("utf-8", "replace"))
    return np.frombuffer(proc.stdout, dtype="<f4").astype(np.float32)


def envelope(
    src: str | Path,
    *,
    sample_rate: int = 16000,
    hop_ms: float = 10.0,
    start_s: float | None = None,
    duration_s: float | None = None,
    headers: dict[str, str] | None = None,
    audio_track: int = 0,
) -> np.ndarray:
    """RMS energy envelope, one value per `hop_ms`.  The alignment currency."""
    pcm = read_pcm(
        src,
        sample_rate=sample_rate,
        start_s=start_s,
        duration_s=duration_s,
        headers=headers,
        audio_track=audio_track,
    )
    hop = max(1, int(round(sample_rate * hop_ms / 1000.0)))
    if pcm.size < hop:
        return np.zeros(0, dtype=np.float64)
    usable = (pcm.size // hop) * hop
    frames = pcm[:usable].reshape(-1, hop).astype(np.float64)
    env = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    return np.log1p(env * 100.0)  # compress: loudness differences between encodes


# ------------------------------------------------------------------------- editing

# Everything is re-encoded to one target profile so clips can be concatenated
# without the stream-copy mismatches that silently desync audio.


def cut_clip(
    src: str | Path,
    dest: Path,
    *,
    start_s: float,
    duration_s: float,
    height: int = 1080,
    fps: int = 30,
    crf: int | None = None,
    preset: str = "medium",
    headers: dict[str, str] | None = None,
    audio_track: int = 0,
    video: bool = True,
) -> Path:
    """Cut `[start_s, start_s+duration_s]` from an episode into a normalised mp4.

    Both streams are padded (`tpad` clones the last frame, `apad` adds silence)
    and then cut to exactly `duration_s` by an *output* `-t`.  Without that the
    audio track comes out a few tens of ms shorter than the video on every clip,
    and the concat filter — which positions each stream independently — lets
    that error accumulate into real A/V drift across a reel.

    Timestamps are rebased to zero first.  HLS sources commonly start at a
    non-zero PTS (one real host reported `start: 0.083401`), and because an
    output `-t` is measured against those timestamps, every clip would come out
    short by exactly that offset — invisible per clip, cumulative across a reel.
    """
    settings = get_settings()
    dest.parent.mkdir(parents=True, exist_ok=True)
    pad_s = duration_s + 0.5
    cmd = [settings.ffmpeg]
    cmd += input_args(src, headers=headers, seek_s=start_s)
    cmd += ["-map", f"0:a:{audio_track}"]
    if video:
        cmd += ["-map", "0:v:0"]
        cmd += [
            "-vf",
            f"setpts=PTS-STARTPTS,scale=-2:{height}:flags=lanczos,fps={fps},setsar=1,"
            f"tpad=stop_mode=clone:stop_duration={pad_s:.3f}",
            "-c:v",
            "libx264",
            "-profile:v",
            "high",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            str(crf if crf is not None else settings.crf),
            "-preset",
            preset,
        ]
    else:
        cmd += ["-vn"]
    cmd += ["-af", f"asetpts=PTS-STARTPTS,apad=whole_dur={pad_s:.3f}"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    cmd += ["-t", f"{duration_s:.3f}"]  # output duration: trims the padding
    if video:
        # `-t` alone keeps the frame that *starts* before the limit, so a 1.667 s
        # clip came out 51 frames (1.700 s) instead of 50.  One extra frame per
        # clip is one extra frame of subtitle drift per clip.
        cmd += ["-frames:v", str(max(1, int(round(duration_s * fps))))]
    cmd += ["-avoid_negative_ts", "make_zero", "-movflags", "+faststart", "-y", str(dest)]
    run(cmd)
    return dest


def build_reel_video(
    clips: list[Path],
    dest: Path,
    *,
    ass_path: Path | None = None,
    aspect: str = "vertical",
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    crf: int | None = None,
    preset: str = "medium",
    loudnorm: bool = True,
    band_y: int | None = None,
    band_h: int | None = None,
    fonts_dir: Path | None = None,
) -> Path:
    """Concatenate normalised clips in one filtergraph, burn subs, pad, loudnorm.

    One pass on purpose: the concat demuxer plus a second encode pass is where
    A/V drift and subtitle-timing drift usually creep in.

    `band_y`/`band_h` place the sharp video within the frame.  A reel reserves
    the top for the vocabulary card and the bottom for the caption, so the
    video is fitted into the band between them rather than centred in the
    frame; the blurred fill still covers the whole thing.
    """
    settings = get_settings()
    if not clips:
        raise ValueError("no clips to render")
    dest.parent.mkdir(parents=True, exist_ok=True)

    cmd = [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    for clip in clips:
        cmd += ["-i", str(clip)]

    parts: list[str] = []
    video_labels: list[str] = []
    audio_labels: list[str] = []
    n = len(clips)
    if aspect == "vertical":
        top = 0 if band_y is None else max(0, int(band_y))
        room = (height - top) if band_h is None else max(2, int(band_h))
        for i in range(n):
            parts.append(
                f"[{i}:v]split=2[bg{i}][fg{i}];"
                f"[bg{i}]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},gblur=sigma=28,eq=brightness=-0.08[bgz{i}];"
                f"[fg{i}]scale={width}:{room}:force_original_aspect_ratio=decrease:flags=lanczos[fgs{i}];"
                f"[bgz{i}][fgs{i}]overlay=(W-w)/2:{top}+({room}-h)/2[v{i}]"
            )
            video_labels.append(f"[v{i}]")
    elif aspect == "square":
        for i in range(n):
            parts.append(
                f"[{i}:v]split=2[bg{i}][fg{i}];"
                f"[bg{i}]scale={width}:{width}:force_original_aspect_ratio=increase,"
                f"crop={width}:{width},gblur=sigma=28,eq=brightness=-0.08[bgz{i}];"
                f"[fg{i}]scale={width}:-2:flags=lanczos[fgs{i}];"
                f"[bgz{i}][fgs{i}]overlay=(W-w)/2:(H-h)/2[v{i}]"
            )
            video_labels.append(f"[v{i}]")
    else:  # original aspect
        for i in range(n):
            parts.append(f"[{i}:v]scale={width}:-2:flags=lanczos,setsar=1[v{i}]")
            video_labels.append(f"[v{i}]")

    # Route every audio input through a real, labelled filter.  Referencing the
    # streams by a bare label instead lets concat bind the wrong pad, which
    # silently shortens the joined audio and drifts A/V out of sync.
    for i in range(n):
        parts.append(
            f"[{i}:a:0]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"asetpts=N/SR/TB[a{i}]"
        )
        audio_labels.append(f"[a{i}]")

    # Labels must be attached to the filter that consumes them — a bare list of
    # labels on its own is parsed as an (empty) filter name.
    parts.append(
        "".join(v + a for v, a in zip(video_labels, audio_labels))
        + f"concat=n={n}:v=1:a=1[vcat][acat]"
    )

    vout = "[vcat]"
    if ass_path is not None:
        escaped = str(ass_path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        opts = f"ass='{escaped}'"
        if fonts_dir:
            opts += f":fontsdir='{str(fonts_dir).replace(chr(92), '/')}'"
        parts.append(f"[vcat]{opts}[vsub]")
        vout = "[vsub]"

    aout = "[acat]"
    if loudnorm:
        parts.append("[acat]loudnorm=I=-16:TP=-1.5:LRA=11[anorm]")
        aout = "[anorm]"

    filter_complex = ";".join(parts)
    cmd += ["-filter_complex", filter_complex]
    cmd += ["-map", vout, "-map", aout]
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
        str(crf if crf is not None else settings.crf),
        "-preset",
        preset,
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
        "-shortest",
        "-y",
        str(dest),
    ]
    run(cmd)
    return dest


def make_thumbnail(src: str | Path, dest: Path, *, at_s: float = 0.0, width: int = 640) -> Path:
    settings = get_settings()
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [settings.ffmpeg]
    cmd += input_args(src, seek_s=at_s, extra=["-frames:v", "1"])
    cmd += ["-vf", f"scale={width}:-2", "-y", str(dest)]
    run(cmd)
    return dest


def write_silent_wav(dest: Path, seconds: float, *, sample_rate: int = 48000) -> Path:
    import wave

    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sample_rate)
        fh.writeframes(b"\x00\x00" * int(seconds * sample_rate))
    return dest


def temp_audio(suffix: str = ".wav") -> Path:
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    handle.close()
    return Path(handle.name)
