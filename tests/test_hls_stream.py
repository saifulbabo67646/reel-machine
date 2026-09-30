"""End-to-end test of the HLS path against a local server.

This exists because the real streaming host cannot be relied on in a test: its
URLs carry expiring signed tokens and its CDN returns 403 without them.  So we
stand up our own HLS origin that behaves the same way in the two respects that
actually matter:

* it **rejects requests without the right Referer/Origin** (proving the headers
  reach both the playlist fetch *and* ffmpeg's segment requests), and
* it serves a **master playlist with several renditions** (proving variant
  selection).

Then the whole pipeline runs against `http://` as if it were the real thing:
resolve -> probe -> align -> cut -> render.
"""

from __future__ import annotations

import functools
import http.server
import re
import socket
import subprocess
import threading
from pathlib import Path

import pytest

from reelmachine import ffmpeg, reel as reelmod
from reelmachine.align import align_episode
from reelmachine.config import get_settings
from reelmachine.sources.base import UnresolvedEpisode
from reelmachine.sources.hls import HlsProvider, pick_variant
from reelmachine.sources.mock import MockProvider

pytestmark = pytest.mark.slow

REFERER = "https://player.example/"
ORIGIN = "https://player.example"
EPISODE_SECONDS = 45


# --------------------------------------------------------------------- fixtures


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _StaticHandler(http.server.SimpleHTTPRequestHandler):
    """Plain static files with quiet logging."""

    def log_message(self, *args: object) -> None:  # keep pytest output readable
        pass


class _GuardedHandler(_StaticHandler):
    """Static files, but only for callers that present Referer and Origin."""

    def _guard(self) -> bool:
        if self.headers.get("Referer") != REFERER or self.headers.get("Origin") != ORIGIN:
            self.send_error(403, "Forbidden")
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self._guard():
            super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802 - http.server API
        if self._guard():
            super().do_HEAD()


class _QuietServer(http.server.ThreadingHTTPServer):
    """Threaded static server that ignores clients hanging up mid-response.

    ffmpeg stops reading as soon as it has what it needs, which makes
    `socketserver` print a BrokenPipeError traceback for every request.
    `handle_error` lives on the *server*, not the handler.
    """

    daemon_threads = True

    def handle_error(self, request: object, client_address: object) -> None:
        import sys

        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def mock_env(tmp_path_factory: pytest.TempPathFactory):
    """A private mock dir, so a stale episode from another run is never reused.

    `MockProvider` builds its episode file once and reuses it; without this the
    module would happily align a 45 s test stream against a 150 s cached file.
    """
    settings = get_settings()
    original = settings.mock_dir
    settings.mock_dir = tmp_path_factory.mktemp("mockmedia")
    try:
        yield settings
    finally:
        settings.mock_dir = original


def make_mock(settings) -> MockProvider:
    mock = MockProvider(settings)
    mock.duration_s = EPISODE_SECONDS
    return mock


@pytest.fixture(scope="module")
def hls_origin(mock_env, tmp_path_factory: pytest.TempPathFactory) -> str:
    """Build a two-rendition HLS stream on disk and serve it over HTTP."""
    settings = mock_env
    root = tmp_path_factory.mktemp("hls") / "site"
    root.mkdir(parents=True)

    asset = make_mock(settings).resolve(None, 1)

    for name, height in (("360", 360), ("720", 720)):
        out = root / name
        out.mkdir()
        ffmpeg.run(
            [
                settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-i", str(asset.url),
                "-vf", f"scale=-2:{height}",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000",
                "-f", "hls", "-hls_time", "4", "-hls_playlist_type", "vod",
                "-hls_segment_filename", str(out / "seg%03d.ts"),
                str(out / "index.m3u8"),
            ]
        )

    (root / "master.m3u8").write_text(
        "#EXTM3U\n#EXT-X-VERSION:3\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\n360/index.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720\n720/index.m3u8\n",
        encoding="utf-8",
    )

    port = _free_port()
    handler = functools.partial(_GuardedHandler, directory=str(root))
    server = _QuietServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch, hls_origin: str, mock_env) -> HlsProvider:
    settings = get_settings()
    monkeypatch.setattr(settings, "hls_template", hls_origin + "/master.m3u8")
    monkeypatch.setattr(settings, "referer", REFERER)
    monkeypatch.setattr(settings, "origin", ORIGIN)
    monkeypatch.setattr(settings, "hls_quality", "1080")
    return HlsProvider(settings)


