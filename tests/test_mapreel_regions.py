# tests/test_mapreel_regions.py
from pathlib import Path

import pytest

from reelmachine.core.errors import InvalidInput
from reelmachine.recipes.mapreel.regions import RegionAtlas

FIXTURE = Path(__file__).parent / "fixtures" / "mapreel" / "regions.geojson"


@pytest.fixture(scope="module")
def regions() -> RegionAtlas:
    return RegionAtlas.load(FIXTURE)


@pytest.mark.parametrize("query,country,code", [
    ("US-TX", "USA", "US-TX"), ("us-tx", "USA", "US-TX"), ("JP-13", "JPN", "JP-13"), ("jp-27", "jpn", "JP-27"),
])
def test_region_codes_resolve(regions, query, country, code):
    place = regions.resolve(query, country)
    assert place.code == code and place.level == "region" and place.polygons


def test_region_names_resolve_inside_their_country(regions):
    assert regions.resolve("Georgia", "USA").code == "US-GA"
    assert regions.resolve("Punjab", "IND").code == "IN-PB"
    assert regions.resolve("Punjab", "PAK").code == "PK-PB"
    assert regions.resolve("Tokyo", "JPN").name == "Tokyo"
    assert regions.resolve("texas", "USA").name == "Texas"


def test_a_region_outside_its_country_is_refused(regions):
    with pytest.raises(InvalidInput, match="US-TX is not in JPN"):
        regions.resolve("US-TX", "JPN")
    with pytest.raises(InvalidInput):
        regions.resolve("Texas", "JPN")


def test_unknown_region_suggests_close_names(regions):
    with pytest.raises(InvalidInput) as err:
        regions.resolve("Texsa", "USA")
    assert "Texas" in (err.value.hint or "")


def test_in_country_lists_regions(regions):
    us = regions.in_country("USA")
    assert {p.code for p in us} == {"US-TX", "US-CA", "US-NY", "US-FL", "US-GA"}
    assert [p.name for p in us] == sorted(p.name for p in us)
    assert regions.in_country("FRA") == []


def test_search_scopes_to_a_country(regions):
    found = regions.search("punjab", "IND", 8)
    assert [r["code"] for r in found] == ["IN-PB"]
    assert found[0]["level"] == "region" and found[0]["parent"] == "IND"
    assert len(regions.search("", "JPN", 2)) == 2
