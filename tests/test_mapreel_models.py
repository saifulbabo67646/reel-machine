import pytest
from pydantic import ValidationError

from reelmachine.recipes.mapreel.models import MapReelInputs

BASE = {"segments": [{"kind": "country", "country": "ITA", "narration": "Italy has Gucci.",
                      "items": [{"name": "Gucci"}]}]}


def test_a_country_segment_validates_with_defaults():
    inputs = MapReelInputs.model_validate(BASE)
    seg = inputs.segments[0]
    assert inputs.mode == "build" and inputs.look == "satellite" and inputs.voice == "fake:default"
    assert seg.kind == "country" and seg.items[0].name == "Gucci" and seg.camera.zoom == 1.0
    assert inputs.segment_ids() == ["seg-1"]


def test_highlight_segment_takes_many_countries_and_a_legend():
    inputs = MapReelInputs.model_validate({"segments": [
        {"kind": "highlight", "countries": ["USA", "Italy", "FRA"], "legend": "Fashion giants",
         "narration": "Only three countries..."}]})
    assert inputs.segments[0].countries == ["USA", "Italy", "FRA"]


@pytest.mark.parametrize("bad", [
    {"kind": "planet", "narration": "x"},                                           # unknown kind
    {"kind": "country", "country": "ITA", "narration": "x", "items": []},           # no items
    {"kind": "country", "country": "ITA", "narration": "x", "items": [{"name": "A"}] * 9},  # > 8
    {"kind": "country", "country": "ITA", "narration": "x", "items": [{"name": "A"}], "colour": "red"},
    {"kind": "country", "country": "ITA", "narration": "x", "items": [{"name": "A", "at": -1}]},
    {"kind": "highlight", "countries": [], "narration": "x"},
])
def test_bad_segments_are_refused(bad):
    with pytest.raises(ValidationError):
        MapReelInputs.model_validate({"segments": [bad]})


def test_item_image_accepts_paths_urls_and_commons_ids():
    for image in ("./logos/a.png", "https://example.com/a.png", "commons:File:Gucci logo.svg"):
        item = MapReelInputs.model_validate({"segments": [{"kind": "country", "country": "ITA",
            "narration": "x", "items": [{"name": "A", "image": image}]}]}).segments[0].items[0]
        assert item.image == image
    with pytest.raises(ValidationError):
        MapReelInputs.model_validate({"segments": [{"kind": "country", "country": "ITA",
            "narration": "x", "items": [{"name": "A", "image": "ftp://x/a.png"}]}]})


def test_the_recipe_is_registered():
    from importlib.metadata import entry_points

    names = {ep.name for ep in entry_points(group="reelmachine.recipes")}
    assert "mapreel" in names


def _rank(**extra):
    return {"kind": "rank", "rank": 1, "country": "ITA", "narration": "Italy is number one.", **extra}


@pytest.mark.parametrize("bad", [
    _rank(rank=0),
    _rank(rank=100),
    _rank(stat={"value": "x" * 25}),
])
def test_bad_rank_segments_are_refused(bad):
    with pytest.raises(ValidationError):
        MapReelInputs.model_validate({"segments": [bad]})


def test_a_rank_with_no_items_validates():
    seg = MapReelInputs.model_validate({"segments": [_rank(stat={"value": "70%", "label": "of the world's olive oil"})]}).segments[0]
    assert seg.kind == "rank" and seg.items == [] and seg.stat.value == "70%" and seg.stat.source is None


def _versus(**extra):
    return {"kind": "versus", "countries": ["JPN", "USA"], "narration": "Japan versus America.",
            "stats": [{"value": "125M"}, {"value": "335M"}], **extra}


def test_a_versus_takes_one_stat_per_country():
    seg = MapReelInputs.model_validate({"segments": [_versus()]}).segments[0]
    assert seg.kind == "versus" and seg.countries == ["JPN", "USA"] and [s.value for s in seg.stats] == ["125M", "335M"]


@pytest.mark.parametrize("bad", [
    _versus(stats=[{"value": "125M"}]),                                                      # one stat short
    _versus(countries=["JPN", "USA", "FRA", "ITA"], stats=[{"value": "1"}] * 4),             # four countries
    _versus(countries=["JPN"], stats=[{"value": "1"}]),                                      # one country
])
def test_bad_versus_segments_are_refused(bad):
    with pytest.raises(ValidationError):
        MapReelInputs.model_validate({"segments": [bad]})


def test_a_mismatched_versus_says_why():
    with pytest.raises(ValidationError, match="versus needs one stat per country"):
        MapReelInputs.model_validate({"segments": [_versus(stats=[{"value": "1"}] * 3)]})


@pytest.mark.parametrize("countries", [["JPN", "JPN"], ["Japan", " japan ", "USA"]])
def test_a_versus_refuses_the_same_country_twice(countries):
    with pytest.raises(ValidationError, match="versus needs different countries"):
        MapReelInputs.model_validate({"segments": [_versus(countries=countries,
                                                           stats=[{"value": "1"}] * len(countries))]})
