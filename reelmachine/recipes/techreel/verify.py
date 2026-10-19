"""Verify a finished techreel reel by inspecting the file itself: its format, length, sound, voice, stage and renderer."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from ... import ffmpeg
from ...config import Settings
from ...core.manifest import CheckResult, VerificationReport
from ..dialoguereel.verify import SILENCE_DB, check_audio, long_silences, parse_silences
from .compose import HEIGHT, WIDTH, write_project
from .schedule import Plan
from .stages import document, plan_of

DURATION_TOLERANCE_MS = 100
SILENT_MAX_MS = 1000     # the longest the voice may go quiet inside the beats; transition gaps are <= 300 ms
STAGE_TOP, STAGE_BOTTOM = 440, 1380
VISIBLE_MIN = 3.0        # mean abs grey difference, stage band vs the bare background
_W, _H = 270, 480


def _mode(outputs: dict[str, Any]) -> str:
    try:
        return outputs["preview"].compose.narration.script.inputs.mode
    except (KeyError, AttributeError):
        return "storyboard"


def check_silence(audio: Path, plan: Plan, settings: Settings) -> CheckResult:
    """The narration never goes quiet for longer than SILENT_MAX_MS between the first beat and the last; the
    transition gaps are shorter than that and the outro is left out."""
    proc = subprocess.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-i", str(audio), "-af",
                           f"silencedetect=n={SILENCE_DB}dB:d={SILENT_MAX_MS / 1000}", "-f", "null", "-"],
                          check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        lines = (proc.stderr or "").strip().splitlines()
        return CheckResult(name="silence", ok=False,
                           detail=f"ffmpeg failed ({proc.returncode}): {lines[-1] if lines else 'no output'}")
    span = (plan.beats[0].start_ms, plan.beats[-1].end_ms) if plan.beats else (0, 0)
    found = long_silences(parse_silences(proc.stderr or "", plan.duration_ms), [span], SILENT_MAX_MS)
    return CheckResult(name="silence", ok=not found,
                       detail="silent " + ", ".join(f"{s}-{e} ms" for s, e in found) if found
                       else f"no gap over {SILENT_MAX_MS} ms in {span[0]}-{span[1]} ms")


def _grey(source: Path, settings: Settings, at_s: float | None = None) -> np.ndarray | None:
    """A video frame (at `at_s`) or an image, 270x480 grey; None when it cannot be read."""
    seek = [] if at_s is None else ["-ss", f"{at_s:.3f}"]
    proc = subprocess.run(
        [settings.ffmpeg, "-hide_banner", "-nostdin", *seek, "-i", str(source), "-frames:v", "1",
         "-vf", f"scale={_W}:{_H}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=False, capture_output=True)
    pixels = np.frombuffer(proc.stdout or b"", dtype=np.uint8)
    return pixels.reshape(_H, _W).astype(np.float32) if pixels.size == _W * _H else None


def stage_band(frame: np.ndarray) -> np.ndarray:
    scale = _H / HEIGHT
    return frame[round(STAGE_TOP * scale):round(STAGE_BOTTOM * scale)]


def bare_document(doc: dict[str, Any]) -> dict[str, Any]:
    """The same reel with nothing on its stage: no visuals, hero, creator or captions; the look is kept."""
    bare = {**doc, "hero": None, "creator": None, "heroStates": [], "captions": []}
    bare["beats"] = [{**b, "visuals": [], "heading": [], "kicker": "", "keyword": "", "dm": "", "next": ""}
                     for b in doc["beats"]]
    return bare


def check_visible(video: Path, ctx: Any, narration: Any, settings: Settings) -> CheckResult:
    """At each beat's midpoint the stage band differs from the bare background (one extra snapshot)."""
    renderer = ctx.providers.get("renderer")
    if getattr(renderer, "name", "") != "hyperframes":
        return CheckResult(name="visible", ok=True, detail="skipped: only the real renderer draws the stage")
    doc = document(narration)
    mids = [(b["startMs"] + b["endMs"]) // 2 for b in doc["beats"]]
    if not mids:
        return CheckResult(name="visible", ok=True, detail="no beats")
    with tempfile.TemporaryDirectory(prefix="techreel-bare-") as tmp:
        project = write_project(bare_document(doc), Path(tmp) / "project")
        reference = _grey(renderer.snapshot(project, mids[0] / 1000, Path(tmp) / "bare.png"), settings)
    if reference is None:
        return CheckResult(name="visible", ok=False, detail="the background reference could not be read")
    values, missed = [], []
    for index, ms in enumerate(mids):
        frame = _grey(video, settings, ms / 1000)
        diff = None if frame is None else float(np.abs(stage_band(frame) - stage_band(reference)).mean())
        values.append("?" if diff is None else f"{diff:.1f}")
        if diff is None or diff <= VISIBLE_MIN:
            missed.append(f"beat {index} ({values[-1]})")
    return CheckResult(name="visible", ok=not missed,
                       detail="empty stage: " + ", ".join(missed) if missed
                       else f"{len(mids)} beats show something (diff {', '.join(values)}; floor {VISIBLE_MIN})")


def verify_techreel(ctx: Any, outputs: dict[str, Any]) -> VerificationReport:
    render = outputs.get("render")
    if render is None:  # storyboard mode: no reel to verify
        return VerificationReport(ok=True, render_mode="hyperframes", notes=_mode(outputs))
    settings: Settings = ctx.config
    planned = outputs["narration"].duration_ms
    video = Path(render.video)
    info = ffmpeg.probe(video)
    narration = outputs["narration"]
    voice = Path(ctx.assets.get(narration.audio_asset))
    checks = [
        CheckResult(name="format",
                    ok=(info.width, info.height) == (WIDTH, HEIGHT) and info.has_video and info.has_audio,
                    detail=f"{info.width}x{info.height} video={info.has_video} audio={info.has_audio}"),
        CheckResult(name="duration", ok=abs(info.duration_s * 1000 - planned) <= DURATION_TOLERANCE_MS,
                    detail=f"file {info.duration_s * 1000:.0f} ms, schedule {planned} ms"),
        check_audio(video, settings, label="reel"),
        check_silence(voice, plan_of(narration), settings),
        check_visible(video, ctx, narration, settings),
    ]
    named = json.loads(Path(render.manifest).read_text(encoding="utf-8")).get("renderer", "")
    bound = getattr(ctx.providers.get("renderer"), "name", "")
    checks.append(CheckResult(name="renderer", ok=bool(named) and named == bound,
                              detail=f"manifest names {named!r}, bound {bound!r}"))
    return VerificationReport(ok=all(c.ok for c in checks), checks=checks, render_mode="hyperframes")
