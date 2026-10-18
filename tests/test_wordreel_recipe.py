"""The wordreel recipe end to end: corpus, stages, artifacts and MCP discovery.

The build tests use the `fake` clip source (one synthetic episode plus a fixed English
story), so they exercise the real search, the real cutter and the real composer with no
network and no fixture media in the repository.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reelmachine.config import get_settings
from reelmachine.core.registry import Registry
from reelmachine.recipes.wordreel.clips import ClipRequest
from reelmachine.recipes.wordreel.fake import FakeClipSource
from reelmachine.recipes.wordreel.library import LibraryClipSource
from reelmachine.recipes.wordreel.models import WordReelInputs
from reelmachine.recipes.wordreel.recipe import WordReelRecipe, run_wordreel

SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\nShe turned the light off.\n\n"
    "2\n00:00:04,000 --> 00:00:06,500\nThe lighthouse was dark that night.\n\n"
    "3\n00:00:08,000 --> 00:00:10,000\nHe lit the fire and waited.\n\n"
    "4\n00:00:12,000 --> 00:00:14,000\nLight.\n"
)


@pytest.fixture()
def fake_source() -> FakeClipSource:
    return FakeClipSource(get_settings())


# ----------------------------------------------------------------- registration


def test_the_recipe_registers_through_entry_points() -> None:
    assert "wordreel" in Registry().names("recipes")


def test_the_clip_sources_register_through_entry_points() -> None:
    names = Registry().names("clips")
    assert {"library", "wordreel-fake"} <= set(names)


def test_the_recipe_declares_the_shape_the_engine_expects() -> None:
    spec = WordReelRecipe().spec
    assert spec.input_model is WordReelInputs
    assert [stage.stage.id for stage in spec.stages] == ["select", "scan", "prep", "render"]
    assert spec.providers["corpus"].group == "clips"
    assert "reel.mp4" in spec.artifacts


def test_a_broken_clip_source_is_reported_not_raised_by_doctor() -> None:
    from reelmachine.engine.providers import provider_health

    rows = {row["name"]: row for row in provider_health(settings=get_settings()) if row["group"] == "clips"}
    assert "library" in rows
    assert rows["library"]["status"] in {"ok", "missing"}


# --------------------------------------------------------------- the library source


def test_a_library_scan_finds_lines_and_their_footage(tmp_path) -> None:
    media = tmp_path / "Film.mkv"
    media.write_bytes(b"\x00")  # the pairing only needs the file to exist
    (tmp_path / "Film.en.srt").write_text(SRT, encoding="utf-8")

    source = LibraryClipSource(get_settings())
    found = source.find(ClipRequest(word="light", library=[str(tmp_path)]), limit=10)

    # ranked, not in file order; case is preserved as the line has it
    assert sorted(item.match_surface.lower() for item in found) == ["light", "light", "lit"]
    assert all(item.media_path == str(media) for item in found)
    assert all(item.subtitle_source.startswith("sidecar:") for item in found)


def test_a_word_inside_a_longer_word_is_not_a_hit(tmp_path) -> None:
    (tmp_path / "Film.en.srt").write_text(SRT, encoding="utf-8")
    source = LibraryClipSource(get_settings())
    found = source.find(ClipRequest(word="light", subtitles=[str(tmp_path / "Film.en.srt")]))
    assert all("lighthouse" not in item.match_surface for item in found)


def test_a_scan_with_no_media_still_reports_the_lines(tmp_path) -> None:
    """A subtitle alone is a real example — it just cannot be cut."""
    subtitle = tmp_path / "Some Film.en.srt"
    subtitle.write_text(SRT, encoding="utf-8")
    source = LibraryClipSource(get_settings())
    found = source.find(ClipRequest(word="light", subtitles=[str(subtitle)]))
    assert found
    assert all(item.media_path == "" for item in found)


def test_the_roll_widens_the_cut_without_going_negative(tmp_path) -> None:
    (tmp_path / "Film.en.srt").write_text(SRT, encoding="utf-8")
    source = LibraryClipSource(get_settings())
    found = source.find(
        ClipRequest(word="light", subtitles=[str(tmp_path / "Film.en.srt")])
    )
    first = min(found, key=lambda item: item.start_ms)
    assert first.start_ms >= 0
    assert first.end_ms > first.start_ms


def _srt(*lines: str) -> str:
    """A well-formed SRT over `lines`, three seconds apart."""
    blocks = []
    for index, line in enumerate(lines, start=1):
        start = (index - 1) * 3
        blocks.append(
            f"{index}\n00:00:{start:02d},000 --> 00:00:{start + 2:02d},000\n{line}\n"
        )
    return "\n".join(blocks)


def test_per_source_spreads_the_examples_across_titles(tmp_path) -> None:
    """`per_source` is a variety preference: within `limit`, each film contributes one."""
    (tmp_path / "Alpha.mkv").write_bytes(b"\x00")
    (tmp_path / "Alpha.en.srt").write_text(
        _srt("She turned the light off.", "The light was everywhere."), encoding="utf-8"
    )
    (tmp_path / "Beta.mkv").write_bytes(b"\x00")
    (tmp_path / "Beta.en.srt").write_text(
        _srt("He carried the light upstairs.", "A light in the window."), encoding="utf-8"
    )
    source = LibraryClipSource(get_settings())
    found = source.find(
        ClipRequest(word="light", library=[str(tmp_path)], per_source=1), limit=2
    )
    assert len(found) == 2
    assert len({item.media_label for item in found}) == 2


def test_a_single_title_still_fills_the_reel(tmp_path) -> None:
    """The preference protects variety; it does not starve a library with one film."""
    (tmp_path / "Only.mkv").write_bytes(b"\x00")
    (tmp_path / "Only.en.srt").write_text(SRT, encoding="utf-8")
    source = LibraryClipSource(get_settings())
    found = source.find(
        ClipRequest(word="light", library=[str(tmp_path)], per_source=1), limit=10
    )
    assert len(found) > 1


def test_suggest_offers_words_the_corpus_can_actually_teach(tmp_path) -> None:
    (tmp_path / "Film.en.srt").write_text(SRT, encoding="utf-8")
    source = LibraryClipSource(get_settings())
    words = source.suggest(ClipRequest(subtitles=[str(tmp_path / "Film.en.srt")]), limit=10)
    assert "light" in words
    # a word that appears once has nothing to teach from
    assert "waited" not in words


def test_a_missing_library_is_reported_by_missing() -> None:
    settings = get_settings()
    bare = LibraryClipSource(settings)
    if not settings.wordreel_library:
        assert bare.missing()


# --------------------------------------------------------------------- the recipe


def test_probe_is_free_and_reports_the_corpus(fake_source) -> None:
    from reelmachine.core.recipe import ProbeContext
    from reelmachine.core.stage import StaticProviders

    settings = get_settings()
    report = WordReelRecipe().probe(
        WordReelInputs(word="light", library=[str(fake_source.root)]),
        ProbeContext(config=settings, providers=StaticProviders({"corpus": fake_source})),
    )
    assert report.quota_free is True
    assert [check.name for check in report.checks] == ["ffmpeg", "corpus", "word"]


def test_probe_rejects_a_word_that_cannot_be_a_pattern() -> None:
    from reelmachine.core.recipe import ProbeContext

    report = WordReelRecipe().probe(
        WordReelInputs(word="(", match_mode="regex"), ProbeContext(config=get_settings())
    )
    word_check = next(check for check in report.checks if check.name == "word")
    assert word_check.ok is False


def test_scan_mode_lists_examples_and_stops_before_cutting(fake_source) -> None:
    runs, outputs = run_wordreel(
        {"word": "light", "mode": "scan", "count": 5},
        corpus=fake_source,
        settings=get_settings(),
    )
    assert [run.id for run in runs] == ["select", "scan"]
    scan = outputs["scan"]
    assert scan.candidates
    assert all(item.media_path for item in scan.candidates)
    assert scan.plan["word"] == "light"


def test_suggest_mode_proposes_a_word_and_stops(fake_source) -> None:
    runs, outputs = run_wordreel(
        {"mode": "suggest"}, corpus=fake_source, settings=get_settings()
    )
    assert [run.id for run in runs] == ["select"]
    suggestions = outputs["select"].suggestions
    assert suggestions and suggestions[0] == "light"


def test_suggest_mode_is_required_to_run_without_a_word(fake_source) -> None:
    from reelmachine.core.errors import InvalidInput

    with pytest.raises(InvalidInput, match="no word"):
        run_wordreel({"mode": "build"}, corpus=fake_source, settings=get_settings())


def test_a_word_with_no_examples_fails_with_a_useful_error(fake_source) -> None:
    from reelmachine.core.errors import InvalidInput

    with pytest.raises(InvalidInput, match="no line in this corpus"):
        run_wordreel(
            {"word": "zzzznotaword", "mode": "scan"},
            corpus=fake_source,
            settings=get_settings(),
        )


def test_a_build_cuts_the_examples_and_renders_a_reel(fake_source, tmp_path) -> None:
    settings = get_settings()
    runs, outputs = run_wordreel(
        {
            "word": "light",
            "mode": "build",
            "count": 3,
            "gloss": "illumination",
            "aspect": "vertical",
            "name": "wordreel-test",
            "outdir": str(tmp_path / "out"),
        },
        corpus=fake_source,
        settings=settings,
        cache_root=tmp_path / "cache",
    )
    assert [run.id for run in runs] == ["select", "scan", "prep", "render"]

    prep = outputs["prep"]
    assert len(prep.clips) == 3
    assert all(record.emphasis == "light" for record in prep.clips)
    assert all(record.words for record in prep.clips)
    assert prep.duration_ms == sum(record.duration_ms for record in prep.clips)

    render = outputs["render"]
    video = Path(render.video)
    assert video.is_file() and video.stat().st_size > 0
    assert render.render_mode == "wordreel"

    # the captions teach the word: it is pinned, glossed, and highlighted in the line
    ass = Path(render.ass).read_text(encoding="utf-8")
    assert "Style: Word" in ass and "Style: Gloss" in ass and "Style: Line" in ass
    assert "illumination" in ass
    assert "\\c&H" in ass  # the emphasis colour
    srt = Path(render.srt).read_text(encoding="utf-8")
    assert "light" in srt and "\\c" not in srt  # SRT stays plain text

    manifest = json.loads(Path(render.manifest).read_text(encoding="utf-8"))
    assert manifest["recipe"] == "wordreel"
    assert manifest["word"] == "light"
    assert len(manifest["examples"]) == 3


def test_the_timeline_places_every_clip_end_to_end(fake_source, tmp_path) -> None:
    _, outputs = run_wordreel(
        {"word": "light", "mode": "build", "count": 3, "outdir": str(tmp_path / "out")},
        corpus=fake_source,
        settings=get_settings(),
        cache_root=tmp_path / "cache",
    )
    timeline = outputs["prep"].timeline
    cursor = 0
    for element in timeline.video:
        assert element.start_ms == cursor
        cursor += element.duration_ms
    assert timeline.duration_ms == cursor


def test_every_spoken_caption_carries_its_emphasis(fake_source, tmp_path) -> None:
    _, outputs = run_wordreel(
        {"word": "light", "mode": "build", "count": 3, "outdir": str(tmp_path / "out")},
        corpus=fake_source,
        settings=get_settings(),
        cache_root=tmp_path / "cache",
    )
    timeline = outputs["prep"].timeline
    spoken = [c for c in timeline.captions if c.style_ref.endswith(".line")]
    assert len(spoken) == 3
    for caption in spoken:
        assert caption.meta["emphasis"] == "light"
        assert caption.words
        assert caption.text.lower().count("light") >= 1


def test_verification_flags_a_reel_that_teaches_the_wrong_thing(fake_source, tmp_path) -> None:
    from reelmachine.core.stage import StageContext, StaticProviders
    from reelmachine.recipes.wordreel.verify import verify_wordreel

    _, outputs = run_wordreel(
        {"word": "light", "mode": "build", "count": 3, "outdir": str(tmp_path / "out")},
        corpus=fake_source,
        settings=get_settings(),
        cache_root=tmp_path / "cache",
    )
    settings = get_settings()
    ctx = StageContext(
        job=None,  # type: ignore[arg-type]
        workdir=tmp_path,
        assets=None,  # type: ignore[arg-type]
        config=settings,
        providers=StaticProviders({"corpus": fake_source}),
    )
    report = verify_wordreel(ctx, outputs)
    assert report.ok
    assert report.render_mode == "wordreel"
    # every example comes from the one mock episode, and the verifier must say so
    assert any("one title" in note for note in report.visible_degradation)


# ------------------------------------------------------------------- MCP surface


@pytest.fixture()
def mcp_tools(monkeypatch, fake_source):
    """The MCP tools bound to an engine whose wordreel corpus is the fixture."""
    monkeypatch.setenv("REEL_WORDREEL_LIBRARY", str(fake_source.root))
    fake_source.prepare()

    from reelmachine.engine import Engine
    from reelmachine.mcp.tenants import TenantRegistry
    from reelmachine.mcp.tools import McpTools

    engine = Engine(get_settings())
    tools = McpTools(engine=engine, tenants=TenantRegistry())
    yield tools
    engine.close()


def test_mcp_suggest_words_proposes_from_the_corpus(mcp_tools, fake_source) -> None:
    pytest.importorskip("mcp")
    result = mcp_tools.suggest_words(
        None, recipe="wordreel", inputs={"library": [str(fake_source.root)]}, limit=6
    )
    assert result["corpus"] == "library"
    assert result["words"][0] == "light"
    assert "hint" in result


def test_mcp_find_examples_reports_lines_and_their_footage(mcp_tools, fake_source) -> None:
    pytest.importorskip("mcp")
    result = mcp_tools.find_examples(
        None, word="light", recipe="wordreel", inputs={"library": [str(fake_source.root)]}, limit=3
    )
    assert result["counts"]["matched"] == 3
    assert result["counts"]["withFootage"] == 3
    assert all(example["text"] for example in result["examples"])
    assert all(example["startMs"] < example["endMs"] for example in result["examples"])


def test_mcp_find_examples_matches_inflections(mcp_tools, fake_source) -> None:
    """The tool must answer the question an agent asks, not only a literal one."""
    pytest.importorskip("mcp")
    result = mcp_tools.find_examples(
        None, word="tell", recipe="wordreel",
        inputs={"library": [str(fake_source.root)], "allow_inflected": True}, limit=5,
    )
    assert result["counts"]["matched"] >= 1
    assert any(example["matched"].lower() == "told" for example in result["examples"])
    assert all(example["exact"] is False for example in result["examples"])


def test_mcp_find_examples_respects_allow_inflected(mcp_tools, fake_source) -> None:
    pytest.importorskip("mcp")
    result = mcp_tools.find_examples(
        None, word="tell", recipe="wordreel",
        inputs={"library": [str(fake_source.root)], "allow_inflected": False},
    )
    assert result["counts"]["matched"] == 0


def test_mcp_find_examples_is_empty_rather_than_an_error_for_an_absent_word(
    mcp_tools, fake_source
) -> None:
    pytest.importorskip("mcp")
    result = mcp_tools.find_examples(
        None, word="zzzznotaword", recipe="wordreel",
        inputs={"library": [str(fake_source.root)]},
    )
    assert result["counts"]["matched"] == 0
    assert result["examples"] == []
    assert "needs media" in result["hint"]
