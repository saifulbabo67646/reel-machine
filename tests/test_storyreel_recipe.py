"""End-to-end storyreel with a synthetic film and deterministic providers.

A generated 90-second "film", a fake source provider, the fake subtitle/brain/narration
providers and the real ffmpeg: the three modes are exercised, the render is probed for
real, and the plan→build cache hand-off is asserted from the manifest.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from reelmachine import ffmpeg
from reelmachine.config import get_settings
from reelmachine.core.job import JobRequest, JobState
from reelmachine.core.registry import Registry
from reelmachine.engine import Engine
from reelmachine.recipes.storyreel.subtitles import SubtitleRequest
from reelmachine.recipes.storyreel.subtitles.embedded import EmbeddedSubtitleProvider
from reelmachine.sources.base import EpisodeAsset, SourceProvider
from reelmachine.tmdb import TitleCandidate, TitleDetail

pytestmark = pytest.mark.slow

FILM_SECONDS = 90
FILM: Path | None = None


class FakeSourceProvider(SourceProvider):
    """Hands the pipeline the synthetic film as if it had downloaded it."""

    name = "storyreel-fake-source"

    def resolve(self, media, episode, **kwargs) -> EpisodeAsset:  # type: ignore[override]
        assert FILM is not None, "the film fixture did not run"
        return EpisodeAsset(
            media_public_id=f"tmdb:{kwargs.get('tmdb_id', 0)}",
            episode=episode,
            url=str(FILM),
            local_path=FILM,
            duration_ms=FILM_SECONDS * 1000,
            label="Test Film (540p)",
        )


class FakeTmdb:
    """No network: the title is always the synthetic film."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def detail(self, tmdb_id: int, media_type: str = "movie") -> TitleDetail:
        return TitleDetail(
            tmdb_id=tmdb_id,
            media_type=media_type,
            title="The Lighthouse Test",
            year=2024,
            original_language="en",
            runtime_min=2,
            genres=["Drama"],
        )

    def find_title(self, title: str, *, media_type: str = "movie", year=None, limit=5):
        return [
            TitleCandidate(
                tmdb_id=42, media_type=media_type, title="The Lighthouse Test", year=2024, popularity=99.0
            )
        ]

    def discover_recent(self, *, media_type: str = "movie", months: int = 12, limit: int = 10, min_votes: int = 80):
        return []


class _StubEntryPoint:
    def __init__(self, name: str, group: str, value) -> None:
        self.name = name
        self.group = group
        self._value = value

    def load(self):
        return self._value


class StoryRegistry(Registry):
    """The real registry, plus one test-only source provider."""

    def entry_points_for(self, group: str):
        entries = list(super().entry_points_for(group))
        if self.group_name(group) == "reelmachine.sources":
            entries.append(_StubEntryPoint("storyreel-fake-source", "reelmachine.sources", FakeSourceProvider))
        return entries


@pytest.fixture(scope="module")
def film(tmp_path_factory):
    global FILM
    settings = get_settings()
    path = tmp_path_factory.mktemp("film") / "lighthouse.mp4"
    ffmpeg.run(
        [
            settings.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=960x540:rate=30:duration={FILM_SECONDS}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={FILM_SECONDS}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "32",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ]
    )
    FILM = path
    return path


def _engine(tmp_path, monkeypatch) -> Engine:
    monkeypatch.setenv("REEL_SUBTITLES_SYNC", "off")
    # never inherit the developer's deployment voice: these tests speak with the fake
    monkeypatch.setenv("REEL_STORY_VOICE", "fake:default")
    monkeypatch.setattr("reelmachine.recipes.storyreel.stages.TmdbClient", FakeTmdb)
    monkeypatch.setattr("reelmachine.recipes.storyreel.recipe.TmdbClient", FakeTmdb)
    settings = dataclasses.replace(
        get_settings(), workdir=tmp_path / "work", outdir=tmp_path / "out"
    )
    return Engine(
        settings,
        registry=StoryRegistry(),
        provider_overrides={
            "source": "storyreel-fake-source",
            "subtitles": "fake",
            "brain": "fake",
            "narration": "fake",
        },
    )


