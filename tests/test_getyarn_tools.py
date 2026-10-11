"""The getyarn tools' own logic: file naming, and where a manifest's file actually lives.

The mixer bug these cover was real: a manifest that carries a *path* had the clips
directory joined onto it a second time, and a directory of perfectly good clips came back
as "no clips found".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from reelmachine import impersonate
from reelmachine.config import get_settings

ROOT = Path(get_settings().workdir.parent)
for _path in (ROOT, ROOT / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import getyarn_reel as reel  # noqa: E402
import getyarn_search as gs  # noqa: E402


# ------------------------------------------------------------------ the mixer


def test_clip_path_accepts_a_name_a_path_and_an_absolute_one(tmp_path, monkeypatch) -> None:
    clips_dir = tmp_path / "clips"
    clips_dir.mkdir()
    inside = clips_dir / "abc.mp4"
    inside.write_bytes(b"x")

    assert reel._clip_path({"file": "abc.mp4"}, clips_dir, "abc") == inside
    assert reel._clip_path({}, clips_dir, "abc") == inside, "the id is the filename"
    assert reel._clip_path({"file": str(inside)}, clips_dir, "abc") == inside

    monkeypatch.chdir(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "abc.mp4").write_bytes(b"x")
    assert reel._clip_path({"file": "elsewhere/abc.mp4"}, clips_dir, "abc") == Path("elsewhere/abc.mp4")


def test_a_missing_file_still_resolves_to_a_name_for_the_warning(tmp_path) -> None:
    clips_dir = tmp_path / "clips"
    assert reel._clip_path({"file": "abc.mp4"}, clips_dir, "abc") == clips_dir / "abc.mp4"


# ------------------------------------------------------------------ the search tool


def test_download_clips_names_the_file_the_way_the_mixer_expects(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        impersonate,
        "fetch",
        lambda url, **kwargs: impersonate.Fetched(url=url, status=200, content_type="video/mp4",
                                                  content=b"y" * 64),
    )
    clips = [gs.Clip(id="11111111-1111-1111-1111-111111111111", title="A Show",
                     line="piece of cake", seconds=1.0)]

    saved = gs.download_clips(clips, tmp_path, verbose=False)

    assert saved[clips[0].id] == tmp_path / "11111111-1111-1111-1111-111111111111.mp4"
    assert saved[clips[0].id].read_bytes() == b"y" * 64

    written = gs.manifest("piece of cake", clips, files=saved)
    assert written["clips"][0]["file"] == "11111111-1111-1111-1111-111111111111.mp4", (
        "a bare name, not a path: --clips-dir owns the directory"
    )


def test_download_clips_keeps_going_when_one_clip_is_refused(monkeypatch, tmp_path) -> None:
    from reelmachine.core.errors import ProviderUnavailable

    def flaky(url, **kwargs):
        if url.endswith("22222222-2222-2222-2222-222222222222.mp4"):
            raise ProviderUnavailable("refused")
        return impersonate.Fetched(url=url, status=200, content_type="video/mp4", content=b"y" * 8)

    monkeypatch.setattr(impersonate, "fetch", flaky)
    clips = [
        gs.Clip(id="11111111-1111-1111-1111-111111111111"),
        gs.Clip(id="22222222-2222-2222-2222-222222222222"),
        gs.Clip(id="33333333-3333-3333-3333-333333333333"),
    ]

    saved = gs.download_clips(clips, tmp_path, verbose=False)

    assert set(saved) == {clips[0].id, clips[2].id}, "one refusal does not lose the rest"


def test_download_clips_honours_the_limit(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        impersonate,
        "fetch",
        lambda url, **kwargs: impersonate.Fetched(url=url, status=200, content_type="video/mp4",
                                                  content=b"z" * 8),
    )
    clips = [gs.Clip(id=f"1111111{i}-1111-1111-1111-111111111111") for i in range(5)]
    saved = gs.download_clips(clips, tmp_path, limit=2, verbose=False)
    assert len(saved) == 2
