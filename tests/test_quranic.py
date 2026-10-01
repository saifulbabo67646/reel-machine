"""The quranic recipe: no source media, no Nadeshiko, no alignment.

Fast tests cover the timings fallback, both corpora's parsers and the recipe's probe;
the slow test renders end to end with the fake corpus and asserts that neither Nadeshiko
nor any source provider was ever constructed.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import httpx
import pytest

from reelmachine import ffmpeg
from reelmachine.config import get_settings
from reelmachine.core.job import JobRequest, JobState
from reelmachine.core.registry import Registry
from reelmachine.engine import Engine
from reelmachine.recipes.quranic.corpora.alquran_cloud import AlQuranCloudCorpus, merge_translation, parse_surah
from reelmachine.recipes.quranic.corpora.fake import FakeQuranCorpus
from reelmachine.recipes.quranic.corpora.quran_com import (
    QuranComCorpus,
    parse_recitation,
    parse_verses,
    word_spans_from_segments,
)
from reelmachine.recipes.quranic.models import QuranicInputs
from reelmachine.recipes.quranic.timings import proportional_word_spans, timings_for_ayah

WORDS = ["بِسْمِ", "اللَّهِ", "الرَّحْمَٰنِ", "الرَّحِيمِ"]


# ----------------------------------------------------------------- timings


def test_proportional_spans_are_deterministic_and_cover_the_window() -> None:
    first = proportional_word_spans(WORDS, 1000, 3000)
    second = proportional_word_spans(WORDS, 1000, 3000)
    assert first == second
    assert first[0].start_ms == 1000
    assert first[-1].end_ms == 3000
    for earlier, later in zip(first, first[1:]):
        assert earlier.end_ms <= later.start_ms + 1
    # a long word gets a longer share than a short one
    short, long = proportional_word_spans(["ما", "الرحمن"], 0, 1000)
    assert (long.end_ms - long.start_ms) > (short.end_ms - short.start_ms)


def test_provider_timings_win_and_partial_ones_are_ignored() -> None:
    from reelmachine.core.timeline import WordSpan
    from reelmachine.recipes.quranic.models import AyahMaterial

    ayah = AyahMaterial(
        surah=1,
        ayah=1,
        text=" ".join(WORDS),
        words=WORDS,
        word_timings=[WordSpan(text=w, start_ms=index * 100, end_ms=index * 100 + 90) for index, w in enumerate(WORDS)],
    )
    spans, used = timings_for_ayah(ayah, 5000, 6000)
    assert used is True
    assert spans[0].start_ms == 5000 and spans[-1].end_ms == 5390

    partial = ayah.model_copy(update={"word_timings": ayah.word_timings[:2]})
    spans, used = timings_for_ayah(partial, 5000, 6000)
    assert used is False
    assert spans[-1].end_ms == 6000


# ----------------------------------------------------------------- parsers


def test_parse_verses_reads_words_and_translation() -> None:
    payload = {
        "verses": [
            {
                "verse_key": "1:1",
                "text_uthmani": " ".join(WORDS),
                "words": [{"text_uthmani": word} for word in WORDS],
                "translations": [{"text": "<p>In the name of Allah</p>"}],
            },
            {
                "verse_key": "1:2",
                "text_uthmani": "الْحَمْدُ لِلَّهِ",
                "words": [{"text_uthmani": "الْحَمْدُ"}, {"text_uthmani": "لِلَّهِ"}],
                "translations": [{"text": "All praise"}],
            },
        ]
    }
    ayahs = parse_verses(payload, surah=1, start=1, end=1)
    assert len(ayahs) == 1
    assert ayahs[0].words == WORDS
    assert ayahs[0].translation == "In the name of Allah"


def test_parse_recitation_and_segment_mapping() -> None:
    payload = {
        "audio_files": [
            {
                "verse_key": "1:1",
                "url": "//mirrors.example/001001.mp3",
                "segments": [[1, 0, 400], [2, 400, 900], [3, 900, 1400], [4, 1400, 2000]],
            }
        ]
    }
    parsed = parse_recitation(payload)
    assert parsed["1:1"]["url"].startswith("https://")
    spans = word_spans_from_segments(WORDS, parsed["1:1"]["segments"])
    assert [span.end_ms for span in spans] == [400, 900, 1400, 2000]
    # a partial mapping would highlight the wrong word and is refused
    assert word_spans_from_segments(WORDS, parsed["1:1"]["segments"][:2]) == []


def test_quran_com_fetch_over_a_stubbed_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/resources/recitations"):
            return httpx.Response(200, json={"recitations": [{"id": 7, "reciter_name": "Mishary Rashid Alafasy"}]})
        if request.url.path.endswith("/resources/translations"):
            return httpx.Response(200, json={"translations": [{"id": 131, "name": "Sahih International"}]})
        if "/verses/by_chapter/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "verses": [
                        {
                            "verse_key": "1:1",
                            "text_uthmani": " ".join(WORDS),
                            "words": [{"text_uthmani": word} for word in WORDS],
                            "translations": [{"text": "In the name of Allah"}],
                        }
                    ]
                },
            )
        if "/recitations/7/by_chapter/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "audio_files": [
                        {
                            "verse_key": "1:1",
                            "url": "https://cdn.example/1_1.mp3",
                            "segments": [[index + 1, index * 500, index * 500 + 480] for index in range(len(WORDS))],
                        }
                    ]
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    corpus = QuranComCorpus(client=client, base_url="https://api.example.test/api/v4")
    selection = corpus.fetch(
        QuranicInputs(surah=1, ayah_start=1, ayah_end=1, reciter="alafasy", translation="en.sahih")
    )
    assert selection.reciter_name == "Mishary Rashid Alafasy"
    assert selection.ayahs[0].audio_url.endswith("1_1.mp3")
    assert len(selection.ayahs[0].word_timings) == len(WORDS)
    assert selection.timings_source == "provider"


def test_alquran_cloud_fetch_over_a_stubbed_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/ar.alafasy" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "number": 1,
                        "name": "سورة الفاتحة",
                        "ayahs": [
                            {"numberInSurah": 1, "text": " ".join(WORDS), "audio": "https://cdn.example/1.mp3"},
                            {"numberInSurah": 2, "text": "الْحَمْدُ لِلَّهِ", "audio": "https://cdn.example/2.mp3"},
                        ],
                    }
                },
            )
        if "/en.sahih" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "name": "Sahih International",
                        "ayahs": [
                            {"numberInSurah": 1, "text": "In the name of Allah"},
                            {"numberInSurah": 2, "text": "All praise"},
                        ],
                    }
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    corpus = AlQuranCloudCorpus(client=client, base_url="https://api.example.test/v1")
    selection = corpus.fetch(
        QuranicInputs(surah=1, ayah_start=1, ayah_end=2, corpus="alquran_cloud", reciter="alafasy")
    )
    assert [ayah.ayah for ayah in selection.ayahs] == [1, 2]
    assert selection.ayahs[1].translation == "All praise"
    assert selection.timings_source == "proportional"
    assert merge_translation([], {"data": {}}) == ""


def test_parse_surah_filters_the_range() -> None:
    payload = {
        "data": {
            "number": 1,
            "ayahs": [{"numberInSurah": number, "text": f"آية {number}", "audio": ""} for number in range(1, 8)],
        }
    }
    ayahs = parse_surah(payload, start=3, end=5)
    assert [ayah.ayah for ayah in ayahs] == [3, 4, 5]


# ------------------------------------------------------------------ recipe


def test_recipe_is_discovered_and_probe_is_actionable() -> None:
    from reelmachine.recipes.quranic.recipe import QuranicRecipe

    registry = Registry()
    assert "quranic" in registry.names("recipes")
    assert "quran-fake" in registry.names("corpora")

    recipe = QuranicRecipe()
    assert recipe.spec.render_modes == ("quranic",)
    assert [spec.stage.id for spec in recipe.spec.stages] == ["select", "prep", "compose", "render"]

    settings = get_settings()
    from reelmachine.core.recipe import ProbeContext
    from reelmachine.core.stage import StaticProviders

    report = recipe.probe(
        QuranicInputs(surah=1, ayah_start=1, ayah_end=3),
        ProbeContext(config=settings, providers=StaticProviders({"corpus": FakeQuranCorpus(settings)})),
    )
    assert report.ok and report.quota_free
    assert report.details["ayahs"] == [1, 3]

    normalised = recipe.probe(
        QuranicInputs(surah=1, ayah_start=5, ayah_end=2),
        ProbeContext(config=settings, providers=StaticProviders({"corpus": FakeQuranCorpus(settings)})),
    )
    assert normalised.details["ayahs"] == [2, 5]  # a reversed range is normalised, not rejected

    bad = recipe.probe(
        QuranicInputs(surah=1, ayah_start=1, ayah_end=300),
        ProbeContext(config=settings, providers=StaticProviders({"corpus": FakeQuranCorpus(settings)})),
    )
    assert bad.ok is False
    assert any(check.name == "ayah_range" and not check.ok for check in bad.checks)


def test_fake_corpus_audio_matches_its_timings(tmp_path) -> None:
    corpus = FakeQuranCorpus()
    selection = corpus.fetch(QuranicInputs(surah=1, ayah_start=1, ayah_end=1))
    ayah = selection.ayahs[0]
    path = corpus.fetch_audio(ayah.audio_url, tmp_path / "1_1.wav")
    duration_ms = int(ffmpeg.probe(path).duration_s * 1000)
    last_word_end = ayah.word_timings[-1].end_ms
    assert abs(duration_ms - (last_word_end + 80)) <= 120  # one trailing gap, ± one block


# ------------------------------------------------------------------ end to end


class RecordingRegistry(Registry):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.loads: list[tuple[str, str]] = []

    def load(self, group: str, name: str):
        self.loads.append((group, name))
        return super().load(group, name)


@pytest.mark.slow
def test_quranic_renders_with_no_nadeshiko_key_and_no_source_media(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NADESHIKO_API_KEY", "")
    monkeypatch.setenv("REEL_LOCAL_ROOT", "")
    monkeypatch.setenv("REEL_SOURCE", "local")

    import reelmachine.nadeshiko as nadeshiko

    def forbidden(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the quranic recipe must not touch Nadeshiko")

    monkeypatch.setattr(nadeshiko.NadeshikoClient, "__init__", forbidden)

    settings = dataclasses.replace(
        get_settings(), workdir=tmp_path / "work", outdir=tmp_path / "out"
    )
    registry = RecordingRegistry()
    engine = Engine(
        settings,
        registry=registry,
        provider_overrides={"corpus": "quran-fake"},
    )
    try:
        job = engine.submit(
            JobRequest(
                recipe="quranic",
                inputs={
                    "surah": 1,
                    "ayah_start": 1,
                    "ayah_end": 3,
                    "corpus": "quran-fake",
                    "style": "quranic.parchment",
                    "name": "quran-reel",
                },
            ),
            caller="local",
        )
        finished = engine.wait(job.id, caller="local", timeout_s=300)
    finally:
        engine.close()

    assert finished.state is JobState.SUCCEEDED, finished.error
    groups = {group for group, _ in registry.loads}
    assert ("corpora", "quran-fake") in registry.loads
    assert "sources" not in groups, "the quranic recipe resolved a source provider"

    names = {artifact.name for artifact in finished.result.artifacts}
    assert {"reel.mp4", "captions.ass", "captions.srt", "timeline.json", "manifest.json"} <= names

    manifest = json.loads(Path(finished.result.manifest).read_text(encoding="utf-8"))
    assert manifest["render_mode"] == "quranic"
    assert manifest["verification"]["ok"] is True
    checks = {check["name"]: check for check in manifest["verification"]["checks"]}
    assert checks["word_highlight"]["ok"] is True
    assert checks["ayahs"]["ok"] is True

    video = Path(finished.result.artifacts[0].path)
    assert video.is_file()
    info = ffmpeg.probe(video)
    assert info.has_video and info.has_audio
    assert abs(info.duration_s * 1000 - manifest["timeline"]["duration_ms"]) <= 1200

    ass = Path(next(a.path for a in finished.result.artifacts if a.name == "captions.ass"))
    text = ass.read_text(encoding="utf-8")
    assert "\\k" in text, "word-level highlighting must reach the ASS"
    assert "Translation" in text