# ------------------------------------------------------------------------ tests


def test_headers_are_required_by_the_origin(provider: HlsProvider) -> None:
    """Sanity-check the fixture itself: without headers the origin says 403."""
    import httpx

    response = httpx.get(provider.settings.hls_template, timeout=5.0)
    assert response.status_code == 403
    # ...and with them, it serves the playlist.
    ok = httpx.get(
        provider.settings.hls_template,
        timeout=5.0,
        headers={"Referer": REFERER, "Origin": ORIGIN},
    )
    assert ok.status_code == 200
    assert ok.text.startswith("#EXTM3U")


def test_resolve_picks_highest_rendition_at_or_below_quality(provider: HlsProvider) -> None:
    asset = provider.resolve(None, 1)

    assert asset.meta["variants"] == ["360p (800000 bps)", "720p (2800000 bps)"]
    assert asset.meta["chosen"] == "720p (2800000 bps)"
    assert asset.url.endswith("/720/index.m3u8")  # relative URI joined to the origin
    assert asset.headers["Referer"] == REFERER
    assert asset.headers["Origin"] == ORIGIN


def test_probe_reads_metadata_over_http(provider: HlsProvider) -> None:
    asset = provider.resolve(None, 1)
    info = provider.probe(asset)

    assert info.has_video and info.has_audio
    assert info.height == 720
    assert info.duration_s == pytest.approx(EPISODE_SECONDS, abs=2.0)


def test_alignment_works_from_a_remote_hls_source(provider: HlsProvider) -> None:
    """The whole point: Nadeshiko timestamps mapped onto a streamed episode."""
    settings = get_settings()
    mock = make_mock(settings)

    asset = provider.resolve(None, 1)
    provider.probe(asset)
    segments = mock.make_segments("MOCKMEDIA001", 1, count=3)
    clips = mock.reference_clips(segments, client=None, asset=asset)

    timeline = align_episode(
        asset.url,
        segments,
        clip_paths=clips,
        headers=asset.headers,
        max_anchors=3,
        episode_duration_ms=asset.duration_ms,
    )

    assert timeline.ok, timeline.reason
    assert timeline.offset_ms == pytest.approx(mock.expected_offset_ms(), abs=250)


def test_full_pipeline_over_hls_renders_a_synced_reel(
    provider: HlsProvider, tmp_path: Path
) -> None:
    settings = get_settings()
    mock = make_mock(settings)

    asset = provider.resolve(None, 1)
    provider.probe(asset)
    segments = mock.make_segments("MOCKMEDIA001", 1, count=2)
    clips = mock.reference_clips(segments, client=None, asset=asset)
    timeline = align_episode(
        asset.url, segments, clip_paths=clips, headers=asset.headers,
        max_anchors=2, episode_duration_ms=asset.duration_ms,
    )
    assert timeline.ok, timeline.reason

    plan = reelmod.Plan(word="彼女", source="hls")
    for segment in segments:
        plan.items.append(
            reelmod.PlannedSegment(
                segment=segment,
                asset=asset,
                timeline=timeline,
                local_start_ms=timeline.to_local_ms(segment.startTimeMs),
                local_end_ms=timeline.to_local_ms(segment.endTimeMs),
                status="ok",
            )
        )

    result = reelmod.render(plan, settings=settings, outdir=tmp_path, name="hls-reel")

    assert result.video.is_file() and result.video.stat().st_size > 10_000
    info = ffmpeg.probe(result.video)
    # The reel must be as long as the cue timeline claims, or subtitles drift.
    assert info.duration_s == pytest.approx(result.duration_ms / 1000.0, abs=0.2)
    assert info.width == 1080 and info.height == 1920


