"""explainer3d with ffmpeg: audio/video assembly and full engine runs on the fake renderer."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from reelmachine import ffmpeg
from reelmachine.config import get_settings
from reelmachine.recipes.explainer3d.audio import SFX_NAMES, mix, pad_speech, synth_sfx
from reelmachine.recipes.explainer3d.timing import XFADE_MS
from reelmachine.recipes.explainer3d.video import join_shots

pytestmark = pytest.mark.slow


def _settings(tmp_path):
    return dataclasses.replace(get_settings(), workdir=tmp_path / "work", outdir=tmp_path / "out", tts="fake")


def _clip(settings, path: Path, seconds: float) -> Path:
    ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i",
                f"testsrc2=size=1080x1920:rate=30:duration={seconds:.3f}", "-pix_fmt", "yuv420p", str(path)])
    return path


def test_every_sfx_synthesises(tmp_path):
    settings = _settings(tmp_path)
    for name in SFX_NAMES:
        info = ffmpeg.probe(synth_sfx(name, tmp_path / f"{name}.wav", settings=settings))
        assert info.has_audio and 0.05 < info.duration_s < 1.5


def test_pad_speech_is_exactly_the_window(tmp_path):
    settings = _settings(tmp_path)
    src = synth_sfx("ding", tmp_path / "speech.wav", settings=settings)
    out = pad_speech(src, tmp_path / "padded.wav", lead_ms=250, total_ms=2000, settings=settings)
    assert ffmpeg.probe(out).duration_s == pytest.approx(2.0, abs=0.03)


def test_mix_is_the_reel_length_with_and_without_music(tmp_path):
    settings = _settings(tmp_path)
    narration = pad_speech(synth_sfx("ding", tmp_path / "s.wav", settings=settings), tmp_path / "n.wav",
                           lead_ms=0, total_ms=3000, settings=settings)
    cue = synth_sfx("whoosh", tmp_path / "w.wav", settings=settings)
    for music in (None, synth_sfx("pour", tmp_path / "m.wav", settings=settings)):
        out = mix(narration, [(500, cue)], music, tmp_path / "mix.wav", total_ms=3000, settings=settings, profile="studio")
        assert ffmpeg.probe(out).duration_s == pytest.approx(3.0, abs=0.05)


def test_join_crossfades_and_cuts_to_the_nominal_length(tmp_path):
    settings = _settings(tmp_path)
    nominal = [1500, 1500, 1500]
    joins = ["none", "xfade", "cut"]
    tracks = [
        _clip(settings, tmp_path / "a.mp4", (1500 + XFADE_MS) / 1000),  # followed by a crossfade
        _clip(settings, tmp_path / "b.mp4", 1.5),                         # followed by a cut
        _clip(settings, tmp_path / "c.mp4", 1.5),
    ]
    out = join_shots(tracks, joins, nominal, tmp_path / "joined.mp4", settings=settings)
    info = ffmpeg.probe(out)
    assert info.duration_s == pytest.approx(4.5, abs=0.05) and (info.width, info.height) == (1080, 1920)


import json  # noqa: E402

from reelmachine.core.job import JobRequest, JobState  # noqa: E402
from reelmachine.engine import Engine  # noqa: E402

SCRIPT = {
    "title": "The Secret Bait",
    "set": {"environment": "meadow-day", "stage": "stone-slab"},
    "cast": {
        "bowl": {"asset": {"shape": "bowl", "height": 0.2, "material": "stone"}, "at": "stage.center"},
        "potato": {"asset": {"shape": "ball", "height": 0.1, "colour": "#d9b44a"}},
    },
    "shots": [
        {"narration": "First, a potato goes in.", "label": "Step 1",
         "camera": {"move": "orbit", "target": "bowl", "from": 10, "to": 40},
         "actions": [{"move": "fly_in", "actor": "potato"}, {"move": "drop_into", "actor": "potato", "into": "bowl"}]},
        {"narration": "Then we mash it.", "camera": {"move": "push_in", "target": "bowl", "continue": True},
         "actions": [{"move": "mash", "actor": "bowl", "colour": "#d9d27a"}]},
    ],
}


def _run(tmp_path, inputs):
    engine = Engine(_settings(tmp_path), provider_overrides={"renderer": "explainer3d-fake"})
    try:
        job = engine.submit(JobRequest(recipe="explainer3d", inputs=inputs), caller="local")
        return engine.wait(job.id, caller="local", timeout_s=600)
    finally:
        engine.close()


def test_storyboard_stops_after_prep_with_a_contact_sheet(tmp_path):
    job = _run(tmp_path, {**SCRIPT, "mode": "storyboard"})
    assert job.state == JobState.SUCCEEDED, job.error
    names = {a.name for a in job.result.artifacts}
    assert {"storyboard.jpg", "script.resolved.json"} <= names and "reel.mp4" not in names


def test_build_makes_a_reel_whose_length_is_the_narration(tmp_path):
    job = _run(tmp_path, SCRIPT)
    assert job.state == JobState.SUCCEEDED, job.error
    reel = next(a for a in job.result.artifacts if a.name == "reel.mp4")
    info = ffmpeg.probe(reel.path)
    assert (info.width, info.height) == (1080, 1920) and info.has_audio
    timeline = next(a for a in job.result.artifacts if a.name == "timeline.json")
    assert abs(info.duration_s * 1000 - json.loads(Path(timeline.path).read_text())["duration_ms"]) < 100
    assert "captions.ass" not in {a.name for a in job.result.artifacts}


def test_unchanged_shots_are_not_rendered_twice(tmp_path):
    _run(tmp_path, {**SCRIPT, "mode": "storyboard"})
    _run(tmp_path, SCRIPT)
    second = _run(tmp_path, SCRIPT)
    assert second.state == JobState.SUCCEEDED
    cache = _settings(tmp_path).workdir / "jobs" / "local" / "cache" / "explainer3d" / "shots"  # per caller
    assert len(list(cache.rglob("*.mp4"))) == 2  # two shots, rendered once each


def test_a_bad_script_fails_with_the_field(tmp_path):
    bad = {**SCRIPT, "shots": [{"narration": "x", "actions": [{"move": "teleport", "actor": "bowl"}]}]}
    job = _run(tmp_path, bad)
    assert job.state == JobState.FAILED
    assert job.error.code == "INVALID_INPUT" and job.error.details["path"] == "shots[0].actions[0].move"


from reelmachine.recipes.explainer3d.verify import MOTION_MIN, motion_score  # noqa: E402


def test_motion_score_tells_a_frozen_clip_from_a_moving_one(tmp_path):
    settings = _settings(tmp_path)
    moving = _clip(settings, tmp_path / "m.mp4", 2.0)
    frozen = tmp_path / "f.mp4"
    ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i",
                "color=c=gray:size=1080x1920:rate=30:duration=2", "-pix_fmt", "yuv420p", str(frozen)])
    assert motion_score(moving, 0.0, 2.0, settings=settings) >= MOTION_MIN
    assert motion_score(frozen, 0.0, 2.0, settings=settings) < MOTION_MIN


def test_build_is_verified(tmp_path):
    job = _run(tmp_path, SCRIPT)
    manifest = next(a for a in job.result.artifacts if a.name == "manifest.json")
    verification = json.loads(Path(manifest.path).read_text())["verification"]
    assert verification["ok"], verification
    assert {c["name"] for c in verification["checks"]} >= {"format", "duration", "motion", "licences", "renderer"}


def test_a_build_after_a_storyboard_does_not_pay_for_the_voice_twice(tmp_path, monkeypatch):
    from reelmachine.recipes.doodle.narration.fake import FakeNarration

    calls = []
    original = FakeNarration.synthesize

    def counting(self, text, **kwargs):
        calls.append(text)
        return original(self, text, **kwargs)

    monkeypatch.setattr(FakeNarration, "synthesize", counting)
    _run(tmp_path, {**SCRIPT, "mode": "storyboard"})
    assert len(calls) == 2
    job = _run(tmp_path, SCRIPT)
    assert job.state == JobState.SUCCEEDED, job.error
    assert len(calls) == 2  # the build reused both lines
