"""Real HyperFrames renders: a verified reel, identical pixels, and no jump at a continuing cut."""

from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from reelmachine.config import get_settings
from reelmachine.core.job import JobRequest, JobState
from reelmachine.engine import Engine
from reelmachine.render.hyperframes import HyperframesRenderer

from test_explainer3d_engine import SCRIPT

SETTINGS = get_settings()
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(bool(HyperframesRenderer(SETTINGS).missing()), reason="HyperFrames renderer not available"),
]


def _settings(tmp_path):
    return dataclasses.replace(SETTINGS, workdir=tmp_path / "work", outdir=tmp_path / "out", tts="fake")


def _frame(video: Path, index: int) -> np.ndarray:
    out = subprocess.run([SETTINGS.ffmpeg, "-v", "error", "-i", str(video), "-vf", f"select=eq(n\\,{index})",
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(out, dtype=np.uint8).astype(np.int16)


def _build(tmp_path, inputs):
    engine = Engine(_settings(tmp_path))
    try:
        job = engine.submit(JobRequest(recipe="explainer3d", inputs=inputs), caller="local")
        return engine.wait(job.id, caller="local", timeout_s=1800)
    finally:
        engine.close()


def _artifacts(job) -> dict[str, Path]:
    return {a.name: Path(a.path) for a in job.result.artifacts}


def test_a_real_reel_builds_and_verifies(tmp_path):
    job = _build(tmp_path, SCRIPT)
    assert job.state == JobState.SUCCEEDED, job.error
    verification = json.loads(_artifacts(job)["manifest.json"].read_text())["verification"]
    assert verification["ok"], verification


def test_the_same_shot_renders_identical_pixels(tmp_path):
    job = _build(tmp_path, {**SCRIPT, "mode": "storyboard"})
    assert job.state == JobState.SUCCEEDED, job.error
    shot_dir = next((tmp_path / "work").rglob("projects/shot-1"))
    renderer = HyperframesRenderer(_settings(tmp_path))
    a = renderer.render(shot_dir, tmp_path / "a.mp4", seconds=3)
    b = renderer.render(shot_dir, tmp_path / "b.mp4", seconds=3)
    assert np.array_equal(_frame(a, 40), _frame(b, 40))


def test_a_continuing_camera_has_no_jump_at_the_cut(tmp_path):
    job = _build(tmp_path, SCRIPT)  # shot 2 continues shot 1's camera, so the join is a hard cut
    assert job.state == JobState.SUCCEEDED, job.error
    found = _artifacts(job)
    windows = json.loads(found["script.resolved.json"].read_text())["windows"]
    boundary = round(windows[1]["start_ms"] / 1000 * 30)  # first frame of shot 2
    reel = found["reel.mp4"]
    across = np.abs(_frame(reel, boundary - 1) - _frame(reel, boundary)).mean()
    within = np.abs(_frame(reel, boundary - 2) - _frame(reel, boundary - 1)).mean()
    assert across < max(12.0, 4 * within)  # a cut inside a chain looks like any other frame step
