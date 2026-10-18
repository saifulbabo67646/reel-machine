"""Verify a finished mapreel reel by inspecting the file itself: its format, length, sound, motion and pops."""

from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from ... import ffmpeg
from ...config import Settings
from ...core.manifest import CheckResult, VerificationReport
from ..explainer3d.verify import _H, _W, MOTION_MIN, _grey
from . import lint
from .compose import FPS
from .geometry import HEIGHT, WIDTH

FROZEN_MAX_S = lint.BEAT_GAP_S * 2  # the longest the picture may sit still
SAMPLE_S = 0.4                      # the frozen check samples the picture this often
ITEM_MIN = 6.0                      # mean grey change inside an item's box across its pop
ITEM_BEFORE_MS, ITEM_AFTER_MS = 200, 400
AUDIO_MIN_DB = -45.0                # the narration's mean volume (ffmpeg volumedetect)
SILENT_DB = -90.0                   # volumedetect reports -91 dB for digital silence
_TOP, _BOTTOM = int(_H * 0.12), int(_H * 0.70)  # between the title and caption bands
_VOLUME = re.compile(r"(mean|max)_volume:\s*(-?[\d.]+|-inf) dB")
_SEGMENT_DATA = re.compile(r'<script id="segment-data" type="application/json">(.*?)</script>', re.S)


def check_audio(media: Path, settings: Settings, *, label: str = "audio") -> CheckResult:
    """`media` is heard: its mean volume is above AUDIO_MIN_DB and its loudest sample is not silence."""
    proc = subprocess.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-i", str(media), "-map", "0:a:0",
                           "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    found = {k: float("-inf") if v == "-inf" else float(v) for k, v in _VOLUME.findall(proc.stderr or "")}
    if "mean" not in found:
        return CheckResult(name="audio", ok=False, detail=f"{label}: no audio to measure")
    mean, peak = found["mean"], found.get("max", float("-inf"))
    return CheckResult(name="audio", ok=mean > AUDIO_MIN_DB and peak > SILENT_DB,
                       detail=f"{label}: mean {mean:.1f} dB (floor {AUDIO_MIN_DB:.0f}), max {peak:.1f} dB")


def longest_still(bands: list[np.ndarray | None]) -> tuple[int, int]:
    """The longest run, in sample steps, in which the picture stays within MOTION_MIN of where the run began
    (so a slow drift counts as motion once it adds up), and the index it starts at. A None (unreadable)
    sample is skipped: it neither extends nor breaks a run, but its step still counts."""
    longest, at = 0, 0
    start, anchor = 0, None
    for index, band in enumerate(bands):
        if band is None:
            continue
        if anchor is not None and float(np.abs(band - anchor).mean()) < MOTION_MIN:
            if index - start > longest:
                longest, at = index - start, start
            continue
        start, anchor = index, band
    return longest, at


def frozen_check(steps: int, start: int) -> CheckResult:
    """Decided in whole samples, so a stretch exactly at the limit never fails on float rounding."""
    limit = round(FROZEN_MAX_S / SAMPLE_S)
    ok = steps <= limit
    still, at = steps * SAMPLE_S, start * SAMPLE_S
    detail = (f"still for {still:.1f} s from {at:.1f} s (limit {limit * SAMPLE_S:.1f} s)" if not ok
              else f"longest still {still:.1f} s (limit {limit * SAMPLE_S:.1f} s)")
    return CheckResult(name="frozen", ok=ok, detail=detail)


def check_frozen(video: Path, duration_s: float, settings: Settings) -> CheckResult:
    """No stretch longer than FROZEN_MAX_S in which the picture between the bands stands still."""
    count = int(max(0.0, duration_s - 0.05) / SAMPLE_S) + 1
    bands = []
    for i in range(count):
        frame = _grey(video, i * SAMPLE_S, settings, strict=True)
        bands.append(None if frame is None else frame[_TOP:_BOTTOM])
    return frozen_check(*longest_still(bands))


