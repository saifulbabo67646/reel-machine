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


TILE_W, TILE_H, PAD = 360, 640, 8
FOOD = Path(__file__).resolve().parents[1] / "examples" / "mapreel" / "food.json"


def _tiles(sheet: Path, count: int) -> list[np.ndarray]:
    from PIL import Image

    grid = np.asarray(Image.open(sheet).convert("RGB")).astype(np.int16)
    cols = min(4, count)
    return [grid[(i // cols) * (TILE_H + PAD):(i // cols) * (TILE_H + PAD) + TILE_H,
                 (i % cols) * (TILE_W + PAD):(i % cols) * (TILE_W + PAD) + TILE_W] for i in range(count)]


def test_every_segment_kind_renders_a_real_still(tmp_path):
    inputs = json.loads(FOOD.read_text(encoding="utf-8"))
    inputs["mode"] = "storyboard"
    kinds = [s["kind"] for s in inputs["segments"]]
    assert {"highlight", "rank", "versus", "country", "regions"} <= set(kinds)
    job = _build(tmp_path, inputs)
    assert job.state == JobState.SUCCEEDED, job.error
    sheet = next(Path(a.path) for a in job.result.artifacts if a.name == "storyboard.jpg")
    assert sheet.is_file()
    tiles = _tiles(sheet, len(kinds))
    for index, (kind, tile) in enumerate(zip(kinds, tiles)):
        assert tile.std() > 10, f"still {index + 1} ({kind}) is blank"
    versus = tiles[kinds.index("versus")]
    top, bottom = versus[:TILE_H // 2], versus[TILE_H // 2:]
    assert np.abs(top - bottom).mean() > 10  # two maps, not one picture twice