def _artifact(finished, name: str) -> Path:
    for artifact in finished.result.artifacts:
        if artifact.name == name:
            return Path(artifact.path)
    raise AssertionError(f"artifact {name!r} missing from {[a.name for a in finished.result.artifacts]}")


def _manifest(finished) -> dict:
    return json.loads(Path(finished.result.manifest).read_text(encoding="utf-8"))


def test_storyreel_transcript_mode_halts_with_a_readable_script(film, tmp_path, monkeypatch) -> None:
    engine = _engine(tmp_path, monkeypatch)
    try:
        job = engine.submit(
            JobRequest(
                recipe="storyreel",
                inputs={"tmdb_id": 42, "mode": "transcript", "target_ms": 60_000},
            ),
            caller="local",
        )
        finished = engine.wait(job.id, caller="local", timeout_s=300)
    finally:
        engine.close()

    assert finished.state is JobState.SUCCEEDED, finished.error
    names = {artifact.name for artifact in finished.result.artifacts}
    assert {"transcript.json", "transcript.txt", "subtitles.srt"} <= names
    assert "reel.mp4" not in names, "transcript mode must stop before the render"

    manifest = _manifest(finished)
    stages = [record["id"] for record in manifest["stages"]]
    assert stages == ["acquire", "subtitles", "transcript"]
    assert manifest["verification"]["ok"] is True

    transcript = json.loads(_artifact(finished, "transcript.json").read_text(encoding="utf-8"))
    assert transcript["stats"]["cues"] > 5
    assert transcript["scenes"]
    text = _artifact(finished, "transcript.txt").read_text(encoding="utf-8")
    assert text.startswith("# The Lighthouse Test")


def test_storyreel_plan_mode_halts_with_five_concepts(film, tmp_path, monkeypatch) -> None:
    engine = _engine(tmp_path, monkeypatch)
    try:
        job = engine.submit(
            JobRequest(
                recipe="storyreel",
                inputs={"tmdb_id": 42, "mode": "plan", "target_ms": 60_000},
            ),
            caller="local",
        )
        finished = engine.wait(job.id, caller="local", timeout_s=300)
    finally:
        engine.close()

    assert finished.state is JobState.SUCCEEDED, finished.error
    concepts = json.loads(_artifact(finished, "concepts.json").read_text(encoding="utf-8"))
    assert concepts["source"] == "brain"
    assert len(concepts["concepts"]) == 5
    assert all(concept["why_viral"] for concept in concepts["concepts"])
    manifest = _manifest(finished)
    assert [record["id"] for record in manifest["stages"]] == [
        "acquire",
        "subtitles",
        "transcript",
        "concepts",
    ]
    assert manifest["verification"]["ok"] is True


