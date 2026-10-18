"""The example dialogue as a real reel: HyperFrames renders it, the fake voice speaks it."""

import dataclasses
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from reelmachine import ffmpeg
from reelmachine.config import get_settings
from reelmachine.core.job import JobRequest, JobState
from reelmachine.engine import Engine
from reelmachine.recipes.dialoguereel.recipe import EXAMPLE
from reelmachine.render.hyperframes import HyperframesRenderer

SETTINGS = get_settings()
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(bool(HyperframesRenderer(SETTINGS).missing()), reason="HyperFrames renderer not available"),
]


def _frame_at(video: Path, ms: int) -> np.ndarray:
    out = subprocess.run([SETTINGS.ffmpeg, "-v", "error", "-ss", f"{ms / 1000:.3f}", "-i", str(video),
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.uint8).reshape(1920, 1080, 3).astype(np.int16)


def test_the_example_renders_as_a_real_reel(tmp_path):
    settings = dataclasses.replace(SETTINGS, workdir=tmp_path / "work", outdir=tmp_path / "out", tts="fake")
    engine = Engine(settings)
    try:
        job = engine.submit(JobRequest(recipe="dialoguereel", inputs=EXAMPLE | {"mode": "full"}), caller="local")
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
    first, second = (_frame_at(reel, line["meaningMs"] + 300) for line in data["lines"][:2])
    assert first.std() > 20  # a drawn scene, not a blank canvas
    assert np.abs(first[:960] - second[:960]).mean() > 1  # the text in the top half changed
    report = json.loads(Path(job.result.manifest).read_text(encoding="utf-8"))["verification"]
    assert {c["name"] for c in report["checks"]} == {"format", "duration", "audio", "silence", "text", "renderer"}
    assert report["ok"], report["checks"]


def test_render_with_an_image_character(tmp_path):
    settings = dataclasses.replace(SETTINGS, workdir=tmp_path / "work", outdir=tmp_path / "out", tts="fake")
    chars = EXAMPLE["cast"]["characters"]
    cast = {"characters": [chars[0], {**chars[1], "source": {"kind": "image", "provider": "fake",
                                                              "description": "a round dumpling"}}]}
    engine = Engine(settings)
    try:
        job = engine.submit(JobRequest(recipe="dialoguereel", inputs=EXAMPLE | {"cast": cast}), caller="local")
        job = engine.wait(job.id, caller="local", timeout_s=1800)
    finally:
        engine.close()
    assert job.state == JobState.SUCCEEDED, job.error
    arts = {a.name: Path(a.path) for a in job.result.artifacts}
    assert arts["storyboard.jpg"].is_file() and arts["storyboard.jpg"].stat().st_size > 0
