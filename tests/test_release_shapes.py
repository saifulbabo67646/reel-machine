"""Real-world release shapes: embedded subtitle and extra audio tracks.

Anime releases come with softsubs, dual audio, or both.  None of that should
change the reel: alignment is audio-only, the cut takes video + one audio track
and drops everything else, and the reel's own subtitles come from Nadeshiko.

Hardcoded (burned-in) subtitles are the exception and cannot be dropped — there
is a test for what that looks like so the distinction is documented in code.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from reelmachine import ffmpeg
from reelmachine.config import get_settings
from reelmachine.sources.base import EpisodeAsset
from reelmachine.sources.mock import MockProvider

pytestmark = pytest.mark.slow

def stream_dump(path: Path) -> str:
    """ffmpeg's stream listing, without raising on the expected exit code 1.

    `ffmpeg -i file` with no output is how you inspect streams, and it always
    exits non-zero; `ffmpeg.run` would treat that as a failure.
    """
    proc = subprocess.run(
        [get_settings().ffmpeg, "-hide_banner", "-nostdin", "-i", str(path)],
        capture_output=True, text=True,
    )
    return proc.stderr or ""


SRT = """1
00:00:01,000 --> 00:00:04,000
This is an embedded subtitle

2
00:00:05,000 --> 00:00:08,000
It must not survive the cut
"""


@pytest.fixture(scope="module")
def source_episode(tmp_path_factory: pytest.TempPathFactory) -> Path:
    settings = get_settings()
    provider = MockProvider(settings)
    provider.duration_s = 40
    return Path(provider.resolve(None, 1).url)


@pytest.fixture(scope="module")
def release_like(source_episode: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A file shaped like a fansub release: 2 audio tracks + 2 subtitle tracks."""
    root = tmp_path_factory.mktemp("release")
    srt = root / "subs.srt"
    srt.write_text(SRT, encoding="utf-8")
    srt2 = root / "subs2.srt"
    srt2.write_text(SRT.replace("embedded subtitle", "second track"), encoding="utf-8")

    dest = root / "release.mkv"
    ffmpeg.run([
        get_settings().ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(source_episode),
        "-f", "lavfi", "-i", "sine=frequency=440:duration=40",   # stand-in dub
        "-i", str(srt), "-i", str(srt2),
        "-map", "0:v", "-map", "1:a", "-map", "0:a", "-map", "2:s", "-map", "3:s",
        "-c:v", "copy", "-c:a", "aac", "-c:s", "srt",
        "-metadata:s:a:0", "language=eng",
        "-metadata:s:a:1", "language=jpn",
        "-metadata:s:s:0", "language=eng",
        "-metadata:s:s:1", "language=eng",
        "-disposition:s:0", "default",
        "-y", str(dest),
    ])
    return dest


def test_probe_survives_a_release_shaped_file(release_like: Path) -> None:
    info = ffmpeg.probe(release_like)
    assert info.has_video and info.has_audio
    assert info.audio_tracks == 2
    assert info.audio_langs == ["eng", "jpn"]
    # Subtitle streams must not be miscounted as audio.
    assert ffmpeg.pick_japanese_track(info) == 1


def test_cut_drops_subtitles_and_takes_the_japanese_track(release_like: Path, tmp_path: Path) -> None:
    asset = EpisodeAsset(
        media_public_id="m", episode=1, url=str(release_like), local_path=release_like
    )
    asset.info = ffmpeg.probe(release_like)
    asset.audio_track = ffmpeg.pick_japanese_track(asset.info)

    dest = tmp_path / "cut.mp4"
    ffmpeg.cut_clip(
        asset.url, dest, start_s=5.0, duration_s=4.0, height=360,
        preset="ultrafast", audio_track=asset.audio_track,
    )

    # Exactly one video and one audio stream out; no subtitle streams at all.
    dump = stream_dump(dest)
    assert "Subtitle:" not in dump
    assert dump.count("Video:") == 1 and dump.count("Audio:") == 1
    out = ffmpeg.probe(dest)
    assert out.has_video and out.has_audio
    assert out.audio_tracks == 1
    assert out.duration_s == pytest.approx(4.0, abs=0.2)


def test_embedded_subtitles_do_not_reach_the_reel(release_like: Path, tmp_path: Path) -> None:
    """The reel's subtitles are Nadeshiko's; the file's own are irrelevant."""
    from reelmachine import reel as reelmod
    from reelmachine.align import TimelineMap

    asset = EpisodeAsset(
        media_public_id="m", episode=1, url=str(release_like), local_path=release_like,
        duration_ms=40_000,
    )
    asset.info = ffmpeg.probe(release_like)
    asset.audio_track = ffmpeg.pick_japanese_track(asset.info)

    provider = MockProvider(get_settings())
    provider.duration_s = 40
    segments = provider.make_segments("MOCKMEDIA001", 1, count=1)

    plan = reelmod.Plan(word="彼女", source="test")
    plan.items.append(
        reelmod.PlannedSegment(
            segment=segments[0], asset=asset, timeline=TimelineMap(),
            local_start_ms=8_000, local_end_ms=13_000,
            scene_start_ms=8_000, scene_end_ms=13_000, status="ok",
        )
    )
    result = reelmod.render(plan, outdir=tmp_path, name="release-reel")

    dump = stream_dump(result.video)
    assert "Subtitle:" not in dump
    assert dump.count("Audio:") == 1
    assert ffmpeg.probe(result.video).audio_tracks == 1

    # ...and the reel's own subtitle file carries Nadeshiko's text.  Strip ASS
    # override blocks first: the target-word highlight is injected `{\c…}`
    # inside the line, so the raw text is not contiguous.
    ass = re.sub(r"\{[^}]*\}", "", result.ass.read_text(encoding="utf-8"))
    assert segments[0].japanese in ass
    assert "embedded subtitle" not in ass