def test_storyreel_plan_then_build_reuses_the_cached_concepts(film, tmp_path, monkeypatch) -> None:
    engine = _engine(tmp_path, monkeypatch)
    try:
        plan_job = engine.submit(
            JobRequest(
                recipe="storyreel",
                inputs={"tmdb_id": 42, "mode": "plan", "target_ms": 45_000},
            ),
            caller="local",
        )
        plan = engine.wait(plan_job.id, caller="local", timeout_s=300)
        assert plan.state is JobState.SUCCEEDED, plan.error

        build_job = engine.submit(
            JobRequest(
                recipe="storyreel",
                inputs={
                    "tmdb_id": 42,
                    "mode": "build",
                    "target_ms": 45_000,
                    "concept": "c2",
                },
            ),
            caller="local",
        )
        build = engine.wait(build_job.id, caller="local", timeout_s=600)
        assert build.state is JobState.SUCCEEDED, build.error

        # …and the halting modes still halt on a warm cache: the transcript/concepts
        # stages are cached now, and their stop condition must be recomputed from the
        # request in hand, not replayed from the artifact the build job produced.
        late_transcript = engine.wait(
            engine.submit(
                JobRequest(recipe="storyreel", inputs={"tmdb_id": 42, "mode": "transcript"}),
                caller="local",
            ).id,
            caller="local",
            timeout_s=300,
        )
        late_plan = engine.wait(
            engine.submit(
                JobRequest(
                    recipe="storyreel",
                    inputs={"tmdb_id": 42, "mode": "plan", "target_ms": 45_000},
                ),
                caller="local",
            ).id,
            caller="local",
            timeout_s=300,
        )
    finally:
        engine.close()

    assert build.state is JobState.SUCCEEDED, build.error
    manifest = _manifest(build)
    cached = {record["id"]: record["cached"] for record in manifest["stages"]}
    # the expensive mechanical work is not repeated across the two jobs…
    assert cached["subtitles"] is True
    assert cached["transcript"] is True
    # …and neither is the creative work: concepts are keyed without `mode`, so the
    # concept the caller chose in the plan job is the one the build job writes about
    assert cached["concepts"] is True
    story = json.loads(_artifact(build, "story.json").read_text(encoding="utf-8"))
    assert story["story"]["concept_id"] == "c2"

    assert late_transcript.state is JobState.SUCCEEDED, late_transcript.error
    assert [record["id"] for record in _manifest(late_transcript)["stages"]] == [
        "acquire",
        "subtitles",
        "transcript",
    ]
    assert late_plan.state is JobState.SUCCEEDED, late_plan.error
    assert [record["id"] for record in _manifest(late_plan)["stages"]] == [
        "acquire",
        "subtitles",
        "transcript",
        "concepts",
    ]


def test_storyreel_build_renders_the_reel(film, tmp_path, monkeypatch) -> None:
    engine = _engine(tmp_path, monkeypatch)
    try:
        job = engine.submit(
            JobRequest(
                recipe="storyreel",
                inputs={
                    "tmdb_id": 42,
                    "mode": "build",
                    "concept": "c1",
                    "target_ms": 30_000,
                    "content_type": "plot-twist",
                    "language": "en",
                    "platform": "tiktok",
                },
            ),
            caller="local",
        )
        finished = engine.wait(job.id, caller="local", timeout_s=600)
    finally:
        engine.close()

    assert finished.state is JobState.SUCCEEDED, finished.error
    names = {artifact.name for artifact in finished.result.artifacts}
    assert {
        "reel.mp4",
        "captions.ass",
        "captions.srt",
        "transcript.json",
        "concepts.json",
        "story.json",
        "seo.json",
        "plan.json",
        "timeline.json",
        "manifest.json",
    } <= names

    manifest = _manifest(finished)
    assert manifest["render_mode"] == "story"
    assert manifest["verification"]["ok"] is True, manifest["verification"]
    checks = {check["name"]: check for check in manifest["verification"]["checks"]}
    assert checks["clip_coverage"]["ok"] is True
    assert checks["clip_windows"]["ok"] is True
    assert checks["captions"]["ok"] is True

    plan = json.loads(_artifact(finished, "plan.json").read_text(encoding="utf-8"))
    sections = plan["sections"]
    assert len(sections) == len(json.loads(_artifact(finished, "story.json").read_text())["story"]["sections"])
    for section in sections:
        assert section["clip"]["durationMs"] > 0
        assert 0 <= section["clip"]["filmStartMs"] < FILM_SECONDS * 1000
        assert section["endMs"] > section["startMs"]
    # every clip is anchored to the subtitle scene it named: its cut point lies inside
    # that scene's own transcript window (± a small lead-in pad)
    transcript = json.loads(_artifact(finished, "transcript.json").read_text(encoding="utf-8"))
    scenes = {scene["id"]: scene for scene in transcript["scenes"]}
    anchored = [section for section in sections if section["clip"].get("sceneRef")]
    assert anchored, "the fake brain anchors its clips to scenes"
    for section in anchored:
        scene = scenes[section["clip"]["sceneRef"]]
        assert scene["start_ms"] - 1_500 <= section["clip"]["filmStartMs"] <= scene["end_ms"] + 1_500
        # the plan carries the referenced dialogue, so "does the line match the shot?"
        # is reviewable from one file
        assert section["clip"]["sceneText"]
        assert scene["text"].startswith(section["clip"]["sceneText"].rstrip("…"))

    video = _artifact(finished, "reel.mp4")
    info = ffmpeg.probe(video)
    assert info.has_video and info.has_audio
    assert (info.width, info.height) == (1080, 1920)
    assert abs(info.duration_s * 1000 - manifest["timeline"]["duration_ms"]) <= 1200

    ass = _artifact(finished, "captions.ass").read_text(encoding="utf-8")
    assert "Voice" in ass and "Overlay" in ass
    assert "\\k" in ass, "the voice captions are karaoke on the provider's word timings"

    seo = json.loads(_artifact(finished, "seo.json").read_text(encoding="utf-8"))
    assert seo["seo"] and seo["seo"]["title"]


