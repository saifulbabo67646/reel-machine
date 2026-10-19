"""techreel's verify step: the voice is never quiet for long and every beat puts something on the stage."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from reelmachine.config import get_settings
from reelmachine.recipes.techreel import compose, verify
from reelmachine.recipes.techreel.schedule import BeatSlot, LineSlot, Plan
from reelmachine.render.hyperframes import HyperframesRenderer

SETTINGS = get_settings()


def _track(path: Path, parts: list[tuple[str, float]]) -> Path:
    """A wav of tones ('tone') and silences ('quiet'), each for the given seconds."""
    args, labels = [], []
    for i, (kind, secs) in enumerate(parts):
        src = f"sine=frequency=300:duration={secs}" if kind == "tone" else f"anullsrc=r=44100:cl=mono:d={secs}"
        args += ["-f", "lavfi", "-i", src]
        labels.append(f"[{i}:a]aresample=44100,aformat=channel_layouts=mono[a{i}];")
    graph = "".join(labels) + "".join(f"[a{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1"
    subprocess.run([SETTINGS.ffmpeg, "-v", "error", "-y", *args, "-filter_complex", graph, str(path)], check=True)
    return path


def _plan(end_ms: int) -> Plan:
    return Plan((BeatSlot(0, end_ms, (LineSlot(0, end_ms, end_ms),)),), end_ms + 1200)


def test_silence_passes_for_short_gaps_and_the_outro(tmp_path):
    audio = _track(tmp_path / "ok.wav", [("tone", 2), ("quiet", 0.3), ("tone", 2), ("quiet", 1.2)])
    result = verify.check_silence(audio, _plan(4300), SETTINGS)
    assert result.name == "silence" and result.ok, result.detail


def test_silence_fails_on_a_long_gap_and_names_the_time(tmp_path):
    audio = _track(tmp_path / "gap.wav", [("tone", 2), ("quiet", 1.5), ("tone", 2), ("quiet", 1.2)])
    result = verify.check_silence(audio, _plan(5500), SETTINGS)
    assert not result.ok and "silent 2" in result.detail


def test_silence_fails_when_ffmpeg_fails(tmp_path):
    result = verify.check_silence(tmp_path / "missing.wav", _plan(1000), SETTINGS)
    assert not result.ok and result.detail


def test_bare_document_empties_the_stage():
    doc = json.loads((compose.RUNTIME_DIR / "tests" / "fixture.json").read_text(encoding="utf-8"))
    bare = verify.bare_document(doc)
    assert all(not b["visuals"] for b in bare["beats"]) and not bare["captions"] and bare["hero"] is None
    assert len(bare["beats"]) == len(doc["beats"]) and bare["look"] == doc["look"]


def test_stage_band_is_the_stage_rows():
    frame = np.arange(480, dtype=np.float32)[:, None] * np.ones((1, 270), dtype=np.float32)
    band = verify.stage_band(frame)
    assert band[0, 0] == round(440 * 480 / 1920) and band.shape[0] == round(1380 * 0.25) - round(440 * 0.25)


@pytest.mark.slow
@pytest.mark.skipif(bool(HyperframesRenderer(SETTINGS).missing()), reason="HyperFrames renderer not available")
def test_visible_under_the_real_renderer(tmp_path, monkeypatch):
    doc = json.loads((compose.RUNTIME_DIR / "tests" / "fixture.json").read_text(encoding="utf-8"))
    doc["beats"], doc["duration"] = doc["beats"][:2], 7.5   # two beats keep the render short
    renderer = HyperframesRenderer(SETTINGS)
    full = renderer.render(compose.write_project(doc, tmp_path / "project"), tmp_path / "full.mp4",
                           fps=30, seconds=7.5)
    bare = renderer.render(compose.write_project(verify.bare_document(doc), tmp_path / "bare"),
                           tmp_path / "bare.mp4", fps=30, seconds=7.5)
    monkeypatch.setattr(verify, "document", lambda _narration: doc)
    ctx = SimpleNamespace(providers={"renderer": renderer})
    shown = verify.check_visible(full, ctx, SimpleNamespace(), SETTINGS)
    empty = verify.check_visible(bare, ctx, SimpleNamespace(), SETTINGS)
    assert shown.ok, shown.detail
    assert not empty.ok and "empty stage" in empty.detail


def test_visible_is_skipped_under_a_fake_renderer(tmp_path):
    ctx = SimpleNamespace(providers={"renderer": SimpleNamespace(name="explainer3d-fake")})
    result = verify.check_visible(tmp_path / "x.mp4", ctx, SimpleNamespace(), SETTINGS)
    assert result.ok and "skipped" in result.detail
