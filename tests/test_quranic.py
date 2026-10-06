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
from reelmachine.core.errors import InvalidInput, ProviderUnavailable
from reelmachine.recipes.quranic.backgrounds import BackgroundRequest
from reelmachine.recipes.quranic.backgrounds.pexels import PexelsBackgroundProvider
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
    # the live API serves bare relative paths; those get the audio CDN base
    bare = parse_recitation({"audio_files": [{"verse_key": "1:1", "url": "Alafasy/mp3/001001.mp3"}]})
    assert bare["1:1"]["url"] == "https://verses.quran.com/Alafasy/mp3/001001.mp3"
    custom = parse_recitation(
        {"audio_files": [{"verse_key": "1:1", "url": "Alafasy/mp3/001001.mp3"}]},
        audio_base="https://audio.example.test/",
    )
    assert custom["1:1"]["url"] == "https://audio.example.test/Alafasy/mp3/001001.mp3"
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


def test_word_text_never_uses_font_glyph_codes() -> None:
    """Without `word_fields=text_uthmani` the API returns U+FB50 glyph codes."""
    from reelmachine.recipes.quranic.corpora.quran_com import strip_html, word_text

    glyph = {"text": "\ufb51", "code_v1": "\ufb51", "text_uthmani": None}
    assert word_text(glyph) == ""  # never render an unjoinable glyph code
    assert word_text({"text_uthmani": "بِسْمِ", "text": "\ufb51"}) == "بِسْمِ"
    assert word_text({"text": "بِسْمِ"}) == "بِسْمِ"

    assert strip_html("<p>Allāh,<sup>1</sup> the Merciful<sup>footnote</sup>.</p>") == "Allāh, the Merciful."

    payload = {
        "verses": [
            {
                "verse_key": "1:1",
                "text_uthmani": "بِسْمِ ٱللَّهِ",
                "words": [
                    {"text_uthmani": "بِسْمِ", "text": "\ufb51", "char_type_name": "word"},
                    {"text_uthmani": "ٱللَّهِ", "text": "\ufb52", "char_type_name": "word"},
                    {"text_uthmani": "١", "char_type_name": "end"},  # the ayah marker
                ],
                "translations": [{"text": "In the name of Allāh,<sup>1</sup>"}],
            }
        ]
    }
    ayahs = parse_verses(payload, surah=1, start=1, end=1)
    assert ayahs[0].words == ["بِسْمِ", "ٱللَّهِ"]
    assert ayahs[0].translation == "In the name of Allāh,"


def test_quran_com_resolves_the_apis_own_spellings() -> None:
    """The live API says "Mishari Rashid al-`Afasy" and "Saheeh International"."""
    from reelmachine.core.errors import InvalidInput
    from reelmachine.recipes.quranic.corpora.quran_com import name_matches, normalise_name

    assert normalise_name("Mishari Rashid al-`Afasy") == "misharirashidalafasy"
    assert name_matches("alafasy", "Mishari Rashid al-`Afasy")
    assert name_matches("shuraim", "saudashshuraym") is False  # aliased, not substring

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/resources/recitations"):
            return httpx.Response(
                200,
                json={
                    "recitations": [
                        {"id": 7, "reciter_name": "Mishari Rashid al-`Afasy",
                         "translated_name": {"name": "Mishari Rashid al-`Afasy"}},
                        {"id": 10, "reciter_name": "Sa`ud ash-Shuraym",
                         "translated_name": {"name": "Sa`ud ash-Shuraym"}},
                    ]
                },
            )
        if request.url.path.endswith("/resources/translations"):
            return httpx.Response(
                200,
                json={
                    "translations": [
                        {"id": 20, "name": "Saheeh International"},
                        {"id": 19, "name": "M. Pickthall"},
                    ]
                },
            )
        return httpx.Response(404)

    corpus = QuranComCorpus(client=httpx.Client(transport=httpx.MockTransport(handler)),
                            base_url="https://api.example.test/api/v4")
    assert corpus.resolve_reciter("alafasy") == ("7", "Mishari Rashid al-`Afasy")
    assert corpus.resolve_reciter("alafasi")[0] == "7"      # common misspelling
    assert corpus.resolve_reciter("shuraim")[0] == "10"     # alias → API's "shuraym"
    assert corpus.resolve_translation("en.sahih") == ("20", "Saheeh International")
    assert corpus.resolve_translation("en.pickthall")[0] == "19"
    assert corpus.resolve_translation("Saheeh International")[0] == "20"
    with pytest.raises(InvalidInput):
        corpus.resolve_reciter("nobody-in-the-listing")
    with pytest.raises(InvalidInput):
        corpus.resolve_translation("en.nope")


