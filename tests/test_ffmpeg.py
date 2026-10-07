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

from reelmachine import ffmpeg


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
