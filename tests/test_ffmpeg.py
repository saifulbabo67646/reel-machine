"""The ffmpeg / ffprobe command shapes a probe is built from.

`probe` once handed ffprobe the same `input_args` it builds for ffmpeg — including
`-nostdin`, which newer ffprobe builds parse as an option that swallows the next
token ("Failed to set value '-loglevel' for option 'nostdin'"), so every probe
failed wherever ffprobe was installed. A machine without ffprobe falls back to
parsing `ffmpeg -i` and never noticed.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from reelmachine import ffmpeg
from reelmachine.core.errors import ErrorCode, to_job_error


def test_probe_command_is_ffprobe_safe(monkeypatch) -> None:
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        payload = {
            "format": {"duration": "1.0"},
            "streams": [{"codec_type": "video", "width": 2, "height": 2}],
        }
        return SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=0)

    monkeypatch.setattr(ffmpeg, "have_ffprobe", lambda: True)
    monkeypatch.setattr(ffmpeg, "run", fake_run)
    info = ffmpeg.probe("movie.mkv")
    assert info.has_video and info.duration_s == 1.0

    cmd = captured[0]
    assert cmd[0].endswith("ffprobe")
    assert "-nostdin" not in cmd, "ffprobe rejects -nostdin on new builds"
    # the quiet flags appear exactly once: repeating the globals confused parsers
    assert cmd.count("-hide_banner") == 1 and cmd.count("-loglevel") == 1
    assert "-show_format" in cmd and "-show_streams" in cmd
    assert cmd[-2:] == ["-of", "json"]


def test_input_args_still_disables_stdin_for_ffmpeg() -> None:
    assert "-nostdin" in ffmpeg.input_args("clip.mp4")
    assert "-nostdin" not in ffmpeg.input_args("clip.mp4", nostdin=False)
    quiet = ffmpeg.input_args("clip.mp4", quiet=False)
    assert "-loglevel" not in quiet


def test_has_filter_reads_the_filter_list(monkeypatch) -> None:
    """Homebrew's ffmpeg (9.0) is built without libass — no `ass` filter at all."""
    ffmpeg.has_filter.cache_clear()
    listing = (
        "Filters:\n"
        " .. ass               V->V       Render ASS subtitles onto input video using libass.\n"
        " .. scale             V->V       Scale the input video size and/or convert the image format.\n"
    )
    monkeypatch.setattr(
        ffmpeg, "run", lambda cmd, **kwargs: SimpleNamespace(stdout=listing, stderr="", returncode=0)
    )
    assert ffmpeg.has_filter("ass")
    assert ffmpeg.has_filter("scale")
    assert not ffmpeg.has_filter("subtitles")
    ffmpeg.has_filter.cache_clear()


def test_require_filter_says_what_to_do(monkeypatch) -> None:
    monkeypatch.setattr(ffmpeg, "has_filter", lambda name: False)
    with pytest.raises(ffmpeg.MissingFilter) as failure:
        ffmpeg.require_filter("ass", what="burn the captions")
    message = str(failure.value)
    assert "burn the captions" in message and "ffmpeg-full" in message
    # a filter that is not libass-backed gets the plain message
    monkeypatch.setattr(ffmpeg, "has_filter", lambda name: True)
    ffmpeg.require_filter("ass", what="burn the captions")


def test_a_missing_filter_is_an_actionable_job_error() -> None:
    error = to_job_error(
        ffmpeg.MissingFilter(
            "this ffmpeg cannot burn the captions: the 'ass' filter is missing — install libass"
        )
    )
    assert error.code == ErrorCode.RENDER_FAILED
    assert "ffmpeg-full" in error.hint