def test_quranic_probe_catches_an_unresolvable_reciter() -> None:
    """probe must fail on a bad reciter, not the job halfway through (found live)."""
    from reelmachine.core.recipe import ProbeContext
    from reelmachine.core.stage import StaticProviders
    from reelmachine.recipes.quranic.recipe import QuranicRecipe

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/resources/recitations"):
            return httpx.Response(200, json={"recitations": [
                {"id": 7, "reciter_name": "Mishari Rashid al-`Afasy"},
            ]})
        if request.url.path.endswith("/resources/translations"):
            return httpx.Response(200, json={"translations": [{"id": 20, "name": "Saheeh International"}]})
        if request.url.path.endswith("/verses/by_chapter/1"):
            return httpx.Response(
                200,
                json={
                    "verses": [
                        {
                            "verse_key": "1:1",
                            "text_uthmani": "بِسْمِ ٱللَّهِ",
                            "words": [
                                {"text_uthmani": "بِسْمِ", "position": 1},
                                {"text_uthmani": "ٱللَّهِ", "position": 2},
                            ],
                            "translations": [{"resource_id": 20, "text": "In the name of Allah"}],
                        }
                    ]
                },
            )
        if "/recitations/7/by_chapter/1" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "audio_files": [
                        {"verse_key": "1:1", "url": "Alafasy/mp3/001001.mp3"},
                    ]
                },
            )
        return httpx.Response(404)

    corpus = QuranComCorpus(client=httpx.Client(transport=httpx.MockTransport(handler)),
                            base_url="https://api.example.test/api/v4")
    settings = get_settings()
    from reelmachine.recipes.quranic.backgrounds.gradient import GradientBackgroundProvider

    ctx = ProbeContext(
        config=settings,
        providers=StaticProviders(
            {"corpus": corpus, "background_gradient": GradientBackgroundProvider(settings)}
        ),
    )

    report = QuranicRecipe().probe(QuranicInputs(surah=1, ayah_start=1, ayah_end=1), ctx)
    assert report.ok is True
    assert any(check.name == "reciter" and check.ok for check in report.checks)
    # the probe hands the caller the verses, so backgrounds can be chosen by meaning
    verse = report.details["verses"][0]
    assert verse["text"] and verse["words"] == ["بِسْمِ", "ٱللَّهِ"]
    assert verse["translation"] == "In the name of Allah"

    bad = QuranicRecipe().probe(
        QuranicInputs(surah=1, ayah_start=1, ayah_end=1, reciter="nobody"), ctx
    )
    assert bad.ok is False
    assert any(check.name == "reciter" and not check.ok for check in bad.checks)


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


def test_recipe_is_discovered_and_probe_is_actionable(tmp_path) -> None:
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
    from reelmachine.recipes.quranic.backgrounds.gradient import GradientBackgroundProvider
    from reelmachine.recipes.quranic.backgrounds.pexels import PexelsBackgroundProvider

    providers = StaticProviders(
        {
            "corpus": FakeQuranCorpus(settings),
            "background_gradient": GradientBackgroundProvider(settings),
            "background_pexels": PexelsBackgroundProvider(settings, api_key="", cache_dir=tmp_path),
        }
    )
    report = recipe.probe(
        QuranicInputs(surah=1, ayah_start=1, ayah_end=3),
        ProbeContext(config=settings, providers=providers),
    )
    assert report.ok and report.quota_free
    assert report.details["ayahs"] == [1, 3]

    # a stock background without the deployment's key fails the probe, with the reason
    pexels = recipe.probe(
        QuranicInputs(
            surah=1,
            ayah_start=1,
            ayah_end=1,
            background={"kind": "pexels", "query": "clouds"},
        ),
        ProbeContext(config=settings, providers=providers),
    )
    assert pexels.ok is False
    assert any(check.name == "background" and "PEXELS_API_KEY" in check.detail for check in pexels.checks)

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


