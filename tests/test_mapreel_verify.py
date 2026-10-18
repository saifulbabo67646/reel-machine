"""mapreel's verify step inspects the finished reel; a map that fails to load fails the job."""

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from reelmachine.config import get_settings
from reelmachine.core.job import JobRequest, JobState
from reelmachine.engine import Engine
from reelmachine.recipes.mapreel import compose
from reelmachine.recipes.mapreel import verify as v
from reelmachine.render.hyperframes import HyperframesRenderer

from test_mapreel_engine import PAID, _run, _seed, _settings

slow = pytest.mark.slow
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


@slow
def test_verify_flags_a_frozen_stretch(tmp_path):
    video = _video(tmp_path / "frozen.mp4", [("moving", 2), ("0x336699", 3), ("moving", 2)])
    check = v.check_frozen(video, 7.0, SETTINGS)
    assert check.name == "frozen" and not check.ok
    assert "from 2." in check.detail  # the stretch starts about 2 s in
    moving = _video(tmp_path / "moving.mp4", [("moving", 4)])
    assert v.check_frozen(moving, 4.0, SETTINGS).ok


@slow
def test_verify_flags_silence(tmp_path):
    silent = _video(tmp_path / "silent.mp4", [("moving", 2)], audio="anullsrc=r=48000:cl=stereo")
    check = v.check_audio(silent, SETTINGS)
    assert check.name == "audio" and not check.ok
    toned = _video(tmp_path / "tone.mp4", [("moving", 2)])
    assert v.check_audio(toned, SETTINGS).ok


@slow
def test_verify_names_an_item_that_never_appears(tmp_path):
    still = _video(tmp_path / "still.mp4", [("0x336699", 3)])
    items = [{"name": "Gucci", "atMs": 1000, "x": 540, "y": 900, "size": 300, "clipStartMs": 0, "clipEndMs": 3000}]
    check = v.check_items(still, items, SETTINGS)
    assert check.name == "items" and not check.ok and "Gucci" in check.detail
    popped = _video(tmp_path / "popped.mp4", [("0x336699", 1), ("white", 2)])
    assert v.check_items(popped, items, SETTINGS).ok


def _moving_bands(count: int) -> list[np.ndarray]:
    rng = np.random.default_rng(7)
    return [rng.uniform(0, 255, (8, 8)).astype(np.float32) for _ in range(count)]


@pytest.mark.parametrize("offset", [0, 1, 2, 3, 5, 8, 13, 21])
def test_a_still_exactly_at_the_limit_passes_wherever_it_starts(offset):
    limit = round(v.FROZEN_MAX_S / v.SAMPLE_S)
    for extra, ok in ((0, True), (1, False)):
        bands = _moving_bands(40)
        still = bands[offset]
        bands[offset:offset + limit + extra + 1] = [still] * (limit + extra + 1)  # `limit + extra` steps still
        steps, start = v.longest_still(bands)
        assert (steps, start) == (limit + extra, offset)
        check = v.frozen_check(steps, start)
        assert check.ok is ok, (offset, extra, check.detail)
        assert f"{(limit + extra) * v.SAMPLE_S:.1f} s" in check.detail


def test_an_unreadable_sample_is_skipped_not_black():
    bands = _moving_bands(10)
    bands[4] = None
    assert v.longest_still(bands)[0] == 0
    black = np.zeros((8, 8), np.float32)
    bands = [black, None, black, black]
    assert v.longest_still(bands) == (3, 0)


def test_an_item_box_off_screen_fails_instead_of_passing_on_nan():
    frame = np.full((v._H, v._W), 100, np.float32)
    item = {"name": "Ghost", "x": 5000, "y": 900, "size": 300}
    assert v.pop_change(frame, frame + 50, item) is None