def _box(item: dict[str, Any]) -> tuple[slice, slice]:
    """An item's anchor box (centre x/y, a `size` square, in reel pixels) on the sampled grey frame."""
    sx, sy = _W / WIDTH, _H / HEIGHT
    half = item["size"] / 2
    x0, x1 = max(0, int((item["x"] - half) * sx)), min(_W, int(np.ceil((item["x"] + half) * sx)))
    y0, y1 = max(0, int((item["y"] - half) * sy)), min(_H, int(np.ceil((item["y"] + half) * sy)))
    return slice(y0, y1), slice(x0, x1)


def pop_change(before: np.ndarray, after: np.ndarray, item: dict[str, Any]) -> float | None:
    """The mean grey change inside the item's box, or None when the box holds no pixel (off screen)."""
    rows, cols = _box(item)
    region = before[rows, cols]
    if region.size == 0:
        return None
    return float(np.abs(after[rows, cols] - region).mean())


def check_items(video: Path, items: list[dict[str, Any]], settings: Settings) -> CheckResult:
    """Every item visibly pops: its box changes by more than ITEM_MIN grey levels across its pop. `items` carry
    reel-time `atMs`, their anchor `x`, `y` and `size`, and their segment's `clipStartMs`/`clipEndMs`: both
    samples stay inside that segment, so a cut or the reel's end is never mistaken for the pop."""
    if not items:
        return CheckResult(name="items", ok=True, detail="no items")
    frame_ms = 1000 / FPS
    missed = []
    for item in items:
        before_ms = max(item["atMs"] - ITEM_BEFORE_MS, item["clipStartMs"])
        # floored to the ms: a seek lands on the first frame at or after it, so rounding up could cross the cut
        after_ms = min(item["atMs"] + ITEM_AFTER_MS, math.floor(item["clipEndMs"] - frame_ms))
        before = _grey(video, before_ms / 1000, settings, strict=True)
        after = _grey(video, after_ms / 1000, settings, strict=True)
        change = None if before is None or after is None else pop_change(before, after, item)
        if change is None:
            missed.append(f"{item['name']} (unreadable)")
        elif change <= ITEM_MIN:
            missed.append(f"{item['name']} ({change:.1f})")
    return CheckResult(name="items", ok=not missed,
                       detail="no pop: " + ", ".join(missed) if missed else f"{len(items)} items pop")


def segment_document(project_dir: str | Path) -> dict[str, Any]:
    html = (Path(project_dir) / "index.html").read_text(encoding="utf-8")
    return json.loads(_SEGMENT_DATA.search(html).group(1))


def reel_items(compose: Any) -> list[dict[str, Any]]:
    """Every item with a pop time, its segment-local `atMs` moved to reel time by the timeline, with the reel-time
    bounds of its segment's clip."""
    clips = {clip.element_id: clip for clip in compose.timeline.video}
    out = []
    for project in compose.prep.segments.projects:
        clip = clips[project.segment_id]
        for item in segment_document(project.dir).get("items", []):
            if item.get("atMs") is not None:
                out.append({**item, "atMs": clip.start_ms + int(item["atMs"]), "clipStartMs": clip.start_ms,
                            "clipEndMs": clip.start_ms + clip.duration_ms})
    return out


def verify_mapreel(ctx: Any, outputs: dict[str, Any]) -> VerificationReport:
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
        check_audio(ctx.assets.get(compose.prep.segments.narration.audio_asset), settings, label="narration"),
        check_frozen(video, info.duration_s, settings),
        check_items(video, reel_items(compose), settings),
    ]
    manifest = json.loads(Path(render.manifest).read_text(encoding="utf-8"))
    named = {compose.prep.renderer, timeline.meta.get("renderer", ""), manifest.get("renderer", "")}
    checks.append(CheckResult(name="renderer", ok=len(named) == 1 and "" not in named,
                              detail=f"segments by {compose.prep.renderer!r}, manifest names "
                                     f"{manifest.get('renderer', '')!r}"))
    return VerificationReport(ok=all(c.ok for c in checks), checks=checks, render_mode="hyperframes")
