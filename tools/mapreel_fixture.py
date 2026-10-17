# tools/mapreel_fixture.py
"""Cut a small, real-geometry Natural Earth subset for mapreel's tests.

    uv run python tools/mapreel_fixture.py            # countries.geojson
    uv run python tools/mapreel_fixture.py --regions  # regions.geojson (downloads admin-1, 40 MB)

Downloads admin-0 10m once (13 MB), keeps a few countries that exercise the hard
cases (far territories, the antimeridian, ambiguous names, tiny states) and
simplifies them so the fixture stays small.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reelmachine.recipes.mapreel.geodata import FILES  # noqa: E402
from reelmachine.recipes.mapreel.geometry import simplify  # noqa: E402

KEEP = {"ITA", "FRA", "USA", "RUS", "FJI", "KOR", "PRK", "NLD", "GBR", "JPN", "SGP", "NOR", "CHE", "DNK", "ZAF", "LSO"}
PROPS = ("ISO_A3_EH", "ADM0_A3", "ISO_A2_EH", "NAME", "NAME_LONG", "NAME_EN", "ADMIN", "FORMAL_EN",
         "BRK_NAME", "ABBREV", "NAME_ALT", "LABEL_X", "LABEL_Y")
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "mapreel" / "countries.geojson"
REGION_KEEP = {"US-TX", "US-CA", "US-NY", "US-FL", "US-GA", "JP-13", "JP-27", "JP-01", "IN-PB", "PK-PB"}
REGION_PROPS = ("iso_3166_2", "adm0_a3", "name", "name_en", "name_alt", "woe_name", "gn_name", "longitude", "latitude")
REGIONS_OUT = OUT.with_name("regions.geojson")


def regions() -> None:
    data = httpx.get(FILES["regions"][1], timeout=600, follow_redirects=True).json()
    feats = []
    for f in data["features"]:
        p = f["properties"]
        if p.get("iso_3166_2") not in REGION_KEEP:
            continue
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        slim = []
        for poly in polys:
            rings = [[list(map(float, pt)) for pt in simplify(ring, 0.02)] for ring in poly]
            if len(rings[0]) >= 4:
                slim.append(rings)
        feats.append({"type": "Feature", "properties": {k: p.get(k) for k in REGION_PROPS},
                      "geometry": {"type": "MultiPolygon", "coordinates": slim}})
    REGIONS_OUT.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")),
                           encoding="utf-8")
    print(f"wrote {REGIONS_OUT} ({REGIONS_OUT.stat().st_size // 1024} KB, {len(feats)} regions)")


def main() -> None:
    if "--regions" in sys.argv[1:]:
        return regions()
    url = FILES["countries"][1]
    data = httpx.get(url, timeout=120, follow_redirects=True).json()
    feats = []
    for f in data["features"]:
        p = f["properties"]
        if p["ADM0_A3"] not in KEEP:
            continue
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        tol = 0.002 if p["ADM0_A3"] in {"SGP", "LSO", "CHE"} else 0.05
        slim = []
        for poly in polys:
            rings = [[list(map(float, pt)) for pt in simplify(ring, tol)] for ring in poly]
            if len(rings[0]) >= 4:
                slim.append(rings)
        feats.append({"type": "Feature", "properties": {k: p.get(k) for k in PROPS},
                      "geometry": {"type": "MultiPolygon", "coordinates": slim}})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(feats)} countries)")


if __name__ == "__main__":
    main()
