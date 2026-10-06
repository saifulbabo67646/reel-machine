"""The nadeshiko-cut recipe end to end, offline: fake corpus + mock source.

Anime and J-Drama are one recipe with a corpus parameter, so the acceptance here is both
that the parameter reaches the same stage code, and that the staged pipeline produces a
real reel with no network.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from reelmachine.core.assets import AssetStore
from reelmachine.core.job import Job, JobRequest
from reelmachine.core.stage import StageContext, StaticProviders
from reelmachine.config import get_settings
from reelmachine.models import Media, Page, SearchPage, TextJa, Token, Translation
from reelmachine.recipes.nadeshiko_cut import NadeshikoCutInputs, run_nadeshiko_cut
from reelmachine.recipes.nadeshiko_cut.stages import SelectStage
from reelmachine.sources.mock import MockProvider

pytestmark = pytest.mark.slow

WORD = "彼女"
MEDIA_ID = "MOCKMEDIA001"


class FakeCorpus:
    """A Nadeshiko-shaped corpus with no network."""

    def __init__(self, segments=(), media=()):
        self.segments = list(segments)
        self.media = {item.publicId: item for item in media}
        self.filters: list[dict | None] = []

    def search(self, query=None, **kwargs) -> SearchPage:
        self.filters.append(kwargs.get("filters"))
        return SearchPage(segments=list(self.segments), pagination=Page(), media=dict(self.media))

    def segment_context(self, segment_id, **kwargs):
        return []


class FakeSource:
    name = "fake-source"


def _mock_setup(tmp_path: Path):
    settings = replace(
        get_settings(),
        workdir=tmp_path / "work",
        outdir=tmp_path / "out",
        mock_dir=tmp_path / "mock",
    )
    provider = MockProvider(settings)
    provider.duration_s = 40
    asset = provider.resolve(None, 1)
    provider.probe(asset)
    segments = provider.make_segments(MEDIA_ID, 1, count=2)
    for segment in segments:
        segment.textJa = TextJa(
            content=f"{WORD}が好き",
            highlight=f"<em>{WORD}</em>",
            tokens=[
                Token(s=WORD, b=0, e=2, r="かのじょ"),
                Token(s="が", b=2, e=3, r="が"),
                Token(s="好き", b=3, e=5, d="好き", r="すき"),
            ],
        )
        segment.textEn = Translation(content="I like her")
    media = Media(publicId=MEDIA_ID, slug="mock-show", nameEn="Mock Show", category="ANIME")
    return settings, provider, segments, media


def test_corpus_parameter_reaches_the_same_stage_code(tmp_path) -> None:
    """anime and jdrama are the same stage; only the filter differs."""
    settings = replace(get_settings(), workdir=tmp_path / "w", outdir=tmp_path / "o")
    corpus = FakeCorpus()
    ctx = StageContext(
        job=Job(id="j1", recipe="nadeshiko-cut", request=JobRequest(recipe="nadeshiko-cut")),
        workdir=tmp_path,
        assets=AssetStore(tmp_path / "assets"),
        config=settings,
        providers=StaticProviders({"corpus": corpus, "source": FakeSource()}),
    )
    stage = SelectStage()
    stage.run(ctx, {"word": "約束", "corpus": "anime"})
    stage.run(ctx, {"word": "約束", "corpus": "jdrama"})
    stage.run(ctx, {"word": "約束", "corpus": "anime+jdrama"})

    assert corpus.filters == [
        {"category": ["ANIME"]},
        {"category": ["JDRAMA"]},
        {"category": ["ANIME", "JDRAMA"]},
    ]


def test_recipe_id_does_not_change_with_the_corpus() -> None:
    from reelmachine.recipes.nadeshiko_cut.recipe import NadeshikoCutRecipe

    recipe = NadeshikoCutRecipe()
    assert recipe.spec.id == "nadeshiko-cut"
    assert [spec.stage.id for spec in recipe.spec.stages] == [
        "select",
        "acquire",
        "compose",
        "prep",
        "render",
    ]
    from reelmachine.recipes.nadeshiko_cut.models import parse_corpus

    assert parse_corpus("anime") == ["ANIME"]
    assert parse_corpus("jdrama") == ["JDRAMA"]
    assert parse_corpus("drama") == ["JDRAMA"]
    assert parse_corpus("youtube") == ["YOUTUBE"]
    assert parse_corpus("anime+jdrama") == ["ANIME", "JDRAMA"]


def test_plan_mode_stops_after_compose(tmp_path) -> None:
    settings, provider, segments, media = _mock_setup(tmp_path)
    corpus = FakeCorpus(segments, [media])
    inputs = NadeshikoCutInputs(word=WORD, mode="plan", name="plan-only", per_media=2)
    runs, outputs = run_nadeshiko_cut(
        inputs, client=corpus, provider=provider, settings=settings
    )
    assert [run.id for run in runs] == ["select", "acquire", "compose"]
    plan = outputs["compose"].plan
    assert plan["word"] == WORD
    assert plan["stats"]["ok"] == 2
    assert "render" not in outputs


def test_full_recipe_renders_a_reel_with_no_network(tmp_path) -> None:
    settings, provider, segments, media = _mock_setup(tmp_path)
    corpus = FakeCorpus(segments, [media])
    inputs = NadeshikoCutInputs(word=WORD, mode="build", name="recipe-reel", count=2, per_media=2)
    runs, outputs = run_nadeshiko_cut(
        inputs, client=corpus, provider=provider, settings=settings
    )

    assert [run.id for run in runs] == ["select", "acquire", "compose", "prep", "render"]
    assert all(not run.cached for run in runs)

    compose = outputs["compose"]
    assert compose.plan["stats"]["ok"] == 2
    assert compose.timeline.render_mode == "cut"
    assert compose.timeline.unprepared  # declarative before prep

    prepared = outputs["prep"].timeline
    assert prepared.prepared
    assert prepared.duration_ms > 0
    assert len(outputs["prep"].clips) == 2

    rendered = outputs["render"]
    assert rendered.render_mode == "cut"
    video = Path(rendered.video)
    assert video.is_file() and video.stat().st_size > 0
    assert Path(rendered.ass).is_file() and Path(rendered.srt).is_file()

    manifest = json.loads(Path(rendered.manifest).read_text(encoding="utf-8"))
    assert manifest["word"] == WORD
    assert manifest["source"] == "mock"
    assert len([s for s in manifest["segments"] if s["status"] == "rendered"]) == 2


def test_recipe_runs_through_the_engine_api(tmp_path, monkeypatch) -> None:
    """The CLI's path: submit + wait, with the shipped fake corpus and the mock source."""
    from reelmachine.core.job import JobRequest, JobState
    from reelmachine.engine import Engine
    from reelmachine.jobs.local import LocalJobStore

    monkeypatch.setenv("REEL_MOCK_DURATION", "40")
    settings = replace(
        get_settings(),
        workdir=tmp_path / "work",
        outdir=tmp_path / "out",
        mock_dir=tmp_path / "mock",
    )
    engine = Engine(
        settings,
        provider_overrides={"corpus": "nadeshiko-fake", "source": "mock"},
        store=LocalJobStore(tmp_path / "jobs"),
    )
    try:
        job = engine.submit(
            JobRequest(
                recipe="nadeshiko-cut",
                inputs={
                    "word": WORD,
                    "mode": "build",
                    "name": "engine-reel",
                    "count": 2,
                    "per_media": 2,
                },
            ),
            caller="alice",
        )
        finished = engine.wait(job.id, caller="alice", timeout_s=300)
    finally:
        engine.close()

    assert finished.state is JobState.SUCCEEDED, finished.error
    names = {artifact.name for artifact in finished.result.artifacts}
    assert {
        "reel.mp4",
        "captions.ass",
        "captions.srt",
        "reel.json",
        "plan.json",
        "timeline.json",
        "manifest.json",
    } <= names

    manifest = json.loads(Path(finished.result.manifest).read_text(encoding="utf-8"))
    assert manifest["recipe"] == "nadeshiko-cut"
    assert manifest["caller"] == "alice"
    assert manifest["render_mode"] == "cut"
    assert manifest["timeline"]["digest"]
    assert manifest["stages"] and any(stage["id"] == "render" for stage in manifest["stages"])
    assert manifest["assets"], "the reel's clips must be recorded as assets"
    assert all(record["asset"]["provenance"]["provider"] for record in manifest["assets"])


def test_repeated_select_is_served_from_the_stage_cache(tmp_path) -> None:
    """Quota is spent once: the same selection is not searched again."""
    settings, provider, segments, media = _mock_setup(tmp_path)
    corpus = FakeCorpus(segments, [media])
    inputs = NadeshikoCutInputs(word=WORD, mode="plan", name="cached")

    _, first = run_nadeshiko_cut(inputs, client=corpus, provider=provider, settings=settings)
    runs, _ = run_nadeshiko_cut(inputs, client=corpus, provider=provider, settings=settings)

    select_runs = [run for run in runs if run.id == "select"]
    assert select_runs and select_runs[0].cached
    assert len(corpus.filters) == 1  # the search ran exactly once