@pytest.fixture(scope="module")
def disguised_url(mock_env, tmp_path_factory: pytest.TempPathFactory) -> str:
    """An fMP4/CMAF HLS stream whose segments are named `.jpg`.

    This mirrors real streaming CDNs, which disguise fMP4 segments as images so
    hotlink filters let them through.  ffmpeg's HLS demuxer refuses those
    segments outright ("not in allowed_segment_extensions"), so this fixture is
    the regression test for the fix in `ffmpeg.hls_input_args`.
    """
    settings = mock_env
    root = tmp_path_factory.mktemp("disguised") / "site"
    out = root / "ep"
    out.mkdir(parents=True)

    asset = make_mock(settings).resolve(None, 1)
    ffmpeg.run(
        [
            settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-i", str(asset.url),
            "-vf", "scale=-2:360",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000",
            "-f", "hls", "-hls_time", "4", "-hls_playlist_type", "vod",
            "-hls_segment_type", "fmp4",
            "-hls_fmp4_init_filename", "init.mp4",
            "-hls_segment_filename", str(out / "seg%03d.m4s"),
            str(out / "pl.m3u8"),
        ]
    )

    # Rename to .jpg and rewrite the playlist, like the real host does.
    playlist = (out / "pl.m3u8").read_text(encoding="utf-8")
    for path in [*sorted(out.glob("*.m4s")), *sorted(out.glob("init.mp4"))]:
        path.rename(path.with_suffix(".jpg"))
    playlist = re.sub(r"([\w.-]+)\.m4s", r"\1.jpg", playlist)
    playlist = playlist.replace("init.mp4", "init.jpg")
    (out / "pl.m3u8").write_text(playlist, encoding="utf-8")

    port = _free_port()
    # Not the guarded handler: this fixture is about disguised extensions, not
    # about headers, and it must be readable without them.
    handler = functools.partial(_StaticHandler, directory=str(root))
    server = _QuietServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}/ep/pl.m3u8"
    finally:
        server.shutdown()
        server.server_close()


def test_disguised_jpg_segments_are_readable(disguised_url: str) -> None:
    """The .jpg-named fMP4 segments must not be blocked by the HLS demuxer."""
    import httpx

    text = httpx.get(disguised_url, timeout=5.0).text
    assert ".jpg" in text and "#EXT-X-MAP" in text  # the fixture is genuinely disguised
    assert "#EXT-X-STREAM-INF" not in text          # ...and it is a media playlist

    info = ffmpeg.probe(disguised_url)
    assert info.has_video and info.has_audio
    assert info.duration_s == pytest.approx(EPISODE_SECONDS, abs=2.0)


def test_cutting_from_a_disguised_stream_works(disguised_url: str, tmp_path: Path) -> None:
    dest = tmp_path / "cut.mp4"
    ffmpeg.cut_clip(disguised_url, dest, start_s=8.0, duration_s=3.0, height=360)

    assert dest.is_file() and dest.stat().st_size > 5_000
    assert ffmpeg.probe(dest).duration_s == pytest.approx(3.0, abs=0.2)


def test_input_args_widen_extensions_for_hls_only() -> None:
    assert ffmpeg.is_hls("https://h/x/pl.m3u8")
    assert ffmpeg.is_hls("https://h/x/pl.m3u8?token=abc")
    assert not ffmpeg.is_hls("https://h/x/movie.mp4")
    assert not ffmpeg.is_hls("/local/file.mkv")

    args = ffmpeg.hls_input_args("https://h/x/pl.m3u8?token=abc")
    if "extension_picky" in ffmpeg.hls_demuxer_options():
        assert "-extension_picky" in args and "false" in args
    assert ffmpeg.hls_input_args("https://h/x/movie.mp4") == []


