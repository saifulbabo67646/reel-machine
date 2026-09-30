"""End-to-end test using *real* Nadeshiko data.

The video source is synthesised — the real reference clips are embedded in a
noise bed at known positions — but everything else is genuine: the segments come
from a live `/v1/search`, the Japanese text, English translations and timestamps
are the API's, and the audio being correlated is the API's own `urls.audioUrl`.

That matters because the hard part of this pipeline is aligning real speech, not
synthetic noise.  Real reference audio is short, compressed, band-limited to
speech, and mono; if the correlator survives that, it survives a real episode.

Skipped automatically when no API key is configured.
"""

from __future__ import annotations

import re
import wave
from pathlib import Path

import numpy as np
import pytest

from reelmachine import ffmpeg, reel as reelmod
from reelmachine.align import align_episode
from reelmachine.config import get_settings
from reelmachine.models import Segment
from reelmachine.nadeshiko import NadeshikoClient

pytestmark = pytest.mark.slow

WORD = "やっぱり"
SAMPLE_RATE = 48000
EPISODE_S = 300
TRUE_OFFSET_MS = 3750        # how much later the "local copy" runs
PLACEMENTS_MS = [40_000, 150_000, 260_000]

needs_key = pytest.mark.skipif(not get_settings().api_key, reason="no NADESHIKO_API_KEY")


def _write_wav(path: Path, samples: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SAMPLE_RATE)
        fh.writeframes(pcm.tobytes())
    return path


def _decode(path: Path) -> np.ndarray:
    return ffmpeg.read_pcm(path, sample_rate=SAMPLE_RATE)


@pytest.fixture(scope="module")
def real_segments() -> list[Segment]:
    """Three live segments for the word, each from a different title."""
    settings = get_settings()
    with NadeshikoClient(settings=settings) as client:
        page = client.search(WORD, take=50)
        picked: list[Segment] = []
        seen: set[str] = set()
        for segment in page.segments:
            if segment.mediaPublicId in seen:
                continue
            if segment.duration_ms < 900 or not segment.english:
                continue
            seen.add(segment.mediaPublicId)
            picked.append(segment)
            if len(picked) == len(PLACEMENTS_MS):
                break
    if len(picked) < len(PLACEMENTS_MS):
        pytest.skip("not enough distinct titles for this word")
    return picked


@pytest.fixture(scope="module")
def synthetic_episode(real_segments: list[Segment], tmp_path_factory: pytest.TempPathFactory):
    """An episode containing the real reference clips at known positions."""
    settings = get_settings()
    root = tmp_path_factory.mktemp("real-demo")
    clips: dict[str, Path] = {}
    with NadeshikoClient(settings=settings) as client:
        for segment in real_segments:
            dest = root / "clips" / f"{segment.publicId}.mp3"
            client.download(segment.urls.audioUrl, dest)
            clips[segment.publicId] = dest

    rng = np.random.default_rng(20260914)
    audio = (rng.standard_normal(EPISODE_S * SAMPLE_RATE) * 0.004).astype(np.float32)
    for segment, at_ms in zip(real_segments, PLACEMENTS_MS):
        pcm = _decode(clips[segment.publicId]) * 0.9
        start = int(at_ms * SAMPLE_RATE / 1000)
        end = min(audio.size, start + pcm.size)
        audio[start:end] += pcm[: end - start]

    wav = _write_wav(root / "episode.wav", audio)
    video = root / f"{WORD}-demo.mkv"
    ffmpeg.run(
        [
            settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={EPISODE_S}",
            "-i", str(wav),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", str(SAMPLE_RATE), "-shortest",
            "-y", str(video),
        ]
    )
    return video, clips


@needs_key
def test_real_reference_audio_aligns(synthetic_episode, real_segments) -> None:
    """The API's own clips must locate themselves in the episode."""
    video, clips = synthetic_episode

    # Give the segments timestamps as if they came from a differently-timed copy:
    # Nadeshiko's clock runs TRUE_OFFSET_MS behind the local file.
    shifted = [
        segment.model_copy(
            update={
                "startTimeMs": at_ms - TRUE_OFFSET_MS,
                "endTimeMs": at_ms - TRUE_OFFSET_MS + segment.duration_ms,
            }
        )
        for segment, at_ms in zip(real_segments, PLACEMENTS_MS)
    ]

    timeline = align_episode(
        video,
        shifted,
        clip_paths=clips,
        max_anchors=len(shifted),
        episode_duration_ms=EPISODE_S * 1000,
    )

    assert timeline.ok, timeline.reason
    assert timeline.offset_ms == pytest.approx(TRUE_OFFSET_MS, abs=250)
    assert timeline.confidence > 0.3
    # Every anchor should have found its own clip where it was actually placed.
    for anchor, at_ms in zip(sorted(timeline.anchors, key=lambda a: a.src_ms), PLACEMENTS_MS):
        assert anchor.found_ms == pytest.approx(at_ms, abs=250)


@needs_key
def test_real_segments_render_a_reel(synthetic_episode, real_segments, tmp_path: Path) -> None:
    """The whole chain on real text: align, cut, subtitle, mux."""
    video, clips = synthetic_episode
    shifted = [
        segment.model_copy(
            update={
                "startTimeMs": at_ms - TRUE_OFFSET_MS,
                "endTimeMs": at_ms - TRUE_OFFSET_MS + segment.duration_ms,
            }
        )
        for segment, at_ms in zip(real_segments, PLACEMENTS_MS)
    ]
    timeline = align_episode(
        video, shifted, clip_paths=clips, max_anchors=len(shifted),
        episode_duration_ms=EPISODE_S * 1000,
    )
    assert timeline.ok, timeline.reason

    plan = reelmod.Plan(word=WORD, source="test")
    media = {s.mediaPublicId: None for s in shifted}
    for segment in shifted:
        plan.items.append(
            reelmod.PlannedSegment(
                segment=segment,
                media=media.get(segment.mediaPublicId),
                asset=_asset_for(video, segment),
                timeline=timeline,
                local_start_ms=timeline.to_local_ms(segment.startTimeMs),
                local_end_ms=timeline.to_local_ms(segment.endTimeMs),
                status="ok",
            )
        )

    result = reelmod.render(plan, outdir=tmp_path, name="real-demo")

    assert result.video.is_file() and result.video.stat().st_size > 20_000
    info = ffmpeg.probe(result.video)
    assert info.duration_s == pytest.approx(result.duration_ms / 1000.0, abs=0.25)
    assert info.width == 1080 and info.height == 1920

    # The real Japanese line and its translation must be in the subtitle file.
    # Strip ASS override blocks first: the target-word highlight is injected as
    # `{\c…}` *inside* the line, so the raw text is not contiguous.
    ass = re.sub(r"\{[^}]*\}", "", result.ass.read_text(encoding="utf-8"))
    for segment in shifted:
        assert segment.japanese.strip() in ass
        assert segment.english.strip() in ass
    assert WORD in ass


def _asset_for(video: Path, segment: Segment):
    from reelmachine.sources.base import EpisodeAsset

    return EpisodeAsset(
        media_public_id=segment.mediaPublicId,
        episode=segment.episode,
        url=str(video),
        duration_ms=EPISODE_S * 1000,
        label=f"demo ep{segment.episode}",
        local_path=video,
    )