def test_pexels_picks_a_portrait_rendition_and_records_its_licence(tmp_path) -> None:
    payload = {
        "videos": [
            {
                "id": 1, "width": 1920, "height": 1080, "duration": 9,
                "url": "https://www.pexels.com/video/1", "user": {"name": "Landscape Person"},
                "video_files": [
                    {"file_type": "video/mp4", "height": 1080, "width": 1920, "link": "https://cdn.test/wide.mp4"}
                ],
            },
            {
                "id": 2, "width": 1080, "height": 1920, "duration": 6,
                "url": "https://www.pexels.com/video/2", "user": {"name": "Portrait Person"},
                "video_files": [
                    {"file_type": "video/mp4", "height": 360, "width": 202, "link": "https://cdn.test/tiny.mp4"},
                    {"file_type": "video/webm", "height": 1920, "width": 1080, "link": "https://cdn.test/big.webm"},
                    {"file_type": "video/mp4", "height": 1080, "width": 608, "link": "https://cdn.test/portrait.mp4"},
                ],
            },
        ]
    }
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/videos/search"):
            assert request.headers["Authorization"] == "test-key"
            assert request.url.params["orientation"] == "portrait"
            return httpx.Response(200, json=payload)
        return httpx.Response(200, content=b"video-bytes")

    provider = PexelsBackgroundProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        api_key="test-key",
        base_url="https://api.test",
        cache_dir=tmp_path / "cache",
    )
    clip = provider.fetch(
        BackgroundRequest(kind="pexels", query="clouds", orientation="portrait", min_height=720),
        dest_dir=tmp_path,
    )
    assert clip.path.read_bytes() == b"video-bytes"
    assert clip.path.name == "pexels-2.mp4"  # cached by Pexels' own id
    assert clip.width == 608 and clip.duration_ms == 6000
    assert clip.licence is not None and clip.licence.name == "Pexels licence"
    assert "Portrait Person" in clip.licence.attribution
    assert clip.provenance.source_id == "2" and clip.provenance.provider == "pexels"

    # a second fetch of the same clip must not re-download it
    provider.fetch(BackgroundRequest(kind="pexels", query="clouds"), dest_dir=tmp_path)
    assert seen.count("/videos/search") == 2
    assert all(not path.endswith("portrait.mp4") for path in seen[2:])


