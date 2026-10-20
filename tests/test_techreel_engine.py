"""techreel through the engine: the example checks clean and builds a storyboard sheet; the recipe registers."""

import dataclasses
import json
import os
import shutil
from pathlib import Path

import pytest

from reelmachine import ffmpeg
from reelmachine.config import get_settings
from reelmachine.core.job import JobRequest, JobState
from reelmachine.engine import Engine
from reelmachine.recipes.techreel.lint import check
from reelmachine.recipes.techreel.models import TechReelInputs
from reelmachine.recipes.techreel.recipe import EXAMPLE, TechReelRecipe

pytestmark = pytest.mark.slow


def run(tmp_path, inputs):
    settings = dataclasses.replace(get_settings(), workdir=tmp_path / "work", outdir=tmp_path / "out",
                                   tts="fake", shared_cache=tmp_path / "shared")
    engine = Engine(settings, provider_overrides={"renderer": "explainer3d-fake"})
    try:
        job = engine.submit(JobRequest(recipe="techreel", inputs=inputs), caller="local")
        return engine.wait(job.id, caller="local", timeout_s=600)
    finally:
        engine.close()


def names(job):
    return {a.name for a in job.result.artifacts}


def artifact(job, name):
    return next(Path(a.path) for a in job.result.artifacts if a.name == name)


def test_the_example_is_valid_and_lints_clean():
    inputs = TechReelInputs.model_validate(EXAMPLE)
    assert [f for f in check(inputs, "fake") if f.severity == "error"] == []
    kinds = {v.kind for b in inputs.beats for v in b.visuals}
    assert {"hero", "counter", "stamp", "emoji", "chips", "repo", "terminal", "window"} <= kinds


def test_the_recipe_is_registered():
    from reelmachine.core.registry import Registry

    found = Registry().find_recipe("techreel")
    assert found is not None and TechReelRecipe.spec.id == "techreel"


def test_storyboard_makes_a_sheet(tmp_path):
    job = run(tmp_path, EXAMPLE)
    assert job.state == JobState.SUCCEEDED, job.error
    assert {"storyboard.jpg", "script.resolved.json"} <= names(job) and "reel.mp4" not in names(job)
    sheet = artifact(job, "storyboard.jpg")
    info = ffmpeg.probe(sheet)
    assert info.width > 360 and info.height > 640   # a grid of stills, not one
    keep = os.environ.get("TECHREEL_KEEP_STORYBOARD")
    if keep:
        Path(keep).mkdir(parents=True, exist_ok=True)
        shutil.copy2(sheet, Path(keep) / "storyboard.jpg")
    data = json.loads(artifact(job, "script.resolved.json").read_text(encoding="utf-8"))
    assert len(data["beats"]) == len(EXAMPLE["beats"]) and data["durationMs"] > data["beats"][-1]["endMs"]
    assert data["beats"][0]["startMs"] == 0
    first = data["beats"][0]["visuals"]
    assert first and {"kind", "enterMs", "exitMs"} <= set(first[0])
    assert 30_000 <= data["durationMs"] <= 60_000   # the lint's target for this example


def test_full_makes_a_reel_with_sound_and_captions(tmp_path):
    job = run(tmp_path, EXAMPLE | {"mode": "full"})
    assert job.state == JobState.SUCCEEDED, job.error
    assert {"reel.mp4", "captions.srt", "reel.json"} <= names(job)
    data = json.loads(artifact(job, "script.resolved.json").read_text(encoding="utf-8"))
    info = ffmpeg.probe(artifact(job, "reel.mp4"))
    assert (info.width, info.height) == (1080, 1920) and info.has_audio
    assert abs(info.duration_s * 1000 - data["durationMs"]) < 100
    srt = artifact(job, "captions.srt").read_text(encoding="utf-8")
    first = " ".join(EXAMPLE["beats"][0]["lines"][0]["text"].replace("*", "").split()[:3])
    assert first in srt and "*" not in srt   # plain chunks of `text`
    manifest = json.loads(artifact(job, "reel.json").read_text(encoding="utf-8"))
    assert manifest["recipe"] == "techreel" and manifest["mode"] == "full"
    assert manifest["sources"] == EXAMPLE["sources"] and manifest["voice"] == "fake:default"
    engine_manifest = next(a for a in job.result.artifacts if a.name == "manifest.json")
    verification = json.loads(Path(engine_manifest.path).read_text(encoding="utf-8"))["verification"]
    assert verification["ok"], verification
    assert {c["name"] for c in verification["checks"]} == {"format", "duration", "audio", "silence", "visible", "renderer"}


def test_full_with_auto_music_mixes_a_bed(tmp_path, monkeypatch):
    import wave

    import numpy as np

    from reelmachine.recipes.techreel import stages

    seen = {}
    real = stages.mix

    def spy(narration, cues, music, *args, **kw):
        if music is not None:
            with wave.open(str(music)) as w:
                seen["peak"] = int(np.abs(np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)).max())
        seen["music"] = music
        return real(narration, cues, music, *args, **kw)

    monkeypatch.setattr(stages, "mix", spy)
    job = run(tmp_path, EXAMPLE | {"mode": "full", "music": "auto"})
    assert job.state == JobState.SUCCEEDED, job.error
    assert seen["music"] is not None and seen["peak"] > 100
    assert json.loads(artifact(job, "reel.json").read_text(encoding="utf-8"))["music"] == "auto"
