# tests/test_mapreel_places.py
from pathlib import Path

import pytest

from reelmachine.core.errors import InvalidInput
from reelmachine.recipes.mapreel.places import Atlas

FIXTURE = Path(__file__).parent / "fixtures" / "mapreel" / "countries.geojson"


@pytest.fixture(scope="module")
def atlas() -> Atlas:
    return Atlas.load(FIXTURE)


@pytest.mark.parametrize("query,code", [
    ("ITA", "ITA"), ("ita", "ITA"), ("Italy", "ITA"), ("  italy ", "ITA"),
    ("FRA", "FRA"), ("France", "FRA"),          # ISO_A3 is -99 for France in Natural Earth
    ("NOR", "NOR"), ("Norway", "NOR"),
    ("United States", "USA"), ("USA", "USA"), ("America", "USA"), ("US", "USA"),
    ("United Kingdom", "GBR"), ("UK", "GBR"), ("Britain", "GBR"),
    ("Holland", "NLD"), ("the Netherlands", "NLD"),
    ("South Korea", "KOR"), ("North Korea", "PRK"),
    ("Switzerland", "CHE"), ("Fiji", "FJI"),
])
def test_names_and_codes_resolve(atlas, query, code):
    assert atlas.resolve(query).code == code


def test_an_ambiguous_name_lists_candidates(atlas):
    with pytest.raises(InvalidInput) as err:
        atlas.resolve("Korea")
    assert set(err.value.details["candidates"]) == {"KOR", "PRK"}
    assert "KOR" in err.value.hint and "PRK" in err.value.hint


def test_a_typo_suggests_close_names(atlas):
    with pytest.raises(InvalidInput) as err:
        atlas.resolve("Itally")
    assert "Italy" in err.value.details["suggestions"]


def test_a_place_carries_lon_lat_rings_and_a_label(atlas):
    place = atlas.resolve("ITA")
    lon, lat = place.polygons[0][0][0]
    assert -180 <= lon <= 180 and -90 <= lat <= 90
    assert place.name == "Italy" and 6 < place.label[0] < 19 and 36 < place.label[1] < 48


def test_south_africa_keeps_the_lesotho_hole(atlas):
    zaf = atlas.resolve("ZAF")
    assert any(len(poly) > 1 for poly in zaf.polygons)  # outer ring + hole


def test_search_returns_ranked_candidates(atlas):
    found = atlas.search("korea")
    assert {f["code"] for f in found} >= {"KOR", "PRK"} and all(f["ambiguous"] for f in found[:2])
    assert atlas.search("ital")[0]["code"] == "ITA"


def test_ensure_downloads_once_and_reuses(tmp_path, monkeypatch):
    from reelmachine.recipes.mapreel import geodata

    calls = []

    def fake_download(url, dest):
        calls.append(url)
        dest.write_bytes(b"{}")

    monkeypatch.setattr(geodata, "_download", fake_download)
    first = geodata.ensure(tmp_path, "countries")
    second = geodata.ensure(tmp_path, "countries")
    assert first == second and first.read_bytes() == b"{}" and len(calls) == 1


def test_ensure_explains_a_network_failure(tmp_path, monkeypatch):
    import httpx

    from reelmachine.core.errors import ProviderUnavailable
    from reelmachine.recipes.mapreel import geodata

    def broken(url, dest):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(geodata, "_download", broken)
    with pytest.raises(ProviderUnavailable) as err:
        geodata.ensure(tmp_path, "world")
    assert "once" in err.value.hint and not (tmp_path / geodata.FILES["world"][0]).exists()
