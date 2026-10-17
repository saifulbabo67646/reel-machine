"""mapreel through the engine: a storyboard sheet, a reel with captions, a bad place, a continuous camera."""

import dataclasses
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from reelmachine.config import get_settings
from reelmachine.core.job import JobRequest, JobState
from reelmachine.engine import Engine
from reelmachine.recipes.mapreel import geodata

pytestmark = pytest.mark.slow
FIXTURE = Path(__file__).parent / "fixtures" / "mapreel" / "countries.geojson"

SCRIPT = {
    "title": "Brands",
    "segments": [
        {"kind": "highlight", "countries": ["USA", "ITA", "FRA"], "legend": "Fashion superpowers",
         "narration": "Only *three countries* dress the world. Number one will surprise you."},
        {"kind": "country", "country": "Italy", "narration": "Italy has Gucci, Prada and Versace.",
         "items": [{"name": "Gucci"}, {"name": "Prada", "image": "LOGO"}, {"name": "Versace", "image": "/nope.png"}]},
    ],
}


def _settings(tmp_path):
    return dataclasses.replace(get_settings(), workdir=tmp_path / "work", outdir=tmp_path / "out",
                               tts="fake", shared_cache=tmp_path / "shared")


def _seed(settings):
    root = geodata.geo_cache(settings)
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FIXTURE, root / geodata.FILES["countries"][0])
    for key, size in (("world", "720x360"), ("detail", "1440x720")):
        subprocess.run([settings.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}",
                        "-frames:v", "1", str(root / geodata.FILES[key][0])], check=True)


def _logo(tmp_path, settings):
    path = tmp_path / "prada.png"
    subprocess.run([settings.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:size=200x80",
                    "-frames:v", "1", str(path)], check=True)
    return str(path)


def _run(tmp_path, inputs):
    settings = _settings(tmp_path)
    _seed(settings)
    script = json.loads(json.dumps(inputs).replace('"LOGO"', json.dumps(_logo(tmp_path, settings))))
    engine = Engine(settings, provider_overrides={"renderer": "explainer3d-fake"})
    try:
        job = engine.submit(JobRequest(recipe="mapreel", inputs=script), caller="local")
        return engine.wait(job.id, caller="local", timeout_s=600)
    finally:
        engine.close()


def _artifacts(job):
    return {a.name: Path(a.path) for a in job.result.artifacts}


def test_a_storyboard_stops_with_a_sheet_and_the_resolved_script(tmp_path):
    job = _run(tmp_path, {**SCRIPT, "mode": "storyboard"})
    assert job.state == JobState.SUCCEEDED, job.error
    arts = _artifacts(job)
    assert {"storyboard.jpg", "script.resolved.json"} <= set(arts) and "reel.mp4" not in arts
    resolved = json.loads(arts["script.resolved.json"].read_text())
    seg = resolved["segments"][1]
    assert seg["codes"] == ["ITA"] and len(seg["items"]) == 3
    assert seg["items"][0]["atMs"] < seg["items"][1]["atMs"] < seg["items"][2]["atMs"]
    assert seg["items"][1]["image"] and seg["items"][2]["image"] is None  # the missing file became a badge
    assert any("Versace" in w for w in resolved["warnings"])


def test_a_build_makes_a_reel_with_captions(tmp_path):
    job = _run(tmp_path, SCRIPT)
    assert job.state == JobState.SUCCEEDED, job.error
    arts = _artifacts(job)
    from reelmachine import ffmpeg

    info = ffmpeg.probe(arts["reel.mp4"])
    assert (info.width, info.height) == (1080, 1920) and info.has_audio and info.duration_s > 3
    assert "Gucci" in arts["captions.srt"].read_text()


def test_an_unknown_country_fails_with_a_suggestion(tmp_path):
    bad = {"segments": [{"kind": "country", "country": "Itally", "narration": "x", "items": [{"name": "a"}]}]}
    job = _run(tmp_path, bad)
    assert job.state == JobState.FAILED and "Italy" in json.dumps(job.error.model_dump() if hasattr(job.error, "model_dump") else str(job.error))


def test_the_camera_continues_across_a_cut(tmp_path):
    job = _run(tmp_path, {**SCRIPT, "mode": "storyboard"})
    resolved = json.loads(_artifacts(job)["script.resolved.json"].read_text())
    a, b = resolved["segments"]
    assert b["camera"]["from"] == a["camera"]["to"]


def test_a_pop_past_the_segment_end_is_clamped_and_warned(tmp_path):
    script = json.loads(json.dumps(SCRIPT))
    script["segments"][1]["items"][2]["at"] = 60
    job = _run(tmp_path, {**script, "mode": "storyboard"})
    assert job.state == JobState.SUCCEEDED, job.error
    resolved = json.loads(_artifacts(job)["script.resolved.json"].read_text())
    seg = resolved["segments"][1]
    assert seg["items"][2]["atMs"] == seg["window"]["duration_ms"] - 300
    assert any("Versace" in w and "after the segment ends" in w for w in resolved["warnings"])
