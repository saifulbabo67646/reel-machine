import dataclasses
import shutil
from contextlib import contextmanager
from pathlib import Path

from reelmachine.config import get_settings
from reelmachine.recipes.mapreel import geodata

FIXTURE = Path(__file__).parent / "fixtures" / "mapreel" / "countries.geojson"
REGIONS = FIXTURE.with_name("regions.geojson")


@contextmanager
def _tools(tmp_path, regions=False):
    from reelmachine.engine import Engine
    from reelmachine.mcp.tenants import TenantRegistry
    from reelmachine.mcp.tools import McpTools

    settings = dataclasses.replace(get_settings(), workdir=tmp_path / "w", shared_cache=tmp_path / "shared")
    root = geodata.geo_cache(settings)
    root.mkdir(parents=True)
    shutil.copy2(FIXTURE, root / geodata.FILES["countries"][0])
    if regions:
        shutil.copy2(REGIONS, root / geodata.FILES["regions"][0])
    engine = Engine(settings)
    try:
        yield McpTools(engine=engine, tenants=TenantRegistry())
    finally:
        engine.close()


def test_find_places_returns_codes_and_flags_ambiguity(tmp_path):
    with _tools(tmp_path) as tools:
        found = tools.find_places(None, "korea")
    assert found["schemaVersion"] == 1
    codes = {p["code"] for p in found["places"]}
    assert {"KOR", "PRK"} <= codes and any(p["ambiguous"] for p in found["places"])
    assert "country" in found["hint"]


def test_find_places_with_no_match_says_so(tmp_path):
    with _tools(tmp_path) as tools:
        found = tools.find_places(None, "zzzz")
    assert found["places"] == [] and "ISO" in found["hint"]


def test_find_places_with_a_country_lists_its_regions(tmp_path):
    with _tools(tmp_path, regions=True) as tools:
        found = tools.find_places(None, "texas", country="United States")
        every = tools.find_places(None, "", limit=3, country="JPN")
    assert [p["code"] for p in found["places"]] == ["US-TX"]
    assert found["places"][0] == {"code": "US-TX", "name": "Texas", "level": "region", "parent": "USA", "ambiguous": False}
    assert len(every["places"]) == 3 and {p["parent"] for p in every["places"]} == {"JPN"}
    # the hint shows the RegionEntry shape the model takes, not a bare list of codes
    assert '"regions": [{"region": "<code>"' in found["hint"] and '["<code>"' not in found["hint"]


def test_find_places_code_shape_searches_regions(tmp_path):
    with _tools(tmp_path, regions=True) as tools:
        found = tools.find_places(None, "JP-13")
    assert [p["code"] for p in found["places"]] == ["JP-13"]


def test_country_results_flag_regions_only_when_the_file_is_cached(tmp_path):
    with _tools(tmp_path, regions=True) as tools:
        flagged = {p["code"]: p.get("regions") for p in tools.find_places(None, "united states")["places"]}
    assert flagged["USA"] is True
    with _tools(tmp_path / "bare") as tools:
        assert all("regions" not in p for p in tools.find_places(None, "united states")["places"])


def test_a_country_search_never_touches_the_regions_file(tmp_path, monkeypatch):
    from reelmachine.recipes.mapreel import regions

    def boom(*args, **kwargs):
        raise AssertionError("the regions file was opened")

    monkeypatch.setattr(regions, "region_atlas_for", boom)
    monkeypatch.setattr(regions.RegionAtlas, "load", boom)
    with _tools(tmp_path) as tools:  # no regions file in the cache at all
        found = tools.find_places(None, "italy")
    assert found["places"][0]["code"] == "ITA"


def test_a_bare_country_prefix_with_a_dash_is_not_a_region_code(tmp_path, monkeypatch):
    """'ab-' is no region code: it must not open (and so maybe download) the 40 MB regions file."""
    from reelmachine.recipes.mapreel import regions

    def boom(*args, **kwargs):
        raise AssertionError("the regions file was opened")

    monkeypatch.setattr(regions, "region_atlas_for", boom)
    monkeypatch.setattr(regions.RegionAtlas, "load", boom)
    with _tools(tmp_path) as tools:
        tools.find_places(None, "ab-")
        tools.find_places(None, "JP-")


