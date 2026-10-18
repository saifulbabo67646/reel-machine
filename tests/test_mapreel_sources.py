"""sources.md: claims with their sources, image licences, and the data and voice credits."""

from reelmachine.recipes.mapreel import geodata, sources
from reelmachine.recipes.mapreel.models import MapReelInputs, ResolvedSegment


def _inputs(segments, title="Foods"):
    return MapReelInputs.model_validate({"title": title, "segments": segments})


def _resolved(inputs, names):
    return [ResolvedSegment(id=sid, index=i, kind=spec.kind, codes=list(n), names=n, colours={})
            for i, (sid, spec, n) in enumerate(zip(inputs.segment_ids(), inputs.segments, names))]


def _render(inputs, names, credits=None, **kw):
    kw.setdefault("voice_licence", "Synthetic voice, no restrictions")
    return sources.render(inputs, inputs.segment_ids(), credits or {}, data=geodata.LICENCES,
                          resolved=_resolved(inputs, names), **kw)


def test_sources_md_lists_claims_images_and_credits():
    inputs = _inputs([
        {"kind": "rank", "rank": 5, "country": "NLD", "source": "https://x.test/hagel",
         "stat": {"value": "750,000", "label": "sandwiches a day", "source": "https://x.test/stat"},
         "narration": "Number five *starts* gentle.", "items": [{"name": "Hagelslag"}]},
        {"kind": "versus", "countries": ["JPN", "ITA"], "narration": "Two.",
         "stats": [{"value": "1", "source": "https://x.test/j"}, {"value": "2"}]},
        {"kind": "country", "country": "ITA", "narration": "Italy.",
         "items": [{"name": "Gucci", "source": "https://x.test/g"}]}])
    credits = {"seg-3/0": {"name": "Gucci", "source": "commons", "id_or_path": "commons:File:G.png",
                           "licence": "CC BY-SA 4.0", "licence_url": None, "attribution": "Jane Doe",
                           "restrictions": "trademarked"},
               "seg-1/0": {"name": "Hagelslag", "source": "caller", "id_or_path": "/h.png",
                           "licence": "caller-supplied", "licence_url": None,
                           "attribution": "supplied by the script author", "restrictions": None}}
    text = _render(inputs, [{"NLD": "Netherlands"}, {"JPN": "Japan", "ITA": "Italy"}, {"ITA": "Italy"}], credits)
    assert text == (
        "# Sources — Foods\n\n## Claims\n"
        "- #5 Netherlands: Number five starts gentle — https://x.test/hagel\n"
        "- #5 Netherlands: 750,000 sandwiches a day — https://x.test/stat\n"
        "- Japan vs Italy: Japan: 1 — https://x.test/j\n"
        "- Italy: Gucci — https://x.test/g\n\n"
        "## Images\n"
        "- Hagelslag: caller-supplied — supplied by the script author (`/h.png`)\n"
        "- Gucci: CC BY-SA 4.0 — Jane Doe (`commons:File:G.png`) — trademarked\n\n"
        "## Map data and voice\n"
        "- Natural Earth — Public domain — https://www.naturalearthdata.com/\n"
        "- NASA Blue Marble — Public domain — https://visibleearth.nasa.gov/\n"
        "- Narration — fake:default — Synthetic voice, no restrictions\n")
    assert _render(inputs, [{"NLD": "Netherlands"}, {"JPN": "Japan", "ITA": "Italy"}, {"ITA": "Italy"}], credits) == text


def test_sources_md_says_when_claims_are_unsourced():
    inputs = _inputs([{"kind": "country", "country": "ITA", "narration": "Italy.", "items": [{"name": "Gucci"}]}])
    text = _render(inputs, [{"ITA": "Italy"}])
    assert "## Claims\n- No sources were given for the claims in this reel.\n" in text
    assert "## Images\n- No images (items are shown as text badges).\n" in text


