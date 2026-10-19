"""Verify a finished techreel reel by inspecting the file itself: its format, its length and its sound."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ... import ffmpeg
from ...config import Settings
from ...core.manifest import CheckResult, VerificationReport
from ..dialoguereel.verify import check_audio
from .compose import HEIGHT, WIDTH

DURATION_TOLERANCE_MS = 100


def _mode(outputs: dict[str, Any]) -> str:
    try:
        return outputs["preview"].compose.narration.script.inputs.mode
    except (KeyError, AttributeError):
        return "storyboard"


def verify_techreel(ctx: Any, outputs: dict[str, Any]) -> VerificationReport:
    render = outputs.get("render")
    if render is None:  # storyboard mode: no reel to verify
        return VerificationReport(ok=True, render_mode="hyperframes", notes=_mode(outputs))
    settings: Settings = ctx.config
    planned = outputs["narration"].duration_ms
    video = Path(render.video)
    info = ffmpeg.probe(video)
    checks = [
        CheckResult(name="format",
                    ok=(info.width, info.height) == (WIDTH, HEIGHT) and info.has_video and info.has_audio,
                    detail=f"{info.width}x{info.height} video={info.has_video} audio={info.has_audio}"),
        CheckResult(name="duration", ok=abs(info.duration_s * 1000 - planned) <= DURATION_TOLERANCE_MS,
                    detail=f"file {info.duration_s * 1000:.0f} ms, schedule {planned} ms"),
        check_audio(video, settings, label="reel"),
    ]
    return VerificationReport(ok=all(c.ok for c in checks), checks=checks, render_mode="hyperframes")
