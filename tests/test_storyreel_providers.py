"""Provider tests for storyreel: SubDL, OpenSubtitles and the model brain.

All HTTP is mocked with `httpx.MockTransport`; nothing here touches the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from reelmachine.core.errors import ProviderUnavailable
from reelmachine.recipes.storyreel.models import (
    ResolvedMedia,
    SceneChunk,
    StoryConcept,
    StoryReelInputs,
)
from reelmachine.recipes.storyreel.story.llm import LlmStoryBrain
from reelmachine.recipes.storyreel.subtitles import SubtitleRequest
from reelmachine.recipes.storyreel.subtitles.opensubtitles import OpenSubtitlesProvider
from reelmachine.recipes.storyreel.subtitles.subdl import SubdlProvider

SRT_TEXT = (
    "1\n00:00:01,000 --> 00:00:02,500\nFirst line.\n\n"
    "2\n00:00:03,000 --> 00:00:04,500\nSecond line.\n"
)


# ----------------------------------------------------------------------- SubDL


def test_subdl_searches_by_tmdb_and_downloads_a_single_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REEL_SUBDL_API_KEY", "test-key")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "search" in request.url.path:
            assert request.url.params["tmdb_id"] == "603"
            assert request.url.params["type"] == "movie"
            return httpx.Response(
                200,
                json={
                    "subtitles": [
                        {"language": "ja", "nId": 1, "downloads": 500},
                        {"language": "en", "nId": 42, "release": "YIFY", "downloads": 900, "hi": False},
                    ]
                },
            )
        assert request.url.path.endswith("/subtitles/42/download")
        assert request.url.params["format"] == "file"
        return httpx.Response(200, content=SRT_TEXT.encode("utf-8"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = SubdlProvider(client=client)
    fetch = provider.fetch(
        SubtitleRequest(tmdb_id=603, media_type="movie", languages=["en"]),
        dest_dir=tmp_path,
    )
    assert fetch is not None
    assert fetch.language == "en"
    assert fetch.meta["release"] == "YIFY"
    assert "-->" in Path(fetch.path).read_text(encoding="utf-8")
    assert any("/api/v2/" in url for url in calls)
    provider.close()


def test_subdl_tv_search_carries_season_and_episode(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REEL_SUBDL_API_KEY", "test-key")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "search" in request.url.path:
            seen.update(dict(request.url.params))
            return httpx.Response(200, json={"subtitles": []})
        return httpx.Response(404)

    provider = SubdlProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert provider.fetch(
        SubtitleRequest(tmdb_id=1399, media_type="tv", season=1, episode=2, languages=["en"]),
        dest_dir=tmp_path,
    ) is None
    assert seen["season_number"] == "1" and seen["episode_number"] == "2"
    assert "parent_tmdb_id" not in seen  # subdl keys TV by the series tmdb id directly
    provider.close()


def test_subdl_rejects_a_bad_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REEL_SUBDL_API_KEY", "wrong")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "nope"})

    provider = SubdlProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderUnavailable) as failure:
        provider.fetch(SubtitleRequest(tmdb_id=1, languages=["en"]), dest_dir=tmp_path)
    assert "API key" in failure.value.message
    provider.close()


def test_subdl_without_a_key_says_so() -> None:
    provider = SubdlProvider(settings=None)
    provider.api_key = ""
    assert provider.missing()
    with pytest.raises(ProviderUnavailable):
        provider.fetch(SubtitleRequest(tmdb_id=1), dest_dir=Path("."))


# --------------------------------------------------------------- OpenSubtitles


def test_opensubtitles_tv_search_uses_parent_tmdb_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REEL_OPENSUBTITLES_API_KEY", "test-key")
    params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/subtitles"):
            params.update(dict(request.url.params))
            assert request.headers["Api-Key"] == "test-key"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "attributes": {
                                "language": "en",
                                "download_count": 120,
                                "hearing_impaired": False,
                                "files": [{"file_id": 999}],
                                "release": "WEB",
                            }
                        }
                    ]
                },
            )
        if request.url.path.endswith("/download"):
            body = json.loads(request.content)
            assert body["file_id"] == 999 and body["sub_format"] == "srt"
            return httpx.Response(200, json={"link": "https://dl.test/sub.srt", "remaining": 19})
        if request.url.host == "dl.test":
            return httpx.Response(200, content=SRT_TEXT.encode("utf-8"))
        return httpx.Response(404)

    provider = OpenSubtitlesProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))
    fetch = provider.fetch(
        SubtitleRequest(tmdb_id=1399, media_type="tv", season=2, episode=3, languages=["en"]),
        dest_dir=tmp_path,
    )
    assert fetch is not None and fetch.meta["fileId"] == 999
    assert params["parent_tmdb_id"] == "1399"
    assert params["season_number"] == "2" and params["episode_number"] == "3"
    provider.close()


def test_opensubtitles_quota_is_actionable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REEL_OPENSUBTITLES_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/subtitles"):
            return httpx.Response(
                200,
                json={"data": [{"attributes": {"language": "en", "files": [{"file_id": 1}]}}]},
            )
        return httpx.Response(406, json={"message": "quota"})

    provider = OpenSubtitlesProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderUnavailable) as failure:
        provider.fetch(SubtitleRequest(tmdb_id=1, languages=["en"]), dest_dir=tmp_path)
    assert "quota" in failure.value.message.lower()
    provider.close()


# ------------------------------------------------------------------ story brain


def _scenes(count: int) -> list[SceneChunk]:
    return [
        SceneChunk(
            id=f"s{index:04d}",
            start_ms=index * 10_000,
            end_ms=index * 10_000 + 9_000,
            text=f"Scene {index}: something happens. Then something else.",
            cues=2,
        )
        for index in range(1, count + 1)
    ]


MEDIA = ResolvedMedia(tmdb_id=603, title="Inception", media_type="movie", original_language="en")
INPUTS = StoryReelInputs(title="Inception", target_ms=60_000, mode="plan")


def test_llm_brain_maps_moments_then_reduces_to_concepts() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        calls.append("moments" if "batch of scenes" in system else "concepts")
        if "batch of scenes" in system:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": '```json\n{"moments": [{"scene": "s0001", "kind": "shock", "why": "x", "strength": 9}]}\n```'
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "concepts": [
                                        {
                                            "id": "c1",
                                            "name": "The Heist",
                                            "angle": "plot-twist",
                                            "why_viral": "whoa",
                                            "emotional_score": "8/10",
                                            "viral_score": 9,
                                            "retention_score": 8,
                                            "characters": ["Cobb"],
                                            "summary": "…",
                                            "audience_reaction": "…",
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )

    brain = LlmStoryBrain(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        api_key="test",
        base_url="https://api.test",
        model="test-model",
    )
    concepts = brain.concepts(scenes=_scenes(20), media=MEDIA, inputs=INPUTS)
    assert [concept.id for concept in concepts] == ["c1"]
    assert concepts[0].emotional_score == 8  # "8/10" is tolerated
    assert calls.count("moments") == 2  # 20 scenes → two batches of 16
    assert calls.count("concepts") == 1
    brain.close()


def test_llm_brain_writes_a_story_package() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "concept_id": "c1",
                                    "concept_name": "The Heist",
                                    "viral_reason": "…",
                                    "sections": [
                                        {
                                            "id": "sec-001",
                                            "voiceover": "He never told them the truth. [Pause] Not once.",
                                            "clip": {
                                                "scene_ref": "s0003",
                                                "source_start_ms": 125_000,
                                                "source_end_ms": 129_000,
                                                "scene": "Cobb watches",
                                                "zoom": "zoom-in",
                                                "text_overlay": "THE TRUTH",
                                                "transition": "cut",
                                            },
                                        }
                                    ],
                                    "seo": {
                                        "title": "T",
                                        "description": "D",
                                        "hashtags": ["#one"],
                                        "hook": "H",
                                        "cta": "C",
                                    },
                                }
                            )
                        }
                    }
                ]
            },
        )

    brain = LlmStoryBrain(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        api_key="test",
        base_url="https://api.test",
        model="test-model",
    )
    story = brain.script(
        scenes=_scenes(5),
        media=MEDIA,
        inputs=INPUTS,
        concept=StoryConcept(id="c1", name="The Heist"),
    )
    assert len(story.sections) == 1
    section = story.sections[0]
    assert section.clip.zoom == "none"  # an unknown zoom is normalised, not fatal
    assert section.clip.scene_ref == "s0003"  # the subtitle reference survives parsing
    assert section.clip.source_start_ms == 125_000
    assert story.seo is not None and story.seo.hashtags == ["#one"]
    brain.close()


def test_llm_brain_writes_a_long_script_in_acts() -> None:
    """A 5-10 minute script is outlined, then written act by act, then the SEO."""
    calls: list[str] = []

    def reply(payload: dict) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]},
        )

    def handler(request: httpx.Request) -> httpx.Response:
        system = json.loads(request.content)["messages"][0]["content"]
        if "planning a long" in system:
            calls.append("outline")
            return reply(
                {
                    "acts": [
                        {"id": "a1", "title": "सेटअप", "summary": "सब कुछ शुरू होता है", "sections": 8, "scenes": ["s0001", "s0002"]},
                        {"id": "a2", "title": "मोड़", "summary": "सच सामने आता है", "sections": 8, "scenes": ["s0003"]},
                    ]
                }
            )
        if "ONE ACT" in system:
            calls.append("act")
            act_number = calls.count("act")
            return reply(
                {
                    "sections": [
                        {
                            "id": f"sec-{index:03d}",
                            "voiceover": f"act {act_number} line {index} — कहानी आगे बढ़ती है।",
                            "clip": {"source_start_ms": act_number * 1_000 + index * 100, "zoom": "in"},
                        }
                        for index in range(1, 9)
                    ]
                }
            )
        if "publishing metadata" in system:
            calls.append("seo")
            return reply(
                {"seo": {"title": "T", "description": "D", "hashtags": ["#h"], "hook": "H", "cta": "C"}}
            )
        raise AssertionError(f"unexpected prompt: {system[:60]}")

    brain = LlmStoryBrain(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        api_key="test",
        base_url="https://api.test",
    )
    inputs = StoryReelInputs(title="x", target_ms=300_000, language="hi")  # budget 79 > 30
    story = brain.script(
        scenes=_scenes(12), media=MEDIA, inputs=inputs, concept=StoryConcept(id="c1", name="X")
    )
    assert calls == ["outline", "act", "act", "seo"]
    assert len(story.sections) == 16
    assert [section.id for section in story.sections] == [f"sec-{index:03d}" for index in range(1, 17)]
    assert story.sections[0].voiceover.startswith("act 1")
    assert story.sections[-1].voiceover.startswith("act 2")
    assert story.seo is not None and story.seo.title == "T"
    brain.close()


def test_llm_brain_without_a_key_fails_loudly() -> None:
    brain = LlmStoryBrain(api_key="")
    assert brain.missing()
    with pytest.raises(ProviderUnavailable):
        brain.concepts(scenes=_scenes(2), media=MEDIA, inputs=INPUTS)


def test_llm_brain_reports_a_non_json_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "I cannot do that."}}]}
        )

    brain = LlmStoryBrain(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        api_key="test",
        base_url="https://api.test",
    )
    from reelmachine.core.errors import InvalidInput

    with pytest.raises(InvalidInput):
        brain.concepts(scenes=_scenes(2), media=MEDIA, inputs=INPUTS)
    brain.close()
