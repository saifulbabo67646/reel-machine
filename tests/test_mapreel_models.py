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


def _regions(**extra):
    return {"kind": "regions", "country": "USA", "narration": "Texas has BBQ and California has avocados.",
            "regions": [{"region": "Texas", "items": [{"name": "BBQ"}]},
                        {"region": "US-CA", "say": "California", "items": [{"name": "Avocado"}]}], **extra}


def test_a_regions_segment_takes_regions_with_their_items():
    seg = MapReelInputs.model_validate({"segments": [_regions()]}).segments[0]
    assert seg.kind == "regions" and seg.country == "USA" and [r.region for r in seg.regions] == ["Texas", "US-CA"]
    assert seg.regions[1].say == "California" and seg.regions[0].colour is None
    assert [it.name for it in seg.items] == ["BBQ", "Avocado"]  # every region's items, in order


@pytest.mark.parametrize("bad", [
    _regions(regions=[]),                                                              # no regions
    _regions(regions=[{"region": f"R{i}"} for i in range(13)]),                        # > 12
    _regions(regions=[{"region": "Texas", "items": [{"name": "A"}] * 7}]),             # > 6 items
    _regions(regions=[{"region": "Texas", "colour": "red"}]),
    _regions(regions=[{"region": "Texas", "extra": 1}]),
])
def test_bad_regions_segments_are_refused(bad):
    with pytest.raises(ValidationError):
        MapReelInputs.model_validate({"segments": [bad]})


FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures" / "mapreel"


def _resolve(segments):
    from reelmachine.recipes.mapreel import stages
    from reelmachine.recipes.mapreel.places import Atlas
    from reelmachine.recipes.mapreel.regions import RegionAtlas

    calls = []

    def regions():
        calls.append(1)
        return RegionAtlas.load(FIXTURES / "regions.geojson")

    inputs = MapReelInputs.model_validate({"segments": segments})
    return stages.resolve_segments(inputs, Atlas.load(FIXTURES / "countries.geojson"), regions), calls


def test_regions_resolve_inside_their_country_with_colours_of_their_own():
    (seg,), calls = _resolve([_regions(regions=[{"region": "Texas"}, {"region": "US-CA"}, {"region": "Georgia"}])])
    assert seg.codes == ["USA"] and calls == [1]
    assert [(r["code"], r["name"]) for r in seg.regions] == [("US-TX", "Texas"), ("US-CA", "California"),
                                                               ("US-GA", "Georgia")]
    colours = [r["colour"] for r in seg.regions]
    assert len(set(colours)) == 3 and seg.colours["USA"] not in colours


def test_a_region_colour_is_kept_and_no_region_atlas_loads_without_regions():
    (seg,), _ = _resolve([_regions(regions=[{"region": "Texas", "colour": "#123456"}, {"region": "US-CA"}])])
    assert seg.regions[0]["colour"] == "#123456" and seg.regions[1]["colour"] not in ("#123456", seg.colours["USA"])
    plain, calls = _resolve([{"kind": "country", "country": "ITA", "narration": "x", "items": [{"name": "a"}]}])
    assert calls == [] and plain[0].regions == []


def test_a_bad_region_fails_with_its_path():
    from reelmachine.core.errors import InvalidInput

    with pytest.raises(InvalidInput) as caught:
        _resolve([{"kind": "country", "country": "ITA", "narration": "x", "items": [{"name": "a"}]},
                  _regions(regions=[{"region": "Texas"}, {"region": "Texsa"}])])
    assert caught.value.details["path"] == "segments[1].regions[1].region"
    assert "Texas" in caught.value.hint
