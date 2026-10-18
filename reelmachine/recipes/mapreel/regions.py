# reelmachine/recipes/mapreel/regions.py
"""Admin-1 regions (states, prefectures, provinces) from Natural Earth, found inside their country."""

from __future__ import annotations

import difflib
import json
import os
import re
import tempfile
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from ...config import Settings
from ...core.errors import InvalidInput
from . import geodata
from .places import Place, _rings, norm

_CODE = re.compile(r"^[A-Z]{2}-[A-Z0-9]{1,3}$")
#: beside the admin-1 file in the geo cache: country alpha-3 -> how many regions it has
INDEX_NAME = "regions-index.json"
_NAME_FIELDS = ("name", "name_en", "woe_name", "gn_name")


class RegionAtlas:
    def __init__(self, places: dict[str, Place], parents: dict[str, str], keys: dict[str, dict[str, set[str]]]) -> None:
        self.places = places  # ISO 3166-2 code -> region
        self.parents = parents  # region code -> ISO alpha-3 of its country
        self.keys = keys  # country -> normalised name -> region codes

    @staticmethod
    def load(path: Path) -> "RegionAtlas":
        path = Path(path)
        return _load(str(path), path.stat().st_mtime_ns)

    def in_country(self, country: str) -> list[Place]:
        country = (country or "").strip().upper()
        return sorted((p for c, p in self.places.items() if self.parents[c] == country), key=lambda p: (p.name, p.code))

    def resolve(self, query: str, country: str) -> Place:
        country = (country or "").strip().upper()
        raw = (query or "").strip()
        code = raw.upper()
        if code in self.places:
            if self.parents[code] == country:
                return self.places[code]
            raise InvalidInput(f"{code} is not in {country}", hint=f"{code} is in {self.parents[code]}",
                               details={"query": query, "country": country})
        codes = sorted(self.keys.get(country, {}).get(norm(raw), set()))
        if len(codes) == 1:
            return self.places[codes[0]]
        if len(codes) > 1:
            names = ", ".join(f"{c} ({self.places[c].name})" for c in codes)
            raise InvalidInput(f"{query!r} could mean more than one region of {country}", hint=f"use one of: {names}",
                               details={"query": query, "candidates": codes})
        names = sorted({p.name for p in self.in_country(country)})
        close = difflib.get_close_matches(raw.title(), names, n=5, cutoff=0.6)
        if not names:
            hint = f"no regions are known for {country}; check the country code (find_places)"
        else:
            hint = f"did you mean {', '.join(close)}?" if close else f"use a code like {self.in_country(country)[0].code} (find_places with country)"
        raise InvalidInput(f"no region called {query!r} in {country}", hint=hint,
                           details={"query": query, "country": country, "suggestions": close})

    def search(self, query: str, country: str, limit: int = 8) -> list[dict[str, Any]]:
        country = (country or "").strip().upper()
        key, upper = norm(query), (query or "").strip().upper()
        mine = self.in_country(country)
        if not key:
            ranked = mine
        else:
            scored: dict[str, float] = {}
            for name, codes in self.keys.get(country, {}).items():
                if name == key:
                    score = 3.0
                elif name.startswith(key):
                    score = 2.0
                elif key in name:
                    score = 1.0
                else:
                    ratio = difflib.SequenceMatcher(None, key, name).ratio()
                    score = ratio if ratio >= 0.75 else 0.0
                for code in codes:
                    if score:
                        scored[code] = max(scored.get(code, 0.0), score)
            if upper in self.places and self.parents[upper] == country:
                scored[upper] = 4.0
            ranked = sorted((self.places[c] for c in scored), key=lambda p: (-scored[p.code], p.name))
        exact = self.keys.get(country, {}).get(key, set())
        return [{"code": p.code, "name": p.name, "level": "region", "parent": country,
                 "ambiguous": len(exact) > 1 and p.code in exact} for p in ranked[:limit]]

    def search_all(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """A code-shaped query ('US-TX', 'JP-') with no country: regions whose code starts with it."""
        upper = (query or "").strip().upper()
        found = sorted((p for c, p in self.places.items() if c.startswith(upper)), key=lambda p: p.code)
        return [{"code": p.code, "name": p.name, "level": "region", "parent": self.parents[p.code], "ambiguous": False}
                for p in found[:limit]]

    def countries(self) -> set[str]:
        return set(self.parents.values())


@lru_cache(maxsize=2)
def _load(path: str, _mtime: int) -> RegionAtlas:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    places: dict[str, Place] = {}
    parents: dict[str, str] = {}
    keys: dict[str, dict[str, set[str]]] = {}
    for feature in data["features"]:
        props, geometry = feature["properties"], feature.get("geometry")
        code, parent = props.get("iso_3166_2") or "", props.get("adm0_a3") or ""
        if not geometry or not _CODE.match(code) or not parent:
            continue  # '~' codes are Natural Earth's own placeholders for regions with no ISO code
        polygons = _rings(geometry)
        if code in places:  # one region cut in several features: keep them all as one shape
            old = places[code]
            places[code] = Place(code, old.name, old.polygons + polygons, old.label, "region")
            continue
        label = (float(props.get("longitude") or 0.0), float(props.get("latitude") or 0.0))
        places[code] = Place(code, props.get("name_en") or props.get("name") or code, polygons, label, "region")
        parents[code] = parent
        table = keys.setdefault(parent, {})
        names = [props.get(f) for f in _NAME_FIELDS] + (props.get("name_alt") or "").split("|")
        for name in names:
            if name and norm(name):
                table.setdefault(norm(name), set()).add(code)
    return RegionAtlas(places, parents, keys)


def region_atlas_for(settings: Settings) -> RegionAtlas:
    return RegionAtlas.load(geodata.ensure(geodata.geo_cache(settings), "regions"))


def region_counts(root: Path) -> dict[str, int] | None:
    """How many regions each country has (alpha-3 -> count), or None when the admin-1 file is not cached (or
    cannot be read). The first call parses the 40 MB file once and writes INDEX_NAME beside it; later calls read
    that index alone. An index older than the file it counts, or damaged, is counted again."""
    root = Path(root)
    source, index = root / geodata.FILES["regions"][0], root / INDEX_NAME
    try:
        source_mtime = source.stat().st_mtime_ns
    except OSError:
        return None
    try:
        if index.stat().st_mtime_ns >= source_mtime:
            data = json.loads(index.read_text(encoding="utf-8"))
            if isinstance(data, dict) and all(isinstance(k, str) and type(v) is int for k, v in data.items()):
                return data
    except (OSError, ValueError):
        pass
    try:
        counts = dict(sorted(Counter(RegionAtlas.load(source).parents.values()).items()))
    except (OSError, ValueError, KeyError, TypeError):
        return None  # a damaged admin-1 file: no flags, and nothing fetched for a plain country search
    fd, name = tempfile.mkstemp(dir=root, prefix=INDEX_NAME + ".", suffix=".partial")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(counts, handle, separators=(",", ":"))
        os.replace(name, index)
    except OSError:
        Path(name).unlink(missing_ok=True)
    return counts
