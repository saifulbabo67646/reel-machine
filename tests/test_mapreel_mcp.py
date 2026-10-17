import dataclasses
import shutil
from contextlib import contextmanager
from pathlib import Path

from reelmachine.config import get_settings
from reelmachine.recipes.mapreel import geodata

FIXTURE = Path(__file__).parent / "fixtures" / "mapreel" / "countries.geojson"


@contextmanager
def _tools(tmp_path):
    from reelmachine.engine import Engine
    from reelmachine.mcp.tenants import TenantRegistry
    from reelmachine.mcp.tools import McpTools

    settings = dataclasses.replace(get_settings(), workdir=tmp_path / "w", shared_cache=tmp_path / "shared")
    root = geodata.geo_cache(settings)
    root.mkdir(parents=True)
    shutil.copy2(FIXTURE, root / geodata.FILES["countries"][0])
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
