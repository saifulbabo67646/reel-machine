"""The impersonating downloader: the one step of the getyarn flow that needs no browser.

Offline throughout — `curl_cffi.requests.get` is the seam, and it is monkeypatched rather
than mocked away, because the block-page detection inside `fetch` is exactly what the
measurement that shaped this module was about.
"""

from __future__ import annotations

import importlib.util

import pytest

from reelmachine import impersonate
from reelmachine.core.errors import ProviderUnavailable


class _Response:
    """The little of `curl_cffi`'s response that `fetch` reads."""

    def __init__(self, status: int = 200, content: bytes = b"\x00\x00\x00\x18ftypmp42",
                 ctype: str = "video/mp4") -> None:
        self.status_code = status
        self.content = content
        self.headers = {"content-type": ctype}


def test_the_missing_dependency_is_reported_not_raised(monkeypatch) -> None:
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *args, **kwargs: None
        if name == "curl_cffi"
        else real_find_spec(name, *args, **kwargs),
    )
    problems = impersonate.requirement()
    assert problems and "reel-machine[impersonate]" in problems[0]
    with pytest.raises(ProviderUnavailable):
        impersonate.fetch("https://example.com/clip.mp4")


def test_fetch_returns_the_bytes_and_sends_the_referer(monkeypatch) -> None:
    pytest.importorskip("curl_cffi")
    seen: dict = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs, url=url)
        return _Response()

    monkeypatch.setattr("curl_cffi.requests.get", fake_get)
    fetched = impersonate.fetch("https://y.getyarn.io/abc.mp4", referer="https://getyarn.io/")

    assert fetched.content.startswith(b"\x00\x00\x00\x18")
    assert fetched.is_media
    assert seen["impersonate"] == "chrome", "the fingerprint is the whole point"
    assert seen["headers"] == {"referer": "https://getyarn.io/"}


def test_a_block_page_is_refused_even_with_a_200(monkeypatch) -> None:
    """A host that answers HTML has not answered with the file, whatever its status code."""
    pytest.importorskip("curl_cffi")
    monkeypatch.setattr(
        "curl_cffi.requests.get",
        lambda url, **kwargs: _Response(200, b"<!DOCTYPE html><title>Just a moment...</title>",
                                        "text/html; charset=UTF-8"),
    )
    with pytest.raises(ProviderUnavailable) as excinfo:
        impersonate.fetch("https://y.getyarn.io/abc.mp4")
    assert "text/html" in str(excinfo.value)
    assert excinfo.value.details["status"] == 200


def test_the_impersonation_target_can_be_overridden(monkeypatch) -> None:
    pytest.importorskip("curl_cffi")
    seen: dict = {}
    monkeypatch.setattr("curl_cffi.requests.get",
                        lambda url, **kwargs: seen.update(kwargs) or _Response())
    impersonate.fetch("https://example.com/x.mp4", impersonate="firefox")
    assert seen["impersonate"] == "firefox"


def test_download_writes_once_and_skips_next_time(monkeypatch, tmp_path) -> None:
    calls: list[str] = []

    def fake_fetch(url, **kwargs):
        calls.append(url)
        return impersonate.Fetched(url=url, status=200, content_type="video/mp4",
                                   content=b"x" * 32)

    monkeypatch.setattr(impersonate, "fetch", fake_fetch)
    dest = tmp_path / "abc.mp4"

    assert impersonate.download("https://y.getyarn.io/abc.mp4", dest).read_bytes() == b"x" * 32
    assert len(calls) == 1
    impersonate.download("https://y.getyarn.io/abc.mp4", dest)
    assert len(calls) == 1, "a completed download is not fetched again"
    impersonate.download("https://y.getyarn.io/abc.mp4", dest, refresh=True)
    assert len(calls) == 2
    assert not dest.with_suffix(".mp4.part").exists(), "the .part file is renamed, not left behind"
