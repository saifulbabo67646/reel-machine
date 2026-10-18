"""Verify a finished dialoguereel reel by inspecting the file itself: its format, length, voice, text and renderer."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from ... import ffmpeg
from ...config import Settings
from ...core.manifest import CheckResult, VerificationReport
from ..mapreel.verify import check_audio
from .compose import HEIGHT
from .schedule import Schedule
from .stages import plan_of

SILENT_MAX_MS = 1500     # the longest the dialogue may go without a voice
SILENCE_DB = -50         # silencedetect's noise floor
TEXT_MIN = 1.5           # mean horizontal grey difference in the bubble band once a line's meaning shows
TEXT_TOP, TEXT_BOTTOM = 372, 1000   # the bubble band, in reel px
TEXT_AFTER_MS = 300
_W, _H = 270, 480
_SILENCE = re.compile(r"silence_(start|end):\s*(-?[\d.]+)")


def dialogue_spans(plan: Schedule) -> list[tuple[int, int]]:
    """The spans that must not go quiet: the listen pass, if any, and the lines to the last one's end, slow
    replay included. The bridge between the two is a planned pause, so it is left out."""
    end = max(w.slow_start_ms + w.slow_ms if w.slow_start_ms is not None else w.end_ms for w in plan.lines)
    listen = [(plan.listen[0].start_ms, plan.listen[-1].end_ms)] if plan.listen else []
    return [*listen, (plan.lines[0].start_ms, end)]


def long_silences(silences: list[tuple[int, int]], spans: list[tuple[int, int]],
                  max_ms: int = SILENT_MAX_MS) -> list[tuple[int, int]]:
    """Each silence clipped to each span, keeping those longer than `max_ms`."""
    clipped = [(max(s, a), min(e, b)) for a, b in spans for s, e in silences]
    return [(s, e) for s, e in clipped if e - s > max_ms]


def parse_silences(stderr: str, total_ms: int) -> list[tuple[int, int]]:
    """ffmpeg silencedetect's (start, end) pairs in ms; a start with no end runs to `total_ms`."""
    out, start = [], None
    for kind, value in _SILENCE.findall(stderr):
        ms = max(0, round(float(value) * 1000))
        if kind == "start":
            start = ms
        elif start is not None:
            out.append((start, ms))
            start = None
    if start is not None:
        out.append((start, total_ms))
    return out


def check_silence(audio: Path, plan: Schedule, settings: Settings) -> CheckResult:
    """The voice track never goes quiet for longer than SILENT_MAX_MS inside the dialogue."""
    proc = subprocess.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-i", str(audio), "-af",
                           f"silencedetect=n={SILENCE_DB}dB:d={SILENT_MAX_MS / 1000}", "-f", "null", "-"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        lines = (proc.stderr or "").strip().splitlines()
        return CheckResult(name="silence", ok=False,
                           detail=f"ffmpeg failed ({proc.returncode}): {lines[-1] if lines else 'no output'}")
    spans = dialogue_spans(plan)
    found = long_silences(parse_silences(proc.stderr or "", plan.duration_ms), spans)
    where = ", ".join(f"{a}-{b} ms" for a, b in spans)
    return CheckResult(name="silence", ok=not found,
                       detail="silent " + ", ".join(f"{s}-{e} ms" for s, e in found) if found
                       else f"no gap over {SILENT_MAX_MS} ms in {where}")


def _grey(video: Path, at_s: float, settings: Settings) -> np.ndarray | None:
    """One frame at 270x480, grey; None when it cannot be read."""
    proc = subprocess.run(
        [settings.ffmpeg, "-hide_banner", "-nostdin", "-ss", f"{at_s:.3f}", "-i", str(video),
         "-frames:v", "1", "-vf", f"scale={_W}:{_H}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    pixels = np.frombuffer(proc.stdout or b"", dtype=np.uint8)
    if pixels.size != _W * _H:
        return None
    return pixels.reshape(_H, _W).astype(np.float32)


def text_energy(band: np.ndarray) -> float:
    """The mean absolute horizontal grey difference: high where glyphs are drawn, near zero on a flat fill."""
    return float(np.abs(np.diff(band, axis=1)).mean())


def text_band(frame: np.ndarray) -> np.ndarray:
    scale = _H / HEIGHT
    return frame[round(TEXT_TOP * scale):round(TEXT_BOTTOM * scale)]


def check_text(video: Path, plan: Schedule, settings: Settings) -> CheckResult:
    """Every line's bubble shows text once its meaning is up."""
    values, missed = [], []
    for index, w in enumerate(plan.lines):
        frame = _grey(video, (w.meaning_ms + TEXT_AFTER_MS) / 1000, settings)
        energy = None if frame is None else text_energy(text_band(frame))
        values.append("?" if energy is None else f"{energy:.1f}")
        if energy is None or energy < TEXT_MIN:
            missed.append(f"line {index} ({values[-1]})")
    return CheckResult(name="text", ok=not missed,
                       detail="no text: " + ", ".join(missed) if missed
                       else f"{len(plan.lines)} lines drawn (energy {', '.join(values)}; floor {TEXT_MIN})")


def _mode(outputs: dict[str, Any]) -> str:
    try:
        return outputs["preview"].compose.narration.script.inputs.mode
    except (KeyError, AttributeError):
        return "storyboard"


def verify_dialoguereel(ctx: Any, outputs: dict[str, Any]) -> VerificationReport:
    render = outputs.get("render")
    if render is None:  # cast or storyboard mode: no reel to verify
        return VerificationReport(ok=True, render_mode="hyperframes", notes=_mode(outputs))
    settings: Settings = ctx.config
    narration = outputs["narration"]
    plan = plan_of(narration)
    video = Path(render.video)
    info = ffmpeg.probe(video)
    voice = Path(ctx.assets.get(narration.audio_asset))
    checks = [
        CheckResult(name="format", ok=(info.width, info.height) == (1080, 1920) and info.has_video and info.has_audio,
                    detail=f"{info.width}x{info.height} video={info.has_video} audio={info.has_audio}"),
        CheckResult(name="duration", ok=abs(info.duration_s * 1000 - plan.duration_ms) <= 100,
                    detail=f"file {info.duration_s * 1000:.0f} ms, schedule {plan.duration_ms} ms"),
        check_audio(video, settings, label="reel"),
        check_silence(voice, plan, settings),
        check_text(video, plan, settings),
    ]
    named = json.loads(Path(render.manifest).read_text(encoding="utf-8")).get("renderer", "")
    bound = getattr(ctx.providers.get("renderer"), "name", "")
    checks.append(CheckResult(name="renderer", ok=bool(named) and named == bound,
                              detail=f"manifest names {named!r}, bound {bound!r}"))
    return VerificationReport(ok=all(c.ok for c in checks), checks=checks, render_mode="hyperframes")