def test_pexels_keeps_people_out_of_a_verse_reel(tmp_path) -> None:
    """The shot behind a verse must not be an unknown person in western dress."""
    from reelmachine.recipes.quranic.backgrounds.pexels import people_word

    assert people_word({"url": "https://www.pexels.com/video/a-body-of-water-9/"}) == ""
    assert people_word({"alt": "Green field under a blue sky", "url": ""}) == ""
    assert people_word({"url": "https://www.pexels.com/video/woman-in-red-dress-1/"}) == "woman"
    assert people_word({"alt": "Woman with arms raised", "url": ""}) == "woman"

    person = {
        "id": 1, "width": 1080, "height": 1920, "duration": 8,
        "url": "https://www.pexels.com/video/woman-in-a-red-dress-111/",
        "user": {"name": "Someone"},
        "video_files": [{"file_type": "video/mp4", "height": 1080, "width": 608, "link": "https://cdn.test/person.mp4"}],
    }
    clouds = {
        "id": 2, "width": 1080, "height": 1920, "duration": 6,
        "url": "https://www.pexels.com/video/dramatic-clouds-222/",
        "user": {"name": "Someone Else"},
        "video_files": [{"file_type": "video/mp4", "height": 1080, "width": 608, "link": "https://cdn.test/clouds.mp4"}],
    }

    def make_handler(videos, photos=None):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/videos/search":
                return httpx.Response(200, json={"videos": videos})
            if request.url.path == "/v1/search":
                return httpx.Response(200, json={"photos": photos or []})
            return httpx.Response(200, content=b"media-bytes")

        return handler

    def provider_for(videos, photos=None):
        return PexelsBackgroundProvider(
            client=httpx.Client(transport=httpx.MockTransport(make_handler(videos, photos))),
            api_key="test-key",
            base_url="https://api.test",
            cache_dir=tmp_path / "cache",
        )

    clip = provider_for([person, clouds]).fetch(
        BackgroundRequest(kind="pexels", query="sky", media="video"), dest_dir=tmp_path
    )
    assert clip.path.name == "pexels-2.mp4", "the person should have been skipped"

    # nothing but people: say so, and name the word that caused it
    with pytest.raises(InvalidInput) as blocked:
        provider_for([person]).fetch(
            BackgroundRequest(kind="pexels", query="arms raised", media="video"), dest_dir=tmp_path
        )
    assert "showed a person" in str(blocked.value)
    assert "allow_people" in (blocked.value.hint or "")
    assert blocked.value.details["skipped"][0]["reason"] == "woman"

    # an explicit opt-in still gets it
    opted_in = provider_for([person]).fetch(
        BackgroundRequest(kind="pexels", query="sky", media="video", allow_people=True),
        dest_dir=tmp_path,
    )
    assert opted_in.path.name == "pexels-1.mp4"

    # media=auto: no usable clip, so the still is used instead
    photo = {
        "id": 5, "width": 1080, "height": 1920,
        "url": "https://www.pexels.com/photo/clouds-5/", "photographer": "Someone",
        "src": {"large2x": "https://cdn.test/clouds.jpg"},
    }
    still = provider_for([person], [photo]).fetch(
        BackgroundRequest(kind="pexels", query="clouds", media="auto"), dest_dir=tmp_path
    )
    assert still.still is True and still.path.name == "pexels-photo-5.jpg"


def test_pexels_tries_the_next_candidate_when_a_face_is_found(tmp_path) -> None:
    """A slug can say "clouds" while the shot shows a person: the frame is the last gate."""
    good_one = {
        "id": 1, "width": 1080, "height": 1920, "duration": 7,
        "url": "https://www.pexels.com/video/clouds-over-a-valley-1/", "user": {"name": "A"},
        "video_files": [{"file_type": "video/mp4", "height": 1080, "width": 608, "link": "https://cdn.test/one.mp4"}],
    }
    good_two = {**good_one, "id": 2, "url": "https://www.pexels.com/video/misty-hills-2/",
                "video_files": [{"file_type": "video/mp4", "height": 1080, "width": 608, "link": "https://cdn.test/two.mp4"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/videos/search":
            return httpx.Response(200, json={"videos": [good_one, good_two]})
        return httpx.Response(200, content=b"media-bytes")

    def detector(path):
        return path.name == "pexels-1.mp4"  # the first clip has a face, the second does not

    provider = PexelsBackgroundProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        api_key="test-key",
        base_url="https://api.test",
        cache_dir=tmp_path / "cache",
        face_detector=detector,
    )
    clip = provider.fetch(
        BackgroundRequest(kind="pexels", query="clouds", media="video"), dest_dir=tmp_path
    )
    assert clip.path.name == "pexels-2.mp4", "the clip with a face should have been refused"
    assert clip.face_check == "passed"

    # every candidate has a face: refuse the query rather than ship a person
    provider._face_detector = lambda path: True
    with pytest.raises(InvalidInput) as blocked:
        provider.fetch(BackgroundRequest(kind="pexels", query="clouds", media="video"), dest_dir=tmp_path)
    assert "showed a person" in str(blocked.value)
    assert blocked.value.details["skipped"][0]["reason"] == "a face"


def test_pexels_moves_on_when_a_download_drops(tmp_path) -> None:
    """A stock CDN dropping a transfer is a reason to try the next match, not to fail."""
    videos = [
        {
            "id": 1, "width": 1080, "height": 1920, "duration": 7,
            "url": "https://www.pexels.com/video/clouds-1/", "user": {"name": "A"},
            "video_files": [{"file_type": "video/mp4", "height": 1080, "width": 608, "link": "https://cdn.test/one.mp4"}],
        },
        {
            "id": 2, "width": 1080, "height": 1920, "duration": 7,
            "url": "https://www.pexels.com/video/clouds-2/", "user": {"name": "B"},
            "video_files": [{"file_type": "video/mp4", "height": 1080, "width": 608, "link": "https://cdn.test/two.mp4"}],
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/videos/search":
            return httpx.Response(200, json={"videos": videos})
        if request.url.path == "/one.mp4":
            raise httpx.RemoteProtocolError("connection dropped mid-transfer")
        return httpx.Response(200, content=b"media-bytes")

    provider = PexelsBackgroundProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        api_key="test-key",
        base_url="https://api.test",
        cache_dir=tmp_path / "cache",
        face_detector=lambda path: False,
    )
    clip = provider.fetch(
        BackgroundRequest(kind="pexels", query="clouds", media="video"), dest_dir=tmp_path
    )
    assert clip.path.name == "pexels-2.mp4"
    assert not (tmp_path / "cache" / "pexels-1.mp4").exists(), "no half file may stay behind"

    # everything drops: say that, rather than blaming the query
    drops = PexelsBackgroundProvider(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"videos": videos})
                if request.url.path == "/videos/search"
                else (_ for _ in ()).throw(httpx.RemoteProtocolError("dropped"))
            )
        ),
        api_key="test-key",
        base_url="https://api.test",
        cache_dir=tmp_path / "cache2",
        face_detector=lambda path: False,
    )
    with pytest.raises(ProviderUnavailable) as failed:
        drops.fetch(BackgroundRequest(kind="pexels", query="clouds", media="video"), dest_dir=tmp_path)
    assert "failed to download" in str(failed.value)


