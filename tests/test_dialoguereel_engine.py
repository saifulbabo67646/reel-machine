"""dialoguereel through the engine: a storyboard sheet, a full reel, a cast sheet, a strict failure."""

import copy
import dataclasses
import json
from pathlib import Path

import pytest

from reelmachine import ffmpeg
from reelmachine.config import get_settings
from reelmachine.core.job import JobRequest, JobState
from reelmachine.engine import Engine
from reelmachine.recipes.dialoguereel.recipe import EXAMPLE

pytestmark = pytest.mark.slow

# the example with key words that its lines contain, a slow line, and a quiz on the line with most keys
EXAMPLE_P2 = copy.deepcopy(EXAMPLE) | {"quiz": True}
EXAMPLE_P2["lines"][1] |= {"slow": True, "keys": [{"text": "てつだう", "reading": "tetsudau", "meaning": "to help"}]}
EXAMPLE_P2["lines"][3] |= {"keys": [{"text": "せんたく", "reading": "sentaku", "meaning": "laundry"}]}
EXAMPLE_P2["lines"][4] |= {"keys": [{"text": "さら", "reading": "sara", "meaning": "dish"},
                                    {"text": "あらう", "reading": "arau", "meaning": "to wash"}]}


def run(tmp_path, inputs):
    settings = dataclasses.replace(get_settings(), workdir=tmp_path / "work", outdir=tmp_path / "out",
                                   tts="fake", shared_cache=tmp_path / "shared")
    engine = Engine(settings, provider_overrides={"renderer": "explainer3d-fake"})
    try:
        job = engine.submit(JobRequest(recipe="dialoguereel", inputs=inputs), caller="local")
        return engine.wait(job.id, caller="local", timeout_s=600)
    finally:
        engine.close()


def names(job):
    return {a.name for a in job.result.artifacts}


def artifact(job, name):
    return next(Path(a.path) for a in job.result.artifacts if a.name == name)


def resolved(job):
    return json.loads(artifact(job, "script.resolved.json").read_text(encoding="utf-8"))


def _error(job):
    return job.error.model_dump() if hasattr(job.error, "model_dump") else job.error


def test_storyboard_makes_a_sheet(tmp_path):
    job = run(tmp_path, EXAMPLE)
    assert job.state == JobState.SUCCEEDED, job.error
    assert {"storyboard.jpg", "script.resolved.json"} <= names(job) and "reel.mp4" not in names(job)
    info = ffmpeg.probe(artifact(job, "storyboard.jpg"))
    assert info.width > 360 and info.height > 640  # a grid of stills, not one
    data = resolved(job)
    assert data["cast"].startswith("cast_") and len(data["lines"]) == len(EXAMPLE["lines"])
    first, second = data["lines"][:2]
    assert first["startMs"] == 1500 and first["startMs"] < first["meaningMs"] < first["endMs"] < second["startMs"]
    assert first["words"] and first["words"][0]["startMs"] >= first["startMs"]  # spans in reel time


def test_full_makes_a_reel_with_sound_and_captions(tmp_path):
    job = run(tmp_path, EXAMPLE | {"mode": "full"})
    assert job.state == JobState.SUCCEEDED, job.error
    reel = artifact(job, "reel.mp4")
    info = ffmpeg.probe(reel)
    assert (info.width, info.height) == (1080, 1920) and info.has_audio
    assert abs(info.duration_s * 1000 - resolved(job)["durationMs"]) < 100
    srt = artifact(job, "captions.srt").read_text(encoding="utf-8")
    assert "Shall I help?" in srt and "てつだいましょうか。" in srt
    manifest = json.loads(artifact(job, "reel.json").read_text(encoding="utf-8"))
    assert manifest["recipe"] == "dialoguereel" and manifest["mode"] == "full"
    assert manifest["cast"] == resolved(job)["cast"] and manifest["credits"]


def test_cast_mode_makes_a_sheet_and_an_inline_cast_is_saved(tmp_path):
    job = run(tmp_path, {"mode": "cast", "title": "", "cast": EXAMPLE["cast"], "lines": []})
    assert job.state == JobState.SUCCEEDED, job.error
    assert "cast.png" in names(job) and "reel.mp4" not in names(job)
    assert list((tmp_path / "work" / "jobs" / "local" / "casts").glob("cast_*.json"))


def test_a_saved_cast_id_builds_a_storyboard(tmp_path):
    first = run(tmp_path, {"mode": "cast", "cast": EXAMPLE["cast"], "lines": []})
    ident = resolved(first)["cast"]
    job = run(tmp_path, EXAMPLE | {"cast": ident})
    assert job.state == JobState.SUCCEEDED, job.error
    assert resolved(job)["cast"] == ident


def test_a_strict_full_build_with_no_title_fails_at_script(tmp_path):
    job = run(tmp_path, EXAMPLE | {"mode": "full", "title": ""})
    assert job.state == JobState.FAILED
    error = _error(job)
    assert error["stage"] == "script" and error["details"]["path"] == "title"
    assert error["details"]["rule"] == "no-title"


def test_full_with_every_section_has_a_matching_voice_track(tmp_path):
    job = run(tmp_path, EXAMPLE_P2 | {"mode": "full", "flow": "listen_first"})
    assert job.state == JobState.SUCCEEDED, job.error
    r = resolved(job)
    sections = r["sections"]
    assert sections["quiz"] and sections["recapMs"] and sections["listen"] and sections["bridgeMs"]
    assert sections["quiz"]["line"] == 4 and len(sections["listen"]) == len(EXAMPLE_P2["lines"])
    assert r["lines"][4]["keys"] == [{"index": 0, "start": 3, "end": 5}, {"index": 1, "start": 6, "end": 8}]
    assert r["lines"][0]["keys"] == []
    info = ffmpeg.probe(artifact(job, "reel.mp4"))
    assert abs(info.duration_s * 1000 - r["durationMs"]) < 100
    voice = next(tmp_path.glob("work/**/narration.wav"))
    assert abs(ffmpeg.probe(voice).duration_s * 1000 - r["durationMs"]) < 30
    srt = artifact(job, "captions.srt").read_text(encoding="utf-8")
    assert srt.count("And wash the dishes.") == 2   # the line, and the quiz answer at the reveal


def test_storyboard_has_the_section_frames(tmp_path):
    job = run(tmp_path, EXAMPLE_P2 | {"flow": "listen_first"})
    assert job.state == JobState.SUCCEEDED, job.error
    stills = list(tmp_path.glob("work/**/stills/*.png"))
    assert len(stills) == 1 + 1 + len(EXAMPLE_P2["lines"]) + 3   # hook, listen pass, lines, recap, quiz, answer


def test_compose_writes_post_md_in_storyboard_not_cast(tmp_path):
    job = run(tmp_path, EXAMPLE)
    assert job.state == JobState.SUCCEEDED, job.error
    assert artifact(job, "post.md").read_text(encoding="utf-8").startswith("Helping Mom at home 🧺")
    cast_job = run(tmp_path / "c", EXAMPLE | {"mode": "cast"})
    assert cast_job.state == JobState.SUCCEEDED, cast_job.error
    assert "post.md" not in names(cast_job)
