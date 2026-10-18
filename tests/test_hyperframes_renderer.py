"""The HyperFrames renderer, driven through a fake `npx` so the logic is tested without Node."""

from __future__ import annotations

import dataclasses
import os
import pathlib
import stat

import pytest

from reelmachine.config import get_settings
from reelmachine.core.errors import ShotRenderFailed
from reelmachine.core.stage import CancellationToken
from reelmachine.render.hyperframes import HyperframesRenderer

FAKE_NPX = """#!/bin/sh
# argv: --yes hyperframes@V <command> ...
echo "$@" >> "$HF_LOG"
cmd="$3"; shift 3
case "$cmd" in
  lint) if [ -n "$HF_LINT_FAIL" ]; then echo "x missing_timeline_registry"; echo "1 error(s), 0 warning(s)"; exit 1; fi; echo "0 errors, 0 warnings";;
  render) if [ -n "$HF_PAGE_ERROR" ]; then echo '[INFO] [Render] Failure summary {"failedStage":"capture","error":"'"$HF_PAGE_ERROR"'"}'; printf '✗  Render failed\n\n   %s\n' "$HF_PAGE_ERROR"; exit 1; fi
    while [ "$1" != "--output" ]; do shift; done; [ -n "$HF_SLEEP" ] && sleep "$HF_SLEEP"; printf 'mp4' > "$2";;
  snapshot) if [ -n "$HF_PAGE_ERROR" ]; then printf '✗ Snapshot failed: %s\n' "$HF_PAGE_ERROR"; exit 1; fi
    while [ "$1" != "--output" ]; do shift; done; mkdir -p "$2"; printf 'png' > "$2/frame-00-at-1s.png";;
esac
"""


@pytest.fixture
def fake_npx(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npx = bin_dir / "npx"
    npx.write_text(FAKE_NPX)
    npx.chmod(npx.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "npx.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("HF_LOG", str(log))
    return log


def _renderer(tmp_path):
    settings = dataclasses.replace(get_settings(), workdir=tmp_path / "work")
    return HyperframesRenderer(settings)


def test_render_lints_then_renders_pinned(tmp_path, fake_npx):
    project = tmp_path / "shot"
    project.mkdir()
    out = _renderer(tmp_path).render(project, tmp_path / "out.mp4")
    assert out.read_text() == "mp4"
    calls = fake_npx.read_text().splitlines()
    assert calls[0].startswith("--yes hyperframes@0.8.146 lint")
    assert "render" in calls[1] and "--fps 30" in calls[1]
    assert (project / "render.log").is_file()


def test_relative_paths_are_resolved_before_hyperframes_runs_in_the_project(
    tmp_path, fake_npx, monkeypatch
):
    # a relative REEL_WORKDIR (`.work`) gives relative paths; HyperFrames runs with the
    # project as its cwd, so they must not be handed to it as-is
    (tmp_path / "shot").mkdir()
    monkeypatch.chdir(tmp_path)
    renderer = _renderer(tmp_path)
    out = renderer.render(pathlib.Path("shot"), pathlib.Path("renders/out.mp4"))
    assert (tmp_path / "renders" / "out.mp4").read_text() == "mp4" and out.is_absolute()
    still = renderer.snapshot(pathlib.Path("shot"), 1.0, pathlib.Path("stills/still.png"))
    assert (tmp_path / "stills" / "still.png").read_text() == "png" and still.is_absolute()


def test_lint_failure_is_a_shot_render_failure(tmp_path, fake_npx, monkeypatch):
    monkeypatch.setenv("HF_LINT_FAIL", "1")
    project = tmp_path / "shot"
    project.mkdir()
    with pytest.raises(ShotRenderFailed) as info:
        _renderer(tmp_path).render(project, tmp_path / "out.mp4")
    assert "missing_timeline_registry" in info.value.details["lint"]


def test_snapshot_returns_the_frame(tmp_path, fake_npx):
    project = tmp_path / "shot"
    project.mkdir()
    still = _renderer(tmp_path).snapshot(project, 1.0, tmp_path / "still.png")
    assert still.read_text() == "png"
    assert "--at 1.000 --no-end" in fake_npx.read_text()


def test_timeout_kills_the_process_and_unregisters_it(tmp_path, fake_npx, monkeypatch):
    monkeypatch.setenv("HF_SLEEP", "30")
    project = tmp_path / "shot"
    project.mkdir()
    token = CancellationToken()
    renderer = _renderer(tmp_path)
    renderer.timeout_floor_s = 1
    with pytest.raises(ShotRenderFailed) as info:
        renderer.render(project, tmp_path / "out.mp4", seconds=0.01, cancel=token)
    assert "timed out" in info.value.message
    assert not token._processes


def test_missing_reports_instead_of_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # nothing on PATH
    settings = dataclasses.replace(get_settings(), ffmpeg="ffmpeg", ffprobe="ffprobe")  # ignore REEL_FFPROBE
    problems = HyperframesRenderer(settings).missing()
    assert any("node" in p for p in problems) and any("ffprobe" in p for p in problems)


def test_workers_can_be_pinned_for_a_low_disk_machine(tmp_path, fake_npx, monkeypatch):
    project = tmp_path / "shot"
    project.mkdir()
    monkeypatch.setenv("REEL_HYPERFRAMES_WORKERS", "1")
    _renderer(tmp_path).render(project, tmp_path / "out.mp4")
    assert "--workers 1" in fake_npx.read_text().splitlines()[1]
    monkeypatch.delenv("REEL_HYPERFRAMES_WORKERS")
    _renderer(tmp_path).render(project, tmp_path / "out2.mp4")
    assert "--workers" not in fake_npx.read_text().splitlines()[3]


def test_a_page_error_is_named_in_the_render_failure(tmp_path, fake_npx, monkeypatch):
    monkeypatch.setenv("HF_PAGE_ERROR", "mapreel: layer layers/world.jpg failed to load")
    project = tmp_path / "shot"
    project.mkdir()
    with pytest.raises(ShotRenderFailed) as info:
        _renderer(tmp_path).render(project, tmp_path / "out.mp4")
    assert "mapreel: layer layers/world.jpg failed to load" in info.value.message
    with pytest.raises(ShotRenderFailed) as info:
        _renderer(tmp_path).snapshot(project, 1.0, tmp_path / "still.png")
    assert "mapreel: layer layers/world.jpg failed to load" in info.value.message