def test_storyreel_build_from_a_caller_story_needs_no_brain(film, tmp_path, monkeypatch) -> None:
    """An agent that did the creative work itself is not blocked by a brain-less deployment.

    `brain` stays at its default (`llm`, no key configured): the concepts stage must pass
    through because the supplied story already names its concept, and the script stage
    must use the story exactly as given.
    """
    monkeypatch.setenv("REEL_SUBTITLES_SYNC", "off")
    monkeypatch.setenv("REEL_STORY_VOICE", "fake:default")
    monkeypatch.setattr("reelmachine.recipes.storyreel.stages.TmdbClient", FakeTmdb)
    settings = dataclasses.replace(
        get_settings(), workdir=tmp_path / "work", outdir=tmp_path / "out"
    )
    engine = Engine(
        settings,
        registry=StoryRegistry(),
        provider_overrides={
            "source": "storyreel-fake-source",
            "subtitles": "fake",
            "narration": "fake",
        },
    )
    story = {
        "concept_id": "c1",
        "concept_name": "The light in the storm",
        "viral_reason": "test",
        "sections": [
            {
                "id": f"sec-{index:03d}",
                "voiceover": f"Maya turned the switch and the storm answered her call, part {index}.",
                "clip": {
                    "source_start_ms": index * 9_000,
                    "scene": "the light",
                    "zoom": "in" if index % 2 else "none",
                    "text_overlay": "THE LIGHT" if index == 1 else "",
                    "transition": "cut",
                },
            }
            for index in range(1, 5)
        ],
        "seo": {"title": "T", "description": "D", "hashtags": ["#x"], "hook": "H", "cta": "C"},
    }
    try:
        job = engine.submit(
            JobRequest(
                recipe="storyreel",
                inputs={"tmdb_id": 42, "mode": "build", "story": story, "target_ms": 30_000},
            ),
            caller="local",
        )
        finished = engine.wait(job.id, caller="local", timeout_s=600)
    finally:
        engine.close()

    assert finished.state is JobState.SUCCEEDED, finished.error
    checks = {c["name"]: c for c in _manifest(finished)["verification"]["checks"]}
    assert _manifest(finished)["verification"]["ok"] is True, checks
    assert checks["concepts"]["ok"] is True  # no brain, no concepts, still a complete run
    story_json = json.loads(_artifact(finished, "story.json").read_text(encoding="utf-8"))
    assert story_json["source"] == "caller"
    assert len(story_json["story"]["sections"]) == 4
    assert _artifact(finished, "reel.mp4").is_file()


