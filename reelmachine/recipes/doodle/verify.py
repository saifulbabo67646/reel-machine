"""Verify a finished doodle reel by inspecting the real file.

Rendering without error is not the same as a correct reel: the audio and video streams
must both be there, the caption timeline must sit inside the picture, every scene must
have produced a track, the final frame must not be blank, and the manifest's render mode
must match the renderer that actually produced the tracks.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from ... import ffmpeg
from ...config import Settings
from ...core.errors import VerificationFailed
from ...core.manifest import CheckResult, VerificationReport

BLANK_STDDEV = 3.0


def last_frame_stddev(video: Path, *, settings: Settings) -> float:
    """Decode the final frame as grey pixels and return its contrast.

    Raw video needs a binary pipe: `ffmpeg.run` decodes stdout as text and would mangle
    the pixels before they are ever measured.
    """
    proc = subprocess.run(
        [
            settings.ffmpeg, "-hide_banner", "-nostdin",
            "-sseof", "-0.2", "-i", str(video),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise VerificationFailed(
            "the final frame could not be decoded",
            hint="the render may be truncated; re-run the job",
            details={"stderr": (proc.stderr or b"").decode("utf-8", "ignore")[-300:]},
        )
    pixels = np.frombuffer(proc.stdout or b"", dtype=np.uint8)
    if pixels.size == 0:
        return 0.0
    return float(pixels.std())


INK_LEVEL = 160


def _art_mask(art: Path, size: tuple[int, int]) -> np.ndarray:
    """Where the source art has ink — the hand that draws it is not part of the art."""
    from PIL import Image

    with Image.open(art).convert("L") as image:
        if image.size != size:
            image = image.resize(size)
        return np.asarray(image) < INK_LEVEL


def ink_growth(
    video: Path,
    *,
    settings: Settings,
    art: Path | None = None,
    samples: int = 3,
    start_s: float = 0.1,
    end_s: float = 0.9,
) -> tuple[bool, list[int]]:
    """Sample ink across a clip; `(grew, counts)`.

    "It produced a file" is not the same as "it drew something". With `art` given, only
    pixels where the source art has ink are counted — the runtime draws a photographic
    hand over the canvas, and counting that hand would confuse "the hand moved" with
    "the picture was drawn".
    """
    counts: list[int] = []
    span = max(0.0, end_s - start_s)
    mask: np.ndarray | None = None
    for index in range(max(2, samples)):
        at_s = start_s + span * index / (max(2, samples) - 1)
        proc = subprocess.run(
            [
                settings.ffmpeg, "-hide_banner", "-nostdin",
                "-ss", f"{at_s:.3f}", "-i", str(video),
                "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.returncode != 0:
            return False, counts
        pixels = np.frombuffer(proc.stdout or b"", dtype=np.uint8)
        dark = pixels < INK_LEVEL
        if art is not None:
            if mask is None:
                info = ffmpeg.probe(video)
                mask = _art_mask(art, (info.width, info.height))
            if mask.size == dark.size:
                dark = dark.reshape(mask.shape) & mask
        counts.append(int(dark.sum()))
    if len(counts) < 2:
        return False, counts
    grew = counts[-1] >= counts[0] + max(200, int(0.05 * max(1, counts[-1])))
    return grew, counts


def verify_doodle(ctx: Any, outputs: dict[str, Any]) -> VerificationReport:
    settings: Settings = ctx.config
    compose = outputs.get("compose")
    prep = outputs.get("prep")
    render = outputs.get("render")
    checks: list[CheckResult] = []
    degradation: list[str] = []

    if compose is None or prep is None or render is None:
        return VerificationReport(
            ok=False,
            checks=[CheckResult(name="stages", ok=False, detail="compose/prep/render did not all run")],
            render_mode=getattr(render, "render_mode", ""),
        )

    video = Path(render.video)
    checks.append(
        CheckResult(name="output", ok=video.is_file() and video.stat().st_size > 0, detail=str(video))
    )
    info = ffmpeg.probe(video)
    checks.append(
        CheckResult(
            name="streams",
            ok=info.has_video and info.has_audio,
            detail=f"video={info.has_video} audio={info.has_audio} {info.vcodec}/{info.acodec}",
        )
    )
    checks.append(
        CheckResult(
            name="duration",
            ok=abs(info.duration_s * 1000 - prep.duration_ms) <= 1200,
            detail=f"container {info.duration_s * 1000:.0f} ms vs timeline {prep.duration_ms} ms",
        )
    )

    captions = compose.timeline.captions
    if captions:
        last_end = max(caption.end_ms for caption in captions)
        checks.append(
            CheckResult(
                name="caption_sync",
                ok=last_end <= prep.duration_ms + 250,
                detail=f"last cue at {last_end} ms of {prep.duration_ms} ms",
            )
        )
    else:
        checks.append(CheckResult(name="caption_sync", ok=not compose.inputs.captions, detail="no captions"))

    expected_scenes = len(outputs["scenes"].scenes) if outputs.get("scenes") else len(prep.tracks)
    track_total = sum(track.duration_ms for track in prep.tracks)
    checks.append(
        CheckResult(
            name="scene_boundaries",
            ok=len(prep.tracks) == expected_scenes and abs(track_total - prep.duration_ms) <= 1500,
            detail=f"{len(prep.tracks)}/{expected_scenes} track(s), {track_total} ms of {prep.duration_ms} ms",
        )
    )

    mode = render.render_mode
    renderers = {track.renderer for track in prep.tracks}
    checks.append(
        CheckResult(
            name="render_mode",
            ok=bool(mode) and renderers == {mode} and compose.timeline.render_mode == mode,
            detail=f"manifest {mode!r}, tracks {sorted(renderers)}",
        )
    )
    checks.append(
        CheckResult(
            name="final_frame",
            ok=last_frame_stddev(video, settings=settings) >= BLANK_STDDEV,
            detail="the last frame must not be blank",
        )
    )

    narration = outputs.get("narration")
    if narration is not None and getattr(narration, "degraded", False):
        degradation.append(getattr(narration, "degradation_note", "degraded narration"))
    preflight = prep.preflight or {}
    if preflight.get("skipped"):
        degradation.append("preflight was disabled")

    return VerificationReport(
        ok=all(check.ok for check in checks),
        checks=checks,
        render_mode=mode,
        visible_degradation=degradation,
    )