def test_pexels_photos_and_a_rejected_key(tmp_path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/v1/search":  # photos live under /v1, videos do not
            return httpx.Response(
                200,
                json={
                    "photos": [
                        {
                            "id": 9, "width": 1080, "height": 1920,
                            "url": "https://www.pexels.com/photo/9", "photographer": "Someone",
                            "src": {"large2x": "https://cdn.test/p.jpg", "original": "https://cdn.test/o.jpg"},
                        }
                    ]
                },
            )
        if request.url.path == "/p.jpg":
            return httpx.Response(200, content=b"jpeg-bytes")
        return httpx.Response(401, json={"error": "unauthorized"})

    provider = PexelsBackgroundProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        api_key="test-key",
        base_url="https://api.test",
        cache_dir=tmp_path / "cache",
    )
    clip = provider.fetch(
        BackgroundRequest(kind="pexels", query="light through trees", media="photo"),
        dest_dir=tmp_path,
    )
    assert seen[0] == "/v1/search", seen
    assert clip.still is True and clip.path.read_bytes() == b"jpeg-bytes"
    assert clip.licence is not None and "Someone" in clip.licence.attribution

    with pytest.raises(ProviderUnavailable) as rejected:
        provider.fetch(BackgroundRequest(kind="pexels", query="clouds"), dest_dir=tmp_path)
    assert "PEXELS_API_KEY" in (rejected.value.hint or "")


def test_pexels_needs_a_key_and_a_match(tmp_path) -> None:
    provider = PexelsBackgroundProvider(
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"videos": []}))),
        api_key="",
        base_url="https://api.test",
        cache_dir=tmp_path / "cache",
    )
    assert provider.missing() == ["PEXELS_API_KEY is not set"]
    with pytest.raises(ProviderUnavailable) as missing:
        provider.fetch(BackgroundRequest(kind="pexels", query="clouds"), dest_dir=tmp_path)
    assert "PEXELS_API_KEY" in (missing.value.hint or "")

    keyed = PexelsBackgroundProvider(
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"videos": []}))),
        api_key="test-key",
        base_url="https://api.test",
        cache_dir=tmp_path / "cache",
    )
    with pytest.raises(InvalidInput) as nomatch:
        keyed.fetch(BackgroundRequest(kind="pexels", query="a very specific thing"), dest_dir=tmp_path)
    assert "a very specific thing" in str(nomatch.value)
    assert "no Pexels" in str(nomatch.value)


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