def test_a_second_story_is_not_served_from_the_first_ones_cache(film, tmp_path, monkeypatch) -> None:
    """A cached script built for story A must never answer a job carrying story B.

    The regression: stage outputs carry the request, but only `inputs` is refreshed on
    a cache hit — so any derived copy of the request (the language, the supplied story)
    goes stale, and a cache key reading the copy serves the wrong story. Keys read the
    refreshed request through dotted paths (`inputs.story`), so they cannot.
    """
    engine = _engine(tmp_path, monkeypatch)

    def story(marker: str) -> dict:
        return {
            "concept_id": "c1",
            "concept_name": f"{marker} concept",
            "viral_reason": "x",
            "sections": [
                {
                    "id": f"sec-{index:03d}",
                    "voiceover": f"{marker} line {index} — a story about the light.",
                    "clip": {"source_start_ms": index * 8_000, "zoom": "none", "transition": "cut"},
                }
                for index in range(1, 5)
            ],
            "seo": {"title": marker, "description": "D", "hashtags": ["#x"], "hook": "H", "cta": "C"},
        }

    try:
        first = engine.wait(
            engine.submit(
                JobRequest(
                    recipe="storyreel",
                    inputs={"tmdb_id": 42, "mode": "build", "language": "en", "target_ms": 30_000, "story": story("alpha")},
                ),
                caller="local",
            ).id,
            caller="local",
            timeout_s=600,
        )
        second = engine.wait(
            engine.submit(
                JobRequest(
                    recipe="storyreel",
                    inputs={"tmdb_id": 42, "mode": "build", "language": "hi", "target_ms": 30_000, "story": story("beta")},
                ),
                caller="local",
            ).id,
            caller="local",
            timeout_s=600,
        )
    finally:
        engine.close()

    assert first.state is JobState.SUCCEEDED, first.error
    assert second.state is JobState.SUCCEEDED, second.error
    for finished, marker in ((first, "alpha"), (second, "beta")):
        rendered = json.loads(_artifact(finished, "story.json").read_text(encoding="utf-8"))
        assert rendered["story"]["sections"][0]["voiceover"].startswith(marker)

    cached = {record["id"]: record["cached"] for record in _manifest(second)["stages"]}
    assert cached["script"] is False  # the changed request invalidated it
    assert cached["subtitles"] is True  # the unchanged mechanical work is still reused
    assert cached["transcript"] is True


def test_join_many_batches_a_long_narration(tmp_path) -> None:
    """150 sections would be 150 ffmpeg inputs in one command; batching keeps it small."""
    from reelmachine.recipes.storyreel.stages import _join_many

    settings = get_settings()
    paths = []
    for index in range(25):
        part = tmp_path / f"part-{index:02d}.wav"
        ffmpeg.run(
            [
                settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "0.2",
                "-c:a", "pcm_s16le", str(part),
            ]
        )
        paths.append(part)
    dest = tmp_path / "narration.wav"
    _join_many(paths, dest, cancel=None, batch=10)
    assert 4.5 <= ffmpeg.probe(dest).duration_s <= 5.5


def test_embedded_subtitles_are_extracted_from_the_film(tmp_path) -> None:
    """The `embedded` provider reads the film's own subtitle stream, keyless."""
    settings = get_settings()
    srt = tmp_path / "source.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nA line from inside the film.\n",
        encoding="utf-8",
    )
    mkv = tmp_path / "with-subs.mkv"
    ffmpeg.run(
        [
            settings.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24:duration=5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=5",
            "-i",
            str(srt),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "2:s",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "35",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-c:s",
            "srt",
            "-metadata:s:s:0",
            "language=eng",
            "-shortest",
            str(mkv),
        ]
    )
    info = ffmpeg.probe(mkv)
    assert [track.language for track in info.subtitle_tracks] == ["eng"]
    assert info.text_subtitles()

    provider = EmbeddedSubtitleProvider()
    fetch = provider.fetch(
        SubtitleRequest(tmdb_id=1, languages=["en"], movie_path=str(mkv)),
        dest_dir=tmp_path / "out",
    )
    assert fetch is not None
    assert fetch.source == "embedded"
    assert "A line from inside the film." in Path(fetch.path).read_text(encoding="utf-8")
    # and a film without subtitle streams finds nothing rather than failing
    assert provider.fetch(
        SubtitleRequest(tmdb_id=1, languages=["en"], movie_path=str(tmp_path / "nope.mp4")),
        dest_dir=tmp_path / "out",
    ) is None
