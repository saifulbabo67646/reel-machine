"""Verify a finished explainer3d reel by inspecting the file itself."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from ... import ffmpeg
from ...config import Settings
from ...core.manifest import CheckResult, VerificationReport

MOTION_MIN = 0.5  # mean absolute grey difference (0–255) between a shot's early and late frames
_W, _H = 108, 192


def _grey(video: Path, at_s: float, settings: Settings, *, strict: bool = False) -> np.ndarray | None:
    """One frame, downscaled and grey. An unreadable frame is black, or None when `strict`."""
    proc = subprocess.run(
        [settings.ffmpeg, "-hide_banner", "-nostdin", "-ss", f"{at_s:.3f}", "-i", str(video),
         "-frames:v", "1", "-vf", f"scale={_W}:{_H}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    pixels = np.frombuffer(proc.stdout or b"", dtype=np.uint8)
    if pixels.size != _W * _H:
        return None if strict else np.zeros((_H, _W), np.float32)
    return pixels.reshape(_H, _W).astype(np.float32)


def motion_score(video: Path, start_s: float, end_s: float, *, settings: Settings) -> float:
    """How much the picture changes across a window, ignoring the title and caption bands."""
    span = end_s - start_s
    first = _grey(video, start_s + span * 0.15, settings)
    last = _grey(video, start_s + span * 0.85, settings)
    top, bottom = int(_H * 0.12), int(_H * 0.70)
    return float(np.abs(first[top:bottom] - last[top:bottom]).mean())


def verify_explainer3d(ctx: Any, outputs: dict[str, Any]) -> VerificationReport:
    settings: Settings = ctx.config
    render, compose = outputs.get("render"), outputs.get("compose")
    if render is None or compose is None:  # storyboard mode: nothing to verify yet
        return VerificationReport(ok=True, render_mode="hyperframes", notes="storyboard")
    video = Path(render.video)
    info = ffmpeg.probe(video)
    timeline = compose.timeline
    checks = [
        CheckResult(name="format", ok=(info.width, info.height) == (1080, 1920) and info.has_video and info.has_audio,
                    detail=f"{info.width}x{info.height} video={info.has_video} audio={info.has_audio}"),
        CheckResult(name="duration", ok=abs(info.duration_s * 1000 - timeline.duration_ms) <= 100,
                    detail=f"file {info.duration_s * 1000:.0f} ms, narration {timeline.duration_ms} ms"),
    ]
    frozen = []
    for clip in timeline.video:
        score = motion_score(video, clip.start_ms / 1000, (clip.start_ms + clip.duration_ms) / 1000, settings=settings)
        if score < MOTION_MIN:
            frozen.append(f"{clip.element_id} ({score:.2f})")
    checks.append(CheckResult(name="motion", ok=not frozen,
                              detail="frozen: " + ", ".join(frozen) if frozen else "every shot moves"))
    models = compose.prep.shots.narration.models
    unlicensed = sorted(k for k, m in models.items() if not m.licence.name)
    checks.append(CheckResult(name="licences", ok=not unlicensed, detail=", ".join(unlicensed) or "every model licensed"))
    renderers = {t.renderer for t in compose.prep.tracks}
    checks.append(CheckResult(name="renderer", ok=renderers == {compose.prep.renderer},
                              detail=f"tracks by {sorted(renderers)}, manifest names {compose.prep.renderer!r}"))
    return VerificationReport(ok=all(c.ok for c in checks), checks=checks, render_mode="hyperframes")