def test_find_images_tool_payload_and_errors(tmp_path, monkeypatch):
    import json

    import pytest
    from mcp.server.mcpserver.exceptions import ToolError

    from reelmachine.recipes.mapreel import commons

    seen = []

    def fake(params):
        seen.append(params)
        return {"query": {"pages": {"1": {
            "title": "File:Nike.svg", "index": 1,
            "imageinfo": [{"url": "u", "thumburl": "t", "width": 1, "height": 2, "mime": "image/svg+xml",
                           "extmetadata": {"LicenseShortName": {"value": "CC0"}}}],
        }}}}

    monkeypatch.setattr(commons, "_http_json", fake)
    with _tools(tmp_path) as tools:
        found = tools.find_images(None, "Nike", kind="logo", limit=999)
        assert found["schemaVersion"] and found["query"] == "Nike"
        assert found["results"][0]["id"] == "commons:File:Nike.svg"
        assert found["results"][0]["permitted"] is True
        assert "image" in found["note"] and "trademark" in found["note"]
        assert seen[0]["gsrlimit"] == 50
        with pytest.raises(ToolError) as err:
            tools.find_images(None, "  ")
        assert json.loads(str(err.value))["code"] == "INVALID_INPUT"

        def down(params):
            from reelmachine.core.errors import ProviderUnavailable
            raise ProviderUnavailable("Wikimedia Commons did not answer", hint="retry")

        monkeypatch.setattr(commons, "_http_json", down)
        with pytest.raises(ToolError) as err:
            tools.find_images(None, "Nike")
        assert json.loads(str(err.value))["code"] == "PROVIDER_UNAVAILABLE"


def test_the_regions_flag_reads_a_sidecar_index_after_the_first_parse(tmp_path, monkeypatch):
    """The first flagged search parses the 40 MB admin-1 file once and writes regions-index.json beside it;
    every search after that reads the index and never opens the GeoJSON."""
    import builtins
    import io
    import json

    from reelmachine.recipes.mapreel import regions

    with _tools(tmp_path, regions=True) as tools:
        first = {p["code"]: p.get("regions") for p in tools.find_places(None, "united states")["places"]}
        index = geodata.geo_cache(tools.engine.settings) / "regions-index.json"
        assert json.loads(index.read_text()) == {"IND": 1, "JPN": 3, "PAK": 1, "USA": 5}

        name = geodata.FILES["regions"][0]
        real_open, real_io_open = builtins.open, io.open

        def guarded(opener):
            def inner(file, *args, **kwargs):
                if str(file).endswith(name):
                    raise AssertionError("the regions GeoJSON was opened")
                return opener(file, *args, **kwargs)
            return inner

        def boom(*args, **kwargs):
            raise AssertionError("the regions atlas was loaded")

        monkeypatch.setattr(builtins, "open", guarded(real_open))
        monkeypatch.setattr(io, "open", guarded(real_io_open))
        monkeypatch.setattr(regions, "region_atlas_for", boom)
        monkeypatch.setattr(regions.RegionAtlas, "load", boom)
        monkeypatch.setattr(regions, "_load", boom)
        again = {p["code"]: p.get("regions") for p in tools.find_places(None, "united states")["places"]}
        japan = tools.find_places(None, "japan")["places"]
    assert first == again and again["USA"] is True
    assert japan[0]["code"] == "JPN" and japan[0]["regions"] is True


def test_a_stale_or_damaged_regions_index_is_rebuilt(tmp_path):
    import json
    import os

    with _tools(tmp_path, regions=True) as tools:
        root = geodata.geo_cache(tools.engine.settings)
        index = root / "regions-index.json"
        index.write_text("{not json")
        assert tools.find_places(None, "united states")["places"][0].get("regions") is True
        assert json.loads(index.read_text())["USA"] == 5
        # an index older than the admin-1 file it counts (a fresh download) is counted again
        index.write_text(json.dumps({"USA": 0}))
        old = (root / geodata.FILES["regions"][0]).stat().st_mtime_ns - 10**9
        os.utime(index, ns=(old, old))
        assert tools.find_places(None, "united states")["places"][0].get("regions") is True
        assert json.loads(index.read_text())["USA"] == 5
        assert not [p for p in root.iterdir() if p.name.startswith("regions-index") and p != index]


def test_a_read_only_cache_still_answers_the_regions_flag(tmp_path, monkeypatch):
    import tempfile

    def refuse(*args, **kwargs):
        raise PermissionError("read-only cache")

    with _tools(tmp_path, regions=True) as tools:
        monkeypatch.setattr(tempfile, "mkstemp", refuse)
        assert tools.find_places(None, "united states")["places"][0].get("regions") is True
