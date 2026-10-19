"""The example tech reel as a real reel: HyperFrames renders it, the fake voice speaks it."""

import dataclasses
import json
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

from reelmachine import ffmpeg
from reelmachine.config import get_settings
from reelmachine.core.job import JobRequest, JobState
from reelmachine.engine import Engine
from reelmachine.recipes.techreel.recipe import EXAMPLE
from reelmachine.render.hyperframes import HyperframesRenderer

SETTINGS = get_settings()
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(bool(HyperframesRenderer(SETTINGS).missing()), reason="HyperFrames renderer not available"),
]

STAGE = slice(440, 1380)
CAPTIONS = slice(1420, 1640)
_SRT_TIME = re.compile(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)")


def _frame_at(video: Path, ms: int) -> np.ndarray:
    out = subprocess.run([SETTINGS.ffmpeg, "-v", "error", "-ss", f"{ms / 1000:.3f}", "-i", str(video),
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.uint8).reshape(1920, 1080, 3).astype(np.int16)


def _srt_cues(path: Path) -> list[tuple[int, int]]:
    def ms(h: str, m: str, s: str, f: str) -> int:
        return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(f)

    return [(ms(*g[:4]), ms(*g[4:])) for g in _SRT_TIME.findall(path.read_text(encoding="utf-8"))]


def test_the_example_renders_as_a_real_reel(tmp_path):
    settings = dataclasses.replace(SETTINGS, workdir=tmp_path / "work", outdir=tmp_path / "out", tts="fake")
    engine = Engine(settings)
    try:
        job = engine.submit(JobRequest(recipe="techreel", inputs=EXAMPLE | {"mode": "full"}), caller="local")
        job = engine.wait(job.id, caller="local", timeout_s=1800)
    finally:
        engine.close()
    assert job.state == JobState.SUCCEEDED, job.error
    arts = {a.name: Path(a.path) for a in job.result.artifacts}
    assert job.result.render_mode == "hyperframes"
    reel, data = arts["reel.mp4"], json.loads(arts["script.resolved.json"].read_text(encoding="utf-8"))
    info = ffmpeg.probe(reel)
    assert (info.width, info.height) == (1080, 1920) and info.has_audio
    assert abs(info.duration_s * 1000 - data["durationMs"]) < 100

    # the counter counts up and lands in the stage band
    beat = next(b for b in data["beats"] if any(v["kind"] == "counter" for v in b["visuals"]))
    counter = next(v for v in beat["visuals"] if v["kind"] == "counter")
    start, landed = _frame_at(reel, beat["startMs"]), _frame_at(reel, counter["enterMs"] + 1000)
    assert np.abs(landed[STAGE] - start[STAGE]).mean() > 4

    # a line's caption is drawn: bright text in the caption band, not only the dark background
    first, end = _srt_cues(arts["captions.srt"])[0]
    caption = _frame_at(reel, (first + end) // 2)[CAPTIONS]
    assert (caption.mean(axis=2) > 200).any()

    report = json.loads(Path(job.result.manifest).read_text(encoding="utf-8"))["verification"]
    assert report["ok"], report["checks"]