def test_sources_md_merges_a_url_used_twice_in_one_segment():
    inputs = _inputs([{"kind": "country", "country": "ITA", "narration": "Italy.", "items": [
        {"name": "Gucci", "source": "https://x.test/it"}, {"name": "Prada", "source": "https://x.test/it"},
        {"name": "Gucci", "source": "https://x.test/it"}]}])
    text = _render(inputs, [{"ITA": "Italy"}])
    assert text.count("https://x.test/it") == 1
    assert "- Italy: Gucci; Prada — https://x.test/it" in text


def test_a_long_segment_source_claim_is_cut_on_a_word_with_an_ellipsis():
    inputs = _inputs([{"kind": "country", "country": "ITA", "source": "https://x.test/s",
                       "narration": "word " * 70, "items": [{"name": "Gucci"}]}])
    line = next(l for l in _render(inputs, [{"ITA": "Italy"}]).splitlines() if "x.test/s" in l)
    claim = line.split(": ", 1)[1].rsplit(" — ", 1)[0]
    assert claim.endswith("word…") and len(claim) <= 121


def test_merged_claims_drop_trailing_periods_and_join_with_semicolons():
    inputs = _inputs([{"kind": "rank", "rank": 1, "country": "ITA", "source": "https://x.test/a",
                       "narration": "It is old.", "items": [{"name": "Cheese.", "source": "https://x.test/a"}]}])
    assert "- #1 Italy: It is old; Cheese — https://x.test/a" in _render(inputs, [{"ITA": "Italy"}])


def test_user_text_cannot_forge_sections_or_bullets():
    evil = "A\n## Images\n- fake\n# Sources"
    inputs = _inputs([{"kind": "country", "country": "ITA", "narration": "Italy.", "source": "x\n## Map data and voice",
                       "items": [{"name": evil, "source": "https://x.test/a\n## Images"}]}], title=evil)
    credits = {"seg-1/0": {"name": evil, "source": "caller", "id_or_path": "/p\n## Claims\n- x.png",
                           "licence": evil, "licence_url": None, "attribution": evil, "restrictions": evil}}
    text = _render(inputs, [{"ITA": "Italy"}], credits, voice_licence=evil)
    lines = text.splitlines()
    assert [l for l in lines if l.startswith("#")] == [
        l for l in lines if l.startswith("# Sources — ")] + ["## Claims", "## Images", "## Map data and voice"]
    assert sum(l.startswith("# ") for l in lines) == 1
    assert sum(l.startswith("- ") for l in lines) == 2 + 1 + 3  # two claims, one image, three credits
    assert "`x ## Map data and voice`" in text and "`/p ## Claims - x.png`" in text


def test_the_voice_licence_reaches_the_narration_line():
    from types import SimpleNamespace

    from reelmachine.core.assets import Licence
    from reelmachine.recipes.mapreel import stages

    inputs = _inputs([{"kind": "country", "country": "ITA", "narration": "Italy.", "items": [{"name": "Gucci"}]}])
    seg = _resolved(inputs, [{"ITA": "Italy"}])
    import tempfile
    from pathlib import Path
    for licence, expected in (
            (Licence(name="CC-BY 4.0", attribution="Voice by Ann", url="https://v.test"), "CC-BY 4.0"),
            (Licence(attribution="Voice by Ann"), "Voice by Ann"),
            (None, "licence not stated")):
        narration = SimpleNamespace(inputs=inputs, image_credits={}, segments=seg, licence=licence)
        with tempfile.TemporaryDirectory() as tmp:
            ctx = SimpleNamespace(workdir=Path(tmp))
            text = stages._write_sources(ctx, narration).read_text(encoding="utf-8")
        assert f"- Narration — fake:default — {expected}\n" in text


def test_flat_look_omits_the_blue_marble_credit():
    inputs = _inputs([{"kind": "country", "country": "ITA", "narration": "Italy.", "items": [{"name": "Gucci"}]}])
    text = _render(inputs, [{"ITA": "Italy"}], look="flat")
    assert "Blue Marble" not in text and "Natural Earth" in text
