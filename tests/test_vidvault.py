"""TMDB bridging, vidvault stream selection and segmented downloading.

The parsing/selection tests are pure.  The download tests stand up a local
byte-range origin, because the real CDN's behaviour is the whole reason the
downloader is shaped the way it is: it throttles each connection and cuts long
transfers off part-way, so the tests reproduce both.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import random
import re
import threading
import time
from pathlib import Path

import pytest

from reelmachine.sources.vidvault import Stream, pick_stream
from reelmachine.tmdb import TmdbMatch, normalise, split_season

NOW = 1_800_000_000.0


def _url(age_s: float = 0.0) -> str:
    return f"https://cdn.example/res/x.mp4?sign=abc&t={int(NOW - age_s)}"


def _stream(res: int, *, age_s: float = 0.0, vip: bool = False, size: int = 10_000_000) -> Stream:
    return Stream(
        resolution=res,
        format="MP4",
        size_bytes=size,
        duration_s=1453,
        url="" if vip else _url(age_s),
        vip_locked=vip,
    )


# ---------------------------------------------------------------- season parsing


@pytest.mark.parametrize(
    "title,base,season",
    [
        ("Rent-a-Girlfriend Season 2", "Rent-a-Girlfriend", 2),
        ("Rent-a-Girlfriend Season 4", "Rent-a-Girlfriend", 4),
        ("SPY x FAMILY Season 3", "SPY x FAMILY", 3),
        ("Grand Blue Dreaming Season 2", "Grand Blue Dreaming", 2),
        ("Mushoku Tensei: Jobless Reincarnation Season 2", "Mushoku Tensei: Jobless Reincarnation", 2),
        ("Violet Evergarden", "Violet Evergarden", 1),
        ("Oshi No Ko", "Oshi No Ko", 1),
        # nothing survives stripping, so the original is kept for searching
        ("2nd Season", "2nd Season", 2),
        ("Some Show Part 2", "Some Show", 2),
        ("Some Show Cour 2", "Some Show", 2),
        ("Blood Blockade Battlefront & Beyond", "Blood Blockade Battlefront", 2),
        ("Blood Blockade Battlefront and Beyond", "Blood Blockade Battlefront", 2),
    ],
)
def test_split_season(title: str, base: str, season: int) -> None:
    got_base, got_season = split_season(title)
    assert got_season == season
    assert got_base == base


def test_split_season_never_returns_empty_base() -> None:
    # A title that is *only* a season marker must still yield something searchable.
    assert split_season("Season 2")[0]


def test_normalise_strips_punctuation_and_brackets() -> None:
    assert normalise("【OSHI NO KO】") == "oshi no ko"
    assert normalise("Kimi ni Todoke: From Me to You") == "kimi ni todoke from me to you"
    assert normalise("DARLING in the FRANXX") == "darling in the franxx"


# ------------------------------------------------------------- stream selection


def test_pick_stream_walks_the_ladder_downwards() -> None:
    """1080 -> 720 -> 480 -> 360, exactly as the user asked."""
    streams = [_stream(360), _stream(480), _stream(720), _stream(1080, vip=True)]
    assert pick_stream(streams, quality="1080,720,480,360", now=NOW).resolution == 720

    # 720 gone -> 480
    assert pick_stream([_stream(360), _stream(480)], quality="1080,720,480,360", now=NOW).resolution == 480
    # only 360 -> 360
    assert pick_stream([_stream(360)], quality="1080,720,480,360", now=NOW).resolution == 360
    # 1080 free -> 1080
    assert pick_stream([_stream(360), _stream(1080)], quality="1080,720,480,360", now=NOW).resolution == 1080


def test_pick_stream_skips_vip_locked() -> None:
    assert pick_stream([_stream(360), _stream(720, vip=True)], quality="1080,720,480,360", now=NOW).resolution == 360


def test_pick_stream_falls_back_to_lowest_when_quality_is_below_everything() -> None:
    streams = [_stream(720), _stream(480)]
    assert pick_stream(streams, quality="360", now=NOW).resolution == 480


def test_pick_stream_returns_none_when_nothing_usable() -> None:
    assert pick_stream([_stream(720, vip=True), _stream(1080, vip=True)], quality="720", now=NOW) is None
    assert pick_stream([], quality="720", now=NOW) is None


def test_stream_age_parsing() -> None:
    assert _stream(720, age_s=120).age_s(now=NOW) == pytest.approx(120, abs=1)
    assert Stream(1, "MP4", 0, 0, "https://cdn/x.mp4").age_s(now=NOW) is None


# ------------------------------------------------------------- payload parsing


VIDVAULT_PAYLOAD = {
    "mp4Data": {
        "success": True,
        "downloadInfo": {
            "code": 0,
            "data": {
                "downloads": [
                    {"format": "MP4", "resolution": 360, "size": "32728862", "duration": 1453,
                     "url": "https://cdn/360.mp4?sign=a&t=100", "vipLocked": False},
                    {"format": "MP4", "resolution": 1080, "size": "232048543", "duration": 1453,
                     "url": "", "vipLocked": True},
                ],
                "hasResource": True,
            },
        },
    },
    "mkvData": None,
    "cached": True,
}


def test_parse_streams_handles_the_real_payload_shape() -> None:
    from reelmachine.sources.vidvault import VidVaultProvider

    streams = VidVaultProvider.parse_streams(VIDVAULT_PAYLOAD)
    assert [s.resolution for s in streams] == [360, 1080]
    assert streams[0].size_bytes == 32_728_862
    assert streams[0].duration_s == 1453
    assert streams[1].vip_locked is True


def test_parse_streams_tolerates_empty_and_odd_payloads() -> None:
    from reelmachine.sources.vidvault import VidVaultProvider

    assert VidVaultProvider.parse_streams({}) == []
    assert VidVaultProvider.parse_streams({"mp4Data": None, "mkvData": None}) == []
    odd = {"mp4Data": {"downloadInfo": {"data": {"downloads": [None, "junk", {"resolution": 720}]}}}}
    got = VidVaultProvider.parse_streams(odd)
    assert len(got) == 1 and got[0].resolution == 720


# The shape the API actually served when this provider silently broke: the
# legacy `downloadInfo.data.downloads` tree is gone, replaced by `links` with
# string sizes and `quality` labels.  Recorded verbatim from a live response.
LIVE_PAYLOAD = {
    "mp4Data": {
        "links": [
            {"url": "https://tdm.example/dl/aaa", "size": "289 MB", "quality": "720p"},
            {"url": "https://tdm.example/dl/bbb", "size": "76 MB", "quality": "1080p"},
            {"url": "https://tdm.example/dl/ccc", "size": "74 MB", "quality": "480p"},
        ]
    },
    "mkvData": None,
    "mkvV2Data": {
        "quality": "720p", "language": "Japanese", "country": "Japan",
        "size": "112.33 MB", "url": "https://mk2.example/d/deadbeef",
    },
    "cached": True,
}


def test_parse_streams_reads_the_live_links_shape() -> None:
    """The regression that made every episode resolve to zero streams."""
    from reelmachine.sources.vidvault import VidVaultProvider

    streams = VidVaultProvider.parse_streams(LIVE_PAYLOAD)
    assert [s.resolution for s in streams] == [720, 1080, 480, 720]
    assert all(s.url for s in streams)
    assert [s.group for s in streams] == ["MP4", "MP4", "MP4", "MKV"]

    by_url = {s.url: s for s in streams}
    assert by_url["https://tdm.example/dl/aaa"].size_bytes == 289 * 1024**2
    assert by_url["https://tdm.example/dl/ccc"].size_bytes == 74 * 1024**2
    assert by_url["https://mk2.example/d/deadbeef"].size_bytes == int(112.33 * 1024**2)
    # no vipLocked flag in this generation, and 1080p is genuinely offered
    assert not any(s.vip_locked for s in streams)


def test_parse_streams_reads_every_mkv_generation() -> None:
    from reelmachine.sources.vidvault import VidVaultProvider

    payload = {
        "mp4Data": None,
        "mkvData": {"url": "https://a.example/1", "size": "1.5 GB", "quality": "1080p"},
        "mkvV2Data": [{"url": "https://b.example/2", "size": 1024, "quality": "720p"}],
        "mkvV3Data": {
            "country": "Japan", "language": "Japanese",
            "downloads": [
                {"qualities": [{"quality": "480p", "episodes": [{"url": "https://c.example/3", "size": "20 MB"}]}]},
                {"url": "https://d.example/4", "size": "30 MB", "quality": "360p"},
            ],
        },
    }
    streams = VidVaultProvider.parse_streams(payload)
    assert [s.url for s in streams] == [
        "https://a.example/1", "https://b.example/2", "https://c.example/3", "https://d.example/4",
    ]
    assert [s.resolution for s in streams] == [1080, 720, 480, 360]
    assert all(s.format == "MKV" for s in streams)
    assert streams[0].size_bytes == int(1.5 * 1024**3)
    assert streams[2].size_bytes == 20 * 1024**2


def test_parse_streams_drops_duplicate_renditions() -> None:
    """Some payloads list the same link under more than one key family."""
    from reelmachine.sources.vidvault import VidVaultProvider

    payload = {
        "mp4Data": {"links": [{"url": "https://x.example/1", "quality": "720p"}]},
        "mkvV2Data": {"url": "https://x.example/1", "quality": "720p"},
    }
    assert len(VidVaultProvider.parse_streams(payload)) == 1


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("289 MB", 289 * 1024**2),
        ("112.33 MB", int(112.33 * 1024**2)),
        ("1.5 GB", int(1.5 * 1024**3)),
        ("32728862", 32_728_862),          # bare byte string (legacy)
        (32_728_862, 32_728_862),          # bare int (legacy)
        ("74 MB", 74 * 1024**2),
        ("", 0),
        (None, 0),
        ("Unknown", 0),
        (True, 0),
    ],
)
def test_coerce_size(raw, expected) -> None:
    from reelmachine.sources.vidvault import coerce_size

    assert coerce_size(raw) == expected


def test_new_shape_streams_are_selectable_by_the_ladder() -> None:
    """End to end from a live payload: the ladder picks the Japanese 720p, not nothing.

    `LIVE_PAYLOAD`'s `mkvV2Data` is the only rendition that states a language,
    and it says Japanese — so it wins over the larger, untagged 1080p MP4,
    because the dialogue alignment needs the original audio.
    """
    from reelmachine.sources.vidvault import VidVaultProvider

    streams = VidVaultProvider.parse_streams(LIVE_PAYLOAD)
    chosen = pick_stream(streams, quality="1080,720,480,360", now=NOW)
    assert chosen is not None
    assert chosen.url == "https://mk2.example/d/deadbeef"
    assert chosen.resolution == 720 and chosen.language == "Japanese"
    # The untagged renditions are still selectable when nothing is tagged.
    untagged = [s for s in streams if not s.language]
    assert pick_stream(untagged, quality="1080,720,480,360", now=NOW).url == "https://tdm.example/dl/bbb"


def test_japanese_audio_wins_over_a_bigger_dub() -> None:
    """A 1080p dub is worth less than a 720p original: a dub cannot be aligned."""
    from reelmachine.sources.vidvault import is_japanese

    japanese = Stream(720, "MKV", 1, 0, "https://x/jp", group="MKV", language="Japanese")
    hindi = Stream(1080, "MP4", 1, 0, "https://x/hin", group="MP4", language="Hindi")
    english = Stream(720, "MP4", 1, 0, "https://x/eng", group="MP4", language="English")

    assert [is_japanese(s) for s in (japanese, hindi, english)] == [True, False, False]
    assert pick_stream([hindi, english, japanese], quality="1080,720,480,360").url == "https://x/jp"
    # With no Japanese on offer, behaviour is unchanged — best rung wins.
    assert pick_stream([hindi, english], quality="1080,720,480,360").url == "https://x/hin"
    # `ja`/`jpn` spellings count too.
    assert is_japanese(Stream(720, "MKV", 1, 0, "https://x", language="ja"))
    assert is_japanese(Stream(720, "MKV", 1, 0, "https://x", language="jpn"))


# ------------------------------------------------------------- segment planning


@pytest.mark.parametrize("total,connections", [(100, 8), (10 << 20, 8), (77 << 20, 8), (1, 4)])
def test_plan_segments_covers_every_byte_exactly_once(total: int, connections: int) -> None:
    from reelmachine.sources.vidvault import VidVaultProvider

    ranges = VidVaultProvider.plan_segments(total, connections)
    assert ranges, "a non-empty body must always yield at least one range"
    assert ranges[0][0] == 0 and ranges[-1][1] == total - 1
    for (_, end), (next_start, _) in zip(ranges, ranges[1:]):
        assert next_start == end + 1, "ranges must be contiguous and non-overlapping"


def test_plan_segments_does_not_shred_a_small_file() -> None:
    from reelmachine.sources.vidvault import VidVaultProvider

    assert len(VidVaultProvider.plan_segments(1 << 20, 8)) == 1      # 1 MB -> one request
    assert len(VidVaultProvider.plan_segments(0, 8)) == 0


def test_plan_segments_sizes_by_chunk_not_by_connection_count() -> None:
    """One stalled link must not be able to waste a quarter of the episode.

    The A/V worker for the Japanese renditions runs at ~0.03–0.25 MB/s, so a
    range sized as total/connections is many minutes of transfer to lose.  Range
    size is therefore bounded by `chunk`, and `connections` only bounds how many
    run at once.
    """
    from reelmachine.sources.vidvault import VidVaultProvider

    # 212 MB (the Re:ZERO episode) -> ~4 MB ranges whatever the concurrency.
    for connections in (1, 8, 32):
        ranges = VidVaultProvider.plan_segments(212 << 20, connections)
        assert len(ranges) == 53
        assert max(end - start + 1 for start, end in ranges) == 4 << 20


# ----------------------------------------------------------- real segmented I/O


@pytest.fixture
def no_backoff(monkeypatch):
    """Range retries sleep up to 4 s; tests should not."""
    from reelmachine.sources import vidvault

    monkeypatch.setattr(vidvault, "RETRY_BACKOFF_S", 0.0)


def _provider(downloads: Path | None = None):
    import dataclasses

    from reelmachine.config import get_settings
    from reelmachine.sources.vidvault import VidVaultProvider

    provider = VidVaultProvider.__new__(VidVaultProvider)  # no network, no TMDB
    settings = get_settings()
    # `get_settings` is lru_cached and shared; copy before redirecting the
    # download dir so a test never writes into (or repoints) the real one.
    provider.settings = dataclasses.replace(settings, vidvault_dir=downloads) if downloads else settings
    provider._token = ""
    provider._token_expiry = 0.0
    provider.tmdb = None
    return provider


class _CdnHandler(http.server.BaseHTTPRequestHandler):
    """A byte-range origin that can misbehave the two ways the real CDN does."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        server = self.server
        body: bytes = server.body  # type: ignore[attr-defined]
        match = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range") or "")
        with server.lock:  # type: ignore[attr-defined]
            server.active += 1  # type: ignore[attr-defined]
            server.peak = max(server.peak, server.active)  # type: ignore[attr-defined]
            server.requests.append(match.group(0) if match else None)  # type: ignore[attr-defined]
        try:
            if match is None or not server.support_range:  # type: ignore[attr-defined]
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Type", "video/x-matroska")
                self.end_headers()
                self.wfile.write(body)
                return
            start = int(match.group(1))
            end = min(int(match.group(2) or len(body) - 1), len(body) - 1)
            with server.lock:  # type: ignore[attr-defined]
                key = (start, end)
                drop = key in server.drop_once and key not in server.dropped  # type: ignore[attr-defined]
                if drop:
                    server.dropped.add(key)  # type: ignore[attr-defined]
                if key in server.drop_always:  # type: ignore[attr-defined]
                    drop = True
            if drop:
                # Advertise the whole range, then hang up a third of the way in:
                # exactly the truncation the real CDN produces.
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                self.wfile.write(body[start:start + max(1, (end - start + 1) // 3)])
                self.wfile.flush()
                self.close_connection = True
                return
            payload = body[start:end + 1]
            if server.delay:  # type: ignore[attr-defined]
                time.sleep(server.delay)  # type: ignore[attr-defined]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Content-Type", "video/x-matroska")
            self.end_headers()
            self.wfile.write(payload)
        finally:
            with server.lock:  # type: ignore[attr-defined]
                server.active -= 1  # type: ignore[attr-defined]


class _Cdn(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: object, client_address: object) -> None:
        import sys

        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)  # type: ignore[arg-type]


@contextlib.contextmanager
def _cdn(body: bytes, *, support_range: bool = True, drop_once=(), drop_always=(), delay: float = 0.0):
    server = _Cdn(("127.0.0.1", 0), _CdnHandler)
    server.body = body                      # type: ignore[attr-defined]
    server.support_range = support_range    # type: ignore[attr-defined]
    server.lock = threading.Lock()          # type: ignore[attr-defined]
    server.active = 0                       # type: ignore[attr-defined]
    server.peak = 0                         # type: ignore[attr-defined]
    server.requests = []                    # type: ignore[attr-defined]
    server.dropped = set()                  # type: ignore[attr-defined]
    server.drop_once = set(drop_once)       # type: ignore[attr-defined]
    server.drop_always = set(drop_always)   # type: ignore[attr-defined]
    server.delay = delay                    # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}/ep.mp4"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _body(size: int) -> bytes:
    """Deterministic random-looking bytes, so a mis-assembled file is caught.

    `random.randbytes` is C-speed; a per-byte generator would take seconds at
    these sizes.  The seed is fixed so failures are reproducible.
    """
    return random.Random(0xC0FFEE).randbytes(size)


SIZE = 16 << 20  # 4 ranges at the default 4 MB chunk


def test_segmented_download_reassembles_the_file_exactly(tmp_path: Path, no_backoff) -> None:
    body = _body(SIZE)
    with _cdn(body, delay=0.05) as (server, url):
        dest = tmp_path / "ep.mp4"
        got = _provider().download(Stream(720, "MKV", len(body), 0, url, group="MKV"),
                                   dest, refresh=True, connections=6)
        assert got == dest
        assert dest.read_bytes() == body, "parallel ranges must reassemble byte-for-byte"
        assert server.peak > 1, "the CDN throttle means this must actually run in parallel"
        assert not (tmp_path / "ep.mp4.part").exists()
        assert not (tmp_path / "ep.mp4.part.json").exists(), "checkpoint must be cleaned up"


def test_segmented_download_retries_the_truncated_range(tmp_path: Path, no_backoff) -> None:
    """A range cut off mid-transfer must be re-fetched, not accepted short."""
    from reelmachine.sources.vidvault import VidVaultProvider

    body = _body(SIZE)
    ranges = VidVaultProvider.plan_segments(len(body), 4)
    assert len(ranges) == 4
    with _cdn(body, drop_once=[ranges[1]]) as (server, url):
        dest = tmp_path / "ep.mp4"
        _provider().download(Stream(720, "MKV", len(body), 0, url), dest, refresh=True, connections=4)
        assert dest.read_bytes() == body
        assert server.requests.count(f"bytes={ranges[1][0]}-{ranges[1][1]}") == 2


def test_segmented_download_resumes_from_the_checkpoint(tmp_path: Path, no_backoff) -> None:
    """Ranges already banked are not re-fetched after a failure."""
    from reelmachine.sources.base import SourceError
    from reelmachine.sources.vidvault import VidVaultProvider

    body = _body(SIZE)
    ranges = VidVaultProvider.plan_segments(len(body), 4)
    doomed = ranges[-1]
    dest = tmp_path / "ep.mp4"
    stream = Stream(720, "MKV", len(body), 0, "placeholder")

    with _cdn(body, drop_always=[doomed]) as (server, url):
        stream.url = url
        with pytest.raises(SourceError, match="failed"):
            _provider().download(stream, dest, refresh=True, connections=4)
        assert (tmp_path / "ep.mp4.part").is_file()
        assert (tmp_path / "ep.mp4.part.json").is_file(), "progress must be recorded for resume"
        assert len(server.requests) > 4

    # The link is "re-minted" (same body) and the failing range now behaves.
    with _cdn(body, drop_always=[]) as (server2, url2):
        stream.url = url2
        _provider().download(stream, dest, refresh=True, connections=4)
        assert dest.read_bytes() == body
        # `bytes=0-0` is the one-byte size probe; the only *range* re-fetched is
        # the one that failed, because the other three are banked in the checkpoint.
        ranged = [r for r in server2.requests if r and r != "bytes=0-0"]
        assert ranged == [f"bytes={doomed[0]}-{doomed[1]}"]


def test_download_falls_back_to_a_single_stream(tmp_path: Path, no_backoff) -> None:
    """A CDN that ignores Range must still work, just serially."""
    body = _body(6 << 20)
    with _cdn(body, support_range=False) as (server, url):
        dest = tmp_path / "ep.mp4"
        _provider().download(Stream(720, "MKV", len(body), 0, url), dest, refresh=True, connections=8)
        assert dest.read_bytes() == body
        assert server.peak == 1


def test_connections_one_skips_segmentation(tmp_path: Path, no_backoff) -> None:
    body = _body(6 << 20)
    with _cdn(body) as (server, url):
        dest = tmp_path / "ep.mp4"
        _provider().download(Stream(720, "MKV", len(body), 0, url), dest, refresh=True, connections=1)
        assert dest.read_bytes() == body
        assert server.peak == 1


def test_download_refuses_an_empty_url(tmp_path: Path) -> None:
    from reelmachine.sources.base import SourceError

    with pytest.raises(SourceError, match="empty stream url"):
        _provider().download(Stream(720, "MKV", 0, 0, ""), tmp_path / "ep.mp4")


def test_download_reuses_an_existing_file_without_refetching(tmp_path: Path) -> None:
    dest = tmp_path / "ep.mp4"
    dest.write_bytes(b"already here")
    # No server is started: a re-used file must never touch the network.
    assert _provider().download(Stream(720, "MKV", 0, 0, "http://127.0.0.1:1/never"), dest) == dest


def test_checkpoint_from_a_different_range_layout_is_ignored(tmp_path: Path) -> None:
    """A stale layout must re-fetch, never mark un-written ranges as done.

    Changing `chunk` (or the connection count) renumbers the ranges.  If the old
    indices were trusted, ranges would be marked complete whose bytes were never
    written — silent holes in the middle of the episode.
    """
    from reelmachine.sources.vidvault import VidVaultProvider

    total = 16 << 20
    part = tmp_path / "ep.mp4.part"
    part.write_bytes(b"\0" * total)
    checkpoint = tmp_path / "ep.mp4.part.json"

    coarse = VidVaultProvider.plan_segments(total, 1, chunk=8 << 20)   # 2 ranges
    fine = VidVaultProvider.plan_segments(total, 8, chunk=4 << 20)     # 4 ranges
    assert (len(coarse), len(fine)) == (2, 4)

    VidVaultProvider._write_checkpoint(checkpoint, total, {0, 1}, coarse)
    # The coarse file claims "everything done"; the fine layout must not believe it.
    assert VidVaultProvider._read_checkpoint(checkpoint, total, part, fine) == set()
    # Same layout still round-trips.
    assert VidVaultProvider._read_checkpoint(checkpoint, total, part, coarse) == {0, 1}


def test_download_retries_with_a_freshly_minted_link(tmp_path: Path, no_backoff, monkeypatch) -> None:
    """A link that dies mid-download must be re-minted, not retried as-is.

    The signed URL outlives nothing like the time a large Japanese rendition
    takes to fetch, so re-requesting the *same* URL can never recover; the
    provider has to ask for a new one and resume from the checkpoint.
    """
    from reelmachine.sources import vidvault
    from reelmachine.sources.vidvault import VidVaultProvider

    body = _body(SIZE)
    ranges = VidVaultProvider.plan_segments(len(body), 4)
    dest = tmp_path / "tmdb1-s01e001-720p.mp4"

    provider = _provider(downloads=tmp_path)
    link = {"url": "http://127.0.0.1:1/dead", "lookups": 0, "tokens": 0}

    def fake_lookup(tmdb_id, **kwargs):
        link["lookups"] += 1
        return {"mp4Data": {"links": [{"url": link["url"], "quality": "720p", "size": "16 MB"}]}}

    def fake_token(*, force: bool = False):
        link["tokens"] += 1
        return "fresh-token"

    provider.lookup = fake_lookup              # type: ignore[method-assign]
    provider.request_token = fake_token        # type: ignore[method-assign]
    monkeypatch.setattr(vidvault, "RETRY_BACKOFF_S", 0.0)

    match = TmdbMatch(tmdb_id=1, media_type="tv", season=1, title="T", year=None,
                      score=9.9, query="1", reason="test")
    # First link is unreachable, so the download must fail and trigger a re-mint.
    with pytest.raises(vidvault.SourceError):
        provider.resolve(None, 1, match=match, download=True, connections=4)
    assert link["lookups"] >= 3, "each retry should ask for a fresh link"
    assert link["tokens"] >= 2, "each retry should mint a fresh token"

    # Now the re-minted link is live: the same call must succeed.
    with _cdn(body) as (_server, url):
        link["url"] = url
        link["lookups"] = 0
        asset = provider.resolve(None, 1, match=match, download=True, connections=4)
        assert asset.local_path == dest
        assert dest.read_bytes() == body


def test_provider_does_not_shadow_the_base_probe() -> None:
    """`SourceProvider.probe(asset)` runs ffprobe and is called all over the pipeline.

    The segment size probe must therefore live under its own name; overriding
    `probe` here would silently break every run *after* the download.
    """
    from reelmachine.sources.base import SourceProvider
    from reelmachine.sources.vidvault import VidVaultProvider

    assert VidVaultProvider.probe is SourceProvider.probe
    assert "probe_url" in VidVaultProvider.__dict__


# --------------------------------------------------------------- tmdb matching


def _client(tmp_path: Path, *, api_key: str = "test-key"):
    """A TmdbClient with a per-test cache and no network."""
    from reelmachine.tmdb import TmdbClient, _Cache

    client = TmdbClient.__new__(TmdbClient)  # bypass __init__ (which loads settings)
    client.settings = None
    client.api_key = api_key
    client.timeout = 5
    client._cache = _Cache(tmp_path / "tmdb.json")
    client._cache.load()
    return client


class _FakeTmdb:
    """Stands in for the network: canned search results per query."""

    def __init__(self, table: dict[str, list[dict]]):
        self.table = table

    def search(self, query: str, media_type: str) -> list[dict]:
        return self.table.get(f"{media_type}:{query}", [])

    def season_episode_count(self, tmdb_id: int, season: int) -> int:
        return 12


def _media(name_en: str, **kw):
    from reelmachine.models import Media

    return Media(publicId="V1StGXR8_Z5d", slug="x", nameEn=name_en, **kw)


def test_match_prefers_exact_title_and_year(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.search = _FakeTmdb(
        {
            "tv:Violet Evergarden": [
                {"id": 75214, "name": "Violet Evergarden", "first_air_date": "2018-01-11", "popularity": 90},
                {"id": 999, "name": "Violet Evergarden Special", "first_air_date": "2018-07-01", "popularity": 5},
            ]
        }
    ).search

    match = client.match_media(_media("Violet Evergarden"))
    assert match is not None
    assert match.tmdb_id == 75214
    assert match.season == 1
    assert match.media_type == "tv"


def test_match_extracts_season_and_searches_the_base_title(tmp_path: Path) -> None:
    client = _client(tmp_path)
    seen: list[str] = []

    def search(query: str, media_type: str) -> list[dict]:
        seen.append(query)
        if query == "Rent-a-Girlfriend":
            return [{"id": 92683, "name": "Rent-a-Girlfriend", "first_air_date": "2020-07-11", "popularity": 120}]
        return []

    client.search = search
    match = client.match_media(_media("Rent-a-Girlfriend Season 3"))
    assert match is not None
    assert match.tmdb_id == 92683
    assert match.season == 3
    assert "Rent-a-Girlfriend" in seen
    assert "Rent-a-Girlfriend Season 3" not in seen  # the marker is stripped first


def test_match_returns_none_without_a_confident_hit(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.search = lambda query, media_type: [
        {"id": 1, "name": "Something Completely Different", "first_air_date": "1999-01-01"}
    ]
    assert client.match_media(_media("Kimi ni Todoke")) is None


def test_movie_format_searches_movies(tmp_path: Path) -> None:
    client = _client(tmp_path)
    seen: list[str] = []

    def search(query: str, media_type: str) -> list[dict]:
        seen.append(media_type)
        if media_type == "movie":
            return [{"id": 50863, "title": "Summer Wars", "release_date": "2009-08-01", "popularity": 40}]
        return []

    client.search = search
    match = client.match_media(_media("Summer Wars", airingFormat="MOVIE"))
    assert match is not None and match.media_type == "movie"
    assert seen == ["movie"]


def test_local_path_naming_is_stable() -> None:
    from reelmachine.sources.vidvault import VidVaultProvider

    match = TmdbMatch(tmdb_id=92683, media_type="tv", season=3, title="Rent-a-Girlfriend",
                      year=2020, score=3.0, query="Rent-a-Girlfriend")
    provider = VidVaultProvider.__new__(VidVaultProvider)
    from reelmachine.config import get_settings

    provider.settings = get_settings()
    path = provider.local_path(match, 7, 720)
    assert path.name == "tmdb92683-s03e007-720p.mp4"
    # same episode always maps to the same file, so a re-run never re-downloads
    assert provider.local_path(match, 7, 720) == path
    assert provider.local_path(match, 8, 720) != path