def test_background_slices_split_the_reel_without_gaps() -> None:
    from reelmachine.recipes.quranic.stages import background_slices

    windows = background_slices(46_100, 3)
    assert windows[0][0] == 0 and windows[-1][1] == 46_100
    assert all(
        windows[index][1] == windows[index + 1][0] for index in range(len(windows) - 1)
    ), windows
    assert all(end - start >= 15_000 for start, end in windows), windows  # evenly spread
    assert background_slices(1000, 1) == [(0, 1000)]


def test_background_scopes_must_tile_the_ayahs() -> None:
    from reelmachine.recipes.quranic.models import QuranicInputs
    from reelmachine.recipes.quranic.stages import scope_problem, scoped_slices

    def scoped(*pairs):
        return QuranicInputs(
            surah=1,
            ayah_start=1,
            ayah_end=5,
            backgrounds=[
                {"kind": "pexels", "query": "x", "ayah_start": start, "ayah_end": end}
                for start, end in pairs
            ],
        ).backgrounds

    assert scope_problem(scoped((1, 2), (3, 5)), (1, 5)) == ""
    assert scope_problem(scoped((3, 5), (1, 2)), (1, 5)) == "backgrounds must follow the ayah order"
    assert "no background for ayah(s) [4, 5]" in scope_problem(scoped((1, 3)), (1, 5))
    assert "more than one" in scope_problem(scoped((1, 3), (3, 5)), (1, 5))
    assert "outside the selected range" in scope_problem(scoped((1, 2), (3, 6)), (1, 5))
    assert "either scope every background" in scope_problem(
        [*scoped((1, 2)), QuranicInputs().background], (1, 5)
    )
    # unscoped: the even split still applies, and needs no validation
    assert scope_problem([QuranicInputs().background], (1, 5)) == ""

    windows = [(1, 0, 1000), (2, 1000, 2600), (3, 2600, 4200), (4, 4200, 5800), (5, 5800, 9000)]
    assert scoped_slices(scoped((1, 2), (3, 5)), windows) == [(0, 2600), (2600, 9000)]


@pytest.mark.slow
def test_quranic_scopes_backgrounds_to_ayah_boundaries(tmp_path) -> None:
    """A background chosen for an ayah starts and ends where that ayah does."""
    settings = dataclasses.replace(
        get_settings(), workdir=tmp_path / "work", outdir=tmp_path / "out"
    )
    engine = Engine(
        settings,
        provider_overrides={"corpus": "quran-fake", "background_pexels": "fake"},
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
                    "backgrounds": [
                        {"kind": "pexels", "query": "light", "ayah_start": 1, "ayah_end": 1},
                        {"kind": "pexels", "query": "path", "ayah_start": 2, "ayah_end": 3},
                    ],
                    "name": "quran-scoped",
                },
            ),
            caller="local",
        )
        finished = engine.wait(job.id, caller="local", timeout_s=300)
    finally:
        engine.close()

    assert finished.state is JobState.SUCCEEDED, finished.error
    timeline = json.loads(
        Path(next(a.path for a in finished.result.artifacts if a.name == "timeline.json")).read_text(
            encoding="utf-8"
        )
    )
    windows = [(entry["startMs"], entry["endMs"]) for entry in timeline["meta"]["backgrounds"]]
    arabic = [caption for caption in timeline["captions"] if caption["lang"] == "ar"]
    assert len(arabic) == 3
    first_ayah_end = arabic[0]["end_ms"]
    assert windows[0] == (0, first_ayah_end), "the first background must end with its ayah"
    assert windows[1][0] == first_ayah_end and windows[1][1] == timeline["duration_ms"]
    even_split = timeline["duration_ms"] / 2
    assert abs(windows[0][1] - even_split) > 200, "this is not an even split"


