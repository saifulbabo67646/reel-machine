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
