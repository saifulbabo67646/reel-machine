"""mapreel's verify step inspects the finished reel; a map that fails to load fails the job."""

import json
import subprocess
from pathlib import Path

import pytest

from reelmachine.config import get_settings
from reelmachine.core.job import JobRequest, JobState
from reelmachine.engine import Engine
from reelmachine.recipes.mapreel import compose
from reelmachine.recipes.mapreel import verify as v
from reelmachine.render.hyperframes import HyperframesRenderer

from test_mapreel_engine import PAID, _run, _seed, _settings

pytestmark = pytest.mark.slow
SETTINGS = get_settings()


def _video(dest: Path, parts: list[tuple[str, float]], audio: str = "sine=frequency=440") -> Path:
    """A 1080x1920 video made of (`moving` or a colour, seconds) parts, with one audio source under it all."""
    inputs, labels = [], []
    for i, (kind, seconds) in enumerate(parts):
        src = "testsrc2=" if kind == "moving" else f"color=c={kind}:"
        inputs += ["-f", "lavfi", "-i", f"{src}size=1080x1920:rate=30:duration={seconds}"]
        labels.append(f"[{i}:v]")
    total = sum(s for _, s in parts)
    inputs += ["-f", "lavfi", "-i", f"{audio}:duration={total}" if audio.startswith("sine") else audio]
    graph = "".join(labels) + f"concat=n={len(parts)}:v=1:a=0[v]"
    subprocess.run([SETTINGS.ffmpeg, "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[v]",
                    "-map", f"{len(parts)}:a", "-t", str(total), "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(dest)], check=True)
    return dest


def test_verify_flags_a_frozen_stretch(tmp_path):
    video = _video(tmp_path / "frozen.mp4", [("moving", 2), ("0x336699", 3), ("moving", 2)])
    check = v.check_frozen(video, 7.0, SETTINGS)
    assert check.name == "frozen" and not check.ok
    assert "2." in check.detail  # the stretch starts about 2 s in
    moving = _video(tmp_path / "moving.mp4", [("moving", 4)])
    assert v.check_frozen(moving, 4.0, SETTINGS).ok


def test_verify_flags_silence(tmp_path):
    silent = _video(tmp_path / "silent.mp4", [("moving", 2)], audio="anullsrc=r=48000:cl=stereo")
    check = v.check_audio(silent, SETTINGS)
    assert check.name == "audio" and not check.ok
    toned = _video(tmp_path / "tone.mp4", [("moving", 2)])
    assert v.check_audio(toned, SETTINGS).ok


def test_verify_names_an_item_that_never_appears(tmp_path):
    still = _video(tmp_path / "still.mp4", [("0x336699", 3)])
    items = [{"name": "Gucci", "atMs": 1000, "x": 540, "y": 900, "size": 300}]
    check = v.check_items(still, items, SETTINGS)
    assert check.name == "items" and not check.ok and "Gucci" in check.detail
    popped = _video(tmp_path / "popped.mp4", [("0x336699", 1), ("white", 2)])
    assert v.check_items(popped, items, SETTINGS).ok


HAS_HYPERFRAMES = not HyperframesRenderer(SETTINGS).missing()
needs_hyperframes = pytest.mark.skipif(not HAS_HYPERFRAMES, reason="HyperFrames renderer not available")


def _real(tmp_path, inputs):
    """A build by the real renderer on the seeded test map: Chrome draws the runtime, so items really pop."""
    settings = _settings(tmp_path)
    _seed(settings)
    engine = Engine(settings)
    try:
        job = engine.submit(JobRequest(recipe="mapreel", inputs=inputs), caller="local")
        return engine.wait(job.id, caller="local", timeout_s=900)
    finally:
        engine.close()


def _report(job):
    return json.loads(Path(job.result.manifest).read_text(encoding="utf-8"))["verification"]


@needs_hyperframes
def test_verify_passes_a_good_reel(tmp_path):
    job = _real(tmp_path, PAID)
    assert job.state == JobState.SUCCEEDED, job.error
    report = _report(job)
    assert {c["name"] for c in report["checks"]} == {"format", "duration", "audio", "frozen", "items", "renderer"}
    assert report["ok"], report["checks"]


def test_verify_runs_on_an_engine_build(tmp_path):
    # the stand-in renderer draws a test pattern, not the runtime: its items never pop, everything else holds
    job = _run(tmp_path, PAID)
    assert job.state == JobState.SUCCEEDED, job.error
    checks = {c["name"]: c for c in _report(job)["checks"]}
    assert all(checks[name]["ok"] for name in ("format", "duration", "audio", "frozen", "renderer")), checks
    assert not checks["items"]["ok"] and "Gucci" in checks["items"]["detail"]


def test_a_storyboard_verifies_as_storyboard(tmp_path):
    job = _run(tmp_path, {**PAID, "mode": "storyboard"})
    assert job.state == JobState.SUCCEEDED, job.error
    report = _report(job)
    assert report["ok"] and report["notes"] == "storyboard"


@needs_hyperframes
def test_a_runtime_map_error_fails_the_render(tmp_path, monkeypatch):
    real = compose.write_project

    def without_world(doc, dest, links):  # the world layer is named in the document but never linked
        return real(doc, dest, {k: p for k, p in links.items() if k != "layers/world.jpg"})

    monkeypatch.setattr(compose, "write_project", without_world)
    job = _real(tmp_path, PAID)
    assert job.state == JobState.FAILED
    assert "mapreel: layer layers/world.jpg failed to load" in job.error.message