def test_reel_items_carry_reel_time_and_their_clip_bounds(tmp_path):
    from types import SimpleNamespace as NS

    from reelmachine.core.timeline import Clip

    doc = compose.segment_document(
        duration_ms=3000, camera={"from": {"x": 0.5, "y": 0.5, "s": 1}, "to": {"x": 0.5, "y": 0.5, "s": 1},
                                  "flyMs": 0, "drift": 0}, layers=[], places=[], previous=[], words=[],
        items=[{"name": "Gucci", "image": None, "emoji": None, "atMs": 2800, "x": 500, "y": 800, "size": 240}])
    project = compose.write_project(doc, tmp_path / "b", {})
    clips = [Clip(element_id="a", asset="a.mp4", start_ms=0, duration_ms=4000, origin="scene"),
             Clip(element_id="b", asset="b.mp4", start_ms=4000, duration_ms=3000, origin="scene")]
    comp = NS(timeline=NS(video=clips), prep=NS(segments=NS(projects=[NS(segment_id="b", dir=str(project))])))
    (item,) = v.reel_items(comp)
    assert (item["atMs"], item["clipStartMs"], item["clipEndMs"]) == (6800, 4000, 7000)
    assert (item["x"], item["y"], item["size"]) == (500, 800, 240)


def _pop(name, at, start, end):
    return {"name": name, "atMs": at, "x": 540, "y": 900, "size": 300, "clipStartMs": start, "clipEndMs": end}


@slow
def test_a_pop_just_before_a_cut_is_read_inside_its_own_segment(tmp_path):
    # the item is due at 1.8 s in a segment that cuts at 2.0 s: the next segment's picture must not count as its pop
    cut = _video(tmp_path / "cut.mp4", [("0x336699", 2), ("red", 1)])
    check = v.check_items(cut, [_pop("Gucci", 1800, 0, 2000)], SETTINGS)
    assert not check.ok and "Gucci" in check.detail
    popped = _video(tmp_path / "popped.mp4", [("0x336699", 1.8), ("white", 0.2), ("red", 1)])
    assert v.check_items(popped, [_pop("Gucci", 1800, 0, 2000)], SETTINGS).ok


@slow
def test_a_pop_at_the_end_of_the_reel_is_read_inside_the_reel(tmp_path):
    still = _video(tmp_path / "still.mp4", [("0x336699", 3)])
    check = v.check_items(still, [_pop("Prada", 2800, 1000, 3000)], SETTINGS)
    assert not check.ok and "Prada" in check.detail
    popped = _video(tmp_path / "popped.mp4", [("0x336699", 2.8), ("white", 0.2)])
    assert v.check_items(popped, [_pop("Prada", 2800, 1000, 3000)], SETTINGS).ok


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


@slow
@needs_hyperframes
def test_verify_passes_a_good_reel(tmp_path):
    job = _real(tmp_path, PAID)
    assert job.state == JobState.SUCCEEDED, job.error
    report = _report(job)
    assert {c["name"] for c in report["checks"]} == {"format", "duration", "audio", "frozen", "items", "renderer"}
    assert report["ok"], report["checks"]


@slow
def test_verify_runs_on_an_engine_build(tmp_path):
    # the stand-in renderer draws a test pattern, not the runtime: its items never pop, everything else holds
    job = _run(tmp_path, PAID)
    assert job.state == JobState.SUCCEEDED, job.error
    checks = {c["name"]: c for c in _report(job)["checks"]}
    assert all(checks[name]["ok"] for name in ("format", "duration", "audio", "frozen", "renderer")), checks
    assert checks["audio"]["detail"].startswith("narration")
    assert not checks["items"]["ok"] and "Gucci" in checks["items"]["detail"]


@slow
def test_a_storyboard_verifies_as_storyboard(tmp_path):
    job = _run(tmp_path, {**PAID, "mode": "storyboard"})
    assert job.state == JobState.SUCCEEDED, job.error
    report = _report(job)
    assert report["ok"] and report["notes"] == "storyboard"


@slow
@needs_hyperframes
def test_a_runtime_map_error_fails_the_render(tmp_path, monkeypatch):
    real = compose.write_project

    def without_world(doc, dest, links):  # the world layer is named in the document but never linked
        return real(doc, dest, {k: p for k, p in links.items() if k != "layers/world.jpg"})

    monkeypatch.setattr(compose, "write_project", without_world)
    job = _real(tmp_path, PAID)
    assert job.state == JobState.FAILED
    assert "mapreel: layer layers/world.jpg failed to load" in job.error.message
