# tests/test_mapreel_render.py
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

SETTINGS = get_settings()
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(bool(HyperframesRenderer(SETTINGS).missing()), reason="HyperFrames renderer not available"),
]
SCRIPT = {
    "segments": [
        {"kind": "highlight", "countries": ["USA", "ITA"], "legend": "Two giants",
         "narration": "Only two countries matter here."},
        {"kind": "country", "country": "ITA", "narration": "Italy has Gucci and Prada.",
         "items": [{"name": "Gucci"}, {"name": "Prada"}]},
    ],
}


def _build(tmp_path, inputs):
    settings = dataclasses.replace(SETTINGS, workdir=tmp_path / "work", outdir=tmp_path / "out", tts="fake")
    engine = Engine(settings)  # real map data in the shared cache: downloaded once on first run
    try:
        job = engine.submit(JobRequest(recipe="mapreel", inputs=inputs), caller="local")
        return engine.wait(job.id, caller="local", timeout_s=1800)
    finally:
        engine.close()


def _frame(video: Path, index: int) -> np.ndarray:
    out = subprocess.run([SETTINGS.ffmpeg, "-v", "error", "-i", str(video), "-vf", f"select=eq(n\\,{index})",
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(out, dtype=np.uint8).reshape(1920, 1080, 3).astype(np.int16)


def test_a_real_reel_moves_and_fills(tmp_path):
    job = _build(tmp_path, SCRIPT)
    assert job.state == JobState.SUCCEEDED, job.error
    reel = next(Path(a.path) for a in job.result.artifacts if a.name == "reel.mp4")
    first, later = _frame(reel, 0), _frame(reel, 45)
    assert np.abs(first - later).mean() > 3  # the camera and fills moved
    assert later.std() > 20                  # a map, not a blank canvas