@pytest.mark.slow
def test_quranic_crossfades_between_backgrounds(tmp_path) -> None:
    """A transition is a fade, and it must not shorten the reel or move the windows."""
    settings = dataclasses.replace(
        get_settings(), workdir=tmp_path / "work", outdir=tmp_path / "out"
    )
    engine = Engine(
        settings,
        provider_overrides={"corpus": "quran-fake", "background_pexels": "fake"},
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
                    "backgrounds": [
                        {"kind": "pexels", "query": "light", "ayah_start": 1, "ayah_end": 1},
                        {"kind": "pexels", "query": "path", "ayah_start": 2, "ayah_end": 3},
                    ],
                    "transition_ms": 600,
                    "name": "quran-fade",
                },
            ),
            caller="local",
        )
        finished = engine.wait(job.id, caller="local", timeout_s=300)
    finally:
        engine.close()

    assert finished.state is JobState.SUCCEEDED, finished.error
    manifest = json.loads(Path(finished.result.manifest).read_text(encoding="utf-8"))
    sequence = next(
        record["asset"]
        for record in manifest["assets"]
        if record["asset"]["kind"] == "background" and record["asset"]["meta"].get("origin") == "sequence"
    )
    assert sequence["meta"]["transitionMs"] == 600
    assert [window["media"] for window in sequence["meta"]["windows"]] == ["video", "video"]
    # the fade overlaps the clips; the reel must still be exactly as long as the recitation
    assert manifest["verification"]["ok"] is True
    checks = {check["name"]: check for check in manifest["verification"]["checks"]}
    assert checks["duration"]["ok"] is True


@pytest.mark.slow
def test_quranic_gives_a_still_background_a_slow_push_in(tmp_path) -> None:
    """A photograph is not left static: it gets the zoom that keeps a hook shot alive."""
    from PIL import Image, ImageDraw

    photo = tmp_path / "backdrop.png"
    image = Image.new("RGB", (1080, 1920), (20, 30, 60))
    ImageDraw.Draw(image).ellipse((200, 500, 880, 1200), fill=(240, 200, 80))
    image.save(photo)

    settings = dataclasses.replace(
        get_settings(), workdir=tmp_path / "work", outdir=tmp_path / "out"
    )
    engine = Engine(settings, provider_overrides={"corpus": "quran-fake"})
    try:
        job = engine.submit(
            JobRequest(
                recipe="quranic",
                inputs={
                    "surah": 1,
                    "ayah_start": 1,
                    "ayah_end": 1,
                    "corpus": "quran-fake",
                    "background": {"kind": "file", "path": str(photo)},
                    "name": "quran-still",
                },
            ),
            caller="local",
        )
        finished = engine.wait(job.id, caller="local", timeout_s=300)
    finally:
        engine.close()

    assert finished.state is JobState.SUCCEEDED, finished.error
    manifest = json.loads(Path(finished.result.manifest).read_text(encoding="utf-8"))
    background = next(
        record["asset"] for record in manifest["assets"] if record["asset"]["kind"] == "background"
    )
    assert background["provenance"]["provider"] == "tenant"
    assert manifest["verification"]["ok"] is True