def test_extension_picky_setting_restores_strict_defaults(
    monkeypatch: pytest.MonkeyPatch, disguised_url: str
) -> None:
    """Opting back in must reproduce ffmpeg's stock refusal."""
    settings = get_settings()
    monkeypatch.setattr(settings, "hls_extension_picky", True)
    assert ffmpeg.hls_input_args(disguised_url) == []
    if "extension_picky" in ffmpeg.hls_demuxer_options():
        with pytest.raises(ffmpeg.FFmpegError):
            ffmpeg.probe(disguised_url)


# ------------------------------------------------------- token / resolver seams


def test_expired_token_gives_an_actionable_message(monkeypatch: pytest.MonkeyPatch, hls_origin: str) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "hls_template", hls_origin + "/master.m3u8")
    monkeypatch.setattr(settings, "referer", "")
    monkeypatch.setattr(settings, "origin", "")
    provider = HlsProvider(settings)

    with pytest.raises(UnresolvedEpisode) as excinfo:
        provider.resolve(None, 1)
    message = str(excinfo.value)
    assert "403" in message and "REEL_HLS_RESOLVER_CMD" in message


def test_token_command_is_substituted_into_the_template(
    monkeypatch: pytest.MonkeyPatch, hls_origin: str
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "referer", REFERER)
    monkeypatch.setattr(settings, "origin", ORIGIN)
    monkeypatch.setattr(
        settings, "hls_template", hls_origin + "/master.m3u8?token={token}&ep={ep3}"
    )
    # A chatty resolver: only the first non-empty line is the token.
    monkeypatch.setattr(settings, "hls_token_cmd", "printf 'noise\\n\\nFRESH.TOKEN\\n'")
    provider = HlsProvider(settings)

    asset = provider.resolve(None, 7)
    assert asset.meta["url"] == hls_origin + "/master.m3u8?token=FRESH.TOKEN&ep=007"


def test_resolver_command_supplies_the_whole_url(
    monkeypatch: pytest.MonkeyPatch, hls_origin: str
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "referer", REFERER)
    monkeypatch.setattr(settings, "origin", ORIGIN)
    monkeypatch.setattr(settings, "hls_template", "https://unused.invalid/{ep}")
    monkeypatch.setattr(
        settings,
        "hls_resolver_cmd",
        f"printf '%s/ep{EPISODE_SECONDS}/../master.m3u8\\n'".replace("%s", hls_origin),
    )
    provider = HlsProvider(settings)

    asset = provider.resolve(None, 1)
    assert asset.url.endswith("/720/index.m3u8")


def test_site_map_resolves_opaque_hashes(
    monkeypatch: pytest.MonkeyPatch, hls_origin: str, tmp_path: Path
) -> None:
    import json

    mapping = tmp_path / "map.json"
    mapping.write_text(
        json.dumps(
            {
                "V1StGXR8_Z5d": {
                    "animeHash": "abb207957b0abc1d85a7e32ab1c4359c",
                    "episodes": {"3": "dfa8b25e59b272cefaa749f2f61c73e4"},
                }
            }
        ),
        encoding="utf-8",
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "referer", REFERER)
    monkeypatch.setattr(settings, "origin", ORIGIN)
    monkeypatch.setattr(settings, "hls_map", mapping)
    monkeypatch.setattr(
        settings,
        "hls_template",
        hls_origin + "/master.m3u8?anime={animeHash}&ep={episodeHash}",
    )
    provider = HlsProvider(settings)

    from reelmachine.models import Media

    media = Media(publicId="V1StGXR8_Z5d", slug="x", nameEn="X")
    asset = provider.resolve(media, 3)
    assert asset.meta["url"].endswith(
        "?anime=abb207957b0abc1d85a7e32ab1c4359c&ep=dfa8b25e59b272cefaa749f2f61c73e4"
    )

    # An episode with no recorded hash must fail loudly, not silently 404.
    monkeypatch.setattr(settings, "hls_map", mapping)
    with pytest.raises(UnresolvedEpisode):
        provider.resolve(media, 99)