@pytest.mark.slow
def test_quranic_plays_a_sequence_of_backgrounds(tmp_path) -> None:
    """Several clips, one after another: each is fetched, credited and switched in turn."""
    settings = dataclasses.replace(  # cache_dir follows workdir, so both jobs share one cache
        get_settings(), workdir=tmp_path / "work", outdir=tmp_path / "out"
    )
    registry = RecordingRegistry()
    engine = Engine(
        settings,
        registry=registry,
        provider_overrides={
            "corpus": "quran-fake",
            "background_pexels": "fake",
            "background_gradient": "fake",
        },
    )
    try:
        # prime the ayah-selection cache with a one-clip job: the selection ignores the
        # background, so the sequence job must still see its own list
        priming = engine.submit(
            JobRequest(
                recipe="quranic",
                inputs={
                    "surah": 1,
                    "ayah_start": 1,
                    "ayah_end": 2,
                    "corpus": "quran-fake",
                    "name": "quran-priming",
                },
            ),
            caller="local",
        )
        primed = engine.wait(priming.id, caller="local", timeout_s=300)
        assert primed.state is JobState.SUCCEEDED, primed.error
        job = engine.submit(
            JobRequest(
                recipe="quranic",
                inputs={
                    "surah": 1,
                    "ayah_start": 1,
                    "ayah_end": 2,
                    "corpus": "quran-fake",
                    "backgrounds": [
                        {"kind": "pexels", "query": "clouds"},
                        {"kind": "gradient", "colors": ["#203040", "#405060"]},
                        {"kind": "pexels", "query": "night sky"},
                    ],
                    "transition_ms": 0,  # the hard-cut path, which joins by stream copy
                    "name": "quran-sequence",
                },
            ),
            caller="local",
        )
        finished = engine.wait(job.id, caller="local", timeout_s=300)
    finally:
        engine.close()

    assert finished.state is JobState.SUCCEEDED, finished.error
    manifest = json.loads(Path(finished.result.manifest).read_text(encoding="utf-8"))
    stages = {stage["id"]: stage for stage in manifest["stages"]}
    assert stages["select"]["cached"] is True, "the regression only bites on a cache hit"
    backgrounds = [record["asset"] for record in manifest["assets"] if record["asset"]["kind"] == "background"]
    assert len(backgrounds) >= 3, "the distinct slices plus the sequence they were joined into"
    assert {asset["provenance"]["provider"] for asset in backgrounds} >= {"fake"}
    sequence = next(asset for asset in backgrounds if asset["meta"].get("origin") == "sequence")
    assert len(sequence["meta"]["parts"]) == 3, sequence["meta"]
    assert len(sequence["meta"]["windows"]) == 3

    # the concat list must carry absolute paths: ffmpeg resolves entries against its own
    # directory, and a stage workdir is relative whenever the caller's is
    concat_lists = list((tmp_path / "work" / "jobs").rglob("background-concat.txt"))
    assert concat_lists, "the sequence should have been joined through a concat list"
    entries = [
        line[len("file '") : -1]
        for line in concat_lists[0].read_text(encoding="utf-8").splitlines()
    ]
    assert len(entries) == 3
    assert all(Path(entry).is_absolute() for entry in entries), entries

    timeline_artifact = Path(
        next(a.path for a in finished.result.artifacts if a.name == "timeline.json")
    )
    meta = json.loads(timeline_artifact.read_text(encoding="utf-8"))["meta"]
    assert len(meta["backgrounds"]) == 3 and all(entry["asset"] for entry in meta["backgrounds"])
    windows = [(entry["startMs"], entry["endMs"]) for entry in meta["backgrounds"]]
    assert windows[0][0] == 0
    assert all(windows[index][1] == windows[index + 1][0] for index in range(len(windows) - 1))
    assert meta["background"] == "sequence"
    assert manifest["verification"]["ok"] is True


@pytest.mark.slow
def test_quranic_takes_its_background_from_the_named_provider(tmp_path) -> None:
    """A provider background flows through prep with its own provenance and licence."""
    settings = dataclasses.replace(
        get_settings(), workdir=tmp_path / "work", outdir=tmp_path / "out"
    )
    registry = RecordingRegistry()
    engine = Engine(
        settings,
        registry=registry,
        provider_overrides={"corpus": "quran-fake", "background_pexels": "fake"},
    )
    try:
        job = engine.submit(
            JobRequest(
                recipe="quranic",
                inputs={
                    "surah": 1,
                    "ayah_start": 1,
                    "ayah_end": 1,
                    "corpus": "quran-fake",
                    "background": {"kind": "pexels", "query": "clouds over mountains"},
                    "name": "quran-stock",
                },
            ),
            caller="local",
        )
        finished = engine.wait(job.id, caller="local", timeout_s=300)
    finally:
        engine.close()

    assert finished.state is JobState.SUCCEEDED, finished.error
    assert ("backgrounds", "fake") in registry.loads, registry.loads
    manifest = json.loads(Path(finished.result.manifest).read_text(encoding="utf-8"))
    background = next(
        record for record in manifest["assets"] if record["asset"]["kind"] == "background"
    )["asset"]
    assert background["provenance"]["provider"] == "fake"  # the provider that made it, in writing
    assert background["licence"]["url"].startswith("https://creativecommons.org/publicdomain/zero")
    assert manifest["verification"]["ok"] is True
